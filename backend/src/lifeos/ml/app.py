"""Internal `ml` service: holds the embedding model once for the API and the worker.

Reachable only on the internal Docker network (ADR 0006). It receives text, returns
vectors, and stores nothing. The model is downloaded once (`lifeos-ml download`); the
service itself then runs offline.
"""

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lifeos.config import Settings, get_settings
from lifeos.logging import configure_logging, get_logger
from lifeos.modules.retrieval.embedders import Embedder, EmbeddingError, LocalEmbedder

log = get_logger("lifeos.ml")
MAX_TEXTS = 128
MAX_CHARS = 8000


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
        task = asyncio.create_task(warm_up())
        yield
        task.cancel()

    app = FastAPI(title="LifeOS ML", lifespan=lifespan, docs_url=None, openapi_url=None)

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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lifeos-ml")
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "download"],
        help="serve (default) or download the embedding model into the cache, then exit",
    )
    args = parser.parse_args(argv)
    if args.command == "download":
        settings = get_settings()
        configure_logging(settings)
        raise SystemExit(asyncio.run(_download(settings)))
    uvicorn.run(create_ml_app(), host="0.0.0.0", port=8100, log_config=None)  # noqa: S104
