"""Internal `ml` service: holds the embedding model once for the API and the worker, and
extracts text from documents locally (PDF text layer, OCR).

Reachable only on the internal Docker network (ADR 0006, ADR 0008). It receives text or
files, returns vectors or text, and stores nothing. The model is downloaded once
(`lifeos-ml download`); the service itself then runs offline.
"""

import argparse
import asyncio
import base64
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lifeos.config import Settings, get_settings
from lifeos.logging import configure_logging, get_logger
from lifeos.modules.documents.sniff import sniff
from lifeos.modules.retrieval.embedders import Embedder, EmbeddingError, LocalEmbedder

log = get_logger("lifeos.ml")
MAX_TEXTS = 128
MAX_CHARS = 8000
# Pages are rendered and OCRed one at a time; a document is never truncated silently.
MAX_PAGES_LIMIT = 500
# A request waits at most this long for the extraction slot, then gets 503 (retried later
# by the API). With the extraction budget, it stays under the API's timeout.
QUEUE_WAIT_S = 90.0


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS)
    kind: Literal["query", "passage"]


def create_ml_app(settings: Settings | None = None, embedder: Embedder | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    model = embedder or LocalEmbedder(settings)

    async def warm_up() -> None:
        try:
            await model.embed(["échauffement"], "query")
            log.info("embedding_model_ready", model=model.model)
        except EmbeddingError as exc:
            log.error("embedding_model_failed", model=model.model, error=str(exc)[:300])

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Embeddings disabled: stay idle, the model is only loaded on a real request.
        task = (
            asyncio.create_task(warm_up()) if settings.embeddings_provider != "disabled" else None
        )
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(title="LifeOS ML", lifespan=lifespan, docs_url=None, openapi_url=None)
    # One extraction at a time: OCR uses the CPU fully and PDFium is not thread-safe.
    extraction_slot = asyncio.Semaphore(1)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "model": model.model}

    @app.post("/embed", response_model=None)
    async def embed(body: EmbedRequest) -> dict[str, Any] | JSONResponse:
        texts = [t[:MAX_CHARS] for t in body.texts]
        try:
            vectors = await model.embed(texts, body.kind)
        except EmbeddingError as exc:
            log.warning("embedding_failed", error=str(exc)[:300])
            return JSONResponse({"error": "embedding_unavailable"}, status_code=503)
        return {"model": model.model, "vectors": vectors}

    @app.post("/extract", response_model=None)
    async def extract(
        file: UploadFile,
        mime: Annotated[str, Form(max_length=40)],
        purpose: Annotated[str, Form(max_length=20)] = "document",
        max_pages: Annotated[int, Form(ge=1, le=MAX_PAGES_LIMIT)] = 30,
    ) -> dict[str, Any] | JSONResponse:
        data = await file.read(settings.documents_max_bytes + 1)
        if len(data) > settings.documents_max_bytes:
            return JSONResponse({"code": "too_large"}, status_code=413)
        detected = sniff(data)
        # The declared type must be the one found by signature: no decoder is chosen by
        # the caller alone.
        if detected is None or detected.mime != mime or detected.kind not in ("image", "pdf"):
            return JSONResponse({"code": "unsupported_type"}, status_code=415)
        try:
            from lifeos.ml import extract as extraction  # the `ml` extra
        except ImportError:
            log.error("extraction_dependencies_missing")
            return JSONResponse({"code": "extraction_unavailable"}, status_code=503)
        try:
            await asyncio.wait_for(extraction_slot.acquire(), QUEUE_WAIT_S)
        except TimeoutError:
            return JSONResponse({"code": "busy"}, status_code=503)
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(
                extraction.run_isolated,
                data,
                mime,
                purpose=purpose,
                max_pages=max_pages,
                memory_mb=settings.extraction_memory_mb,
            )
        except extraction.ExtractFailure as exc:
            log.info(
                "extraction_refused",
                mime=mime,
                code=exc.code,
                peak_mb=exc.peak_mb,
                signal=exc.signal,
            )
            return JSONResponse({"code": exc.code}, status_code=exc.status)
        finally:
            extraction_slot.release()
        # Never the content: only its shape.
        log.info(
            "extraction_done",
            mime=mime,
            source=result.source,
            pages=result.pages,
            chars=len(result.text),
            sanitized=result.sanitized is not None,
            seconds=round(time.monotonic() - started, 2),
            # Memory of the isolated extraction: sizes LIFEOS_EXTRACTION_MEMORY_MB.
            peak_mb=result.peak_mb,
        )
        return {
            "text": result.text,
            "source": result.source,
            "pages": result.pages,
            "confidence": result.confidence,
            "sanitized_b64": (
                base64.b64encode(result.sanitized).decode() if result.sanitized else None
            ),
            "sanitized_mime": result.sanitized_mime,
        }

    return app


async def _download(settings: Settings) -> int:
    embedder = LocalEmbedder(settings)
    try:
        await embedder.load()
        [vector] = await embedder.embed(["vérification du modèle"], "query")
    except EmbeddingError as exc:
        print(f"échec : {exc}", file=sys.stderr)
        return 1
    print(f"modèle {embedder.model} prêt ({len(vector)} dimensions)")
    return 0


def _selftest() -> int:
    """Reads a generated French receipt: checks the OCR install (Tesseract, French data)."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    from lifeos.ml.extract import ExtractFailure, extract

    lines = ["SUPERMARCHE TEST", "10/10/2026", "TOTAL TTC 12,34"]
    font = ImageFont.load_default(size=40)
    image = Image.new("RGB", (900, 260), "white")
    for index, line in enumerate(lines):
        ImageDraw.Draw(image).text((40, 30 + index * 70), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    try:
        result = extract(buffer.getvalue(), "image/png", purpose="receipt", max_pages=1)
    except ExtractFailure as exc:
        print(f"échec de l'OCR : {exc.code}", file=sys.stderr)
        return 1
    if "12,34" not in result.text:
        print(f"OCR incorrect : {result.text!r}", file=sys.stderr)
        return 1
    print(f"OCR prêt (confiance {result.confidence:.2f})")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lifeos-ml")
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "download", "selftest"],
        help="serve (default); download the embedding model into the cache; selftest the OCR",
    )
    args = parser.parse_args(argv)
    if args.command == "download":
        settings = get_settings()
        configure_logging(settings)
        raise SystemExit(asyncio.run(_download(settings)))
    if args.command == "selftest":
        raise SystemExit(_selftest())
    uvicorn.run(create_ml_app(), host="0.0.0.0", port=8100, log_config=None)  # noqa: S104
