"""Internal `stt` service: local transcription of dictations (UC01, ADR 0009).

Reachable only on the internal Docker network, with no route to the outside world. It
receives an audio file, returns its French transcript, and stores nothing; audio and text
are never logged. The model is downloaded once (`lifeos-stt download`); the service then
runs offline.
"""

import argparse
import asyncio
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import JSONResponse

from lifeos.config import Settings, get_settings
from lifeos.logging import configure_logging, get_logger
from lifeos.modules.documents.sniff import sniff
from lifeos.stt.engine import Engine, SttFailure

log = get_logger("lifeos.stt")
# A request waits at most this long for the transcription slot, then gets 503 (the API
# transcribes one dictation at a time anyway).
QUEUE_WAIT_S = 60.0
IDLE_CHECK_S = 60.0


def create_stt_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    stt = engine or Engine(settings)
    slot = asyncio.Semaphore(1)

    async def unload_when_idle() -> None:
        while True:
            await asyncio.sleep(IDLE_CHECK_S)
            if not slot.locked():
                await asyncio.to_thread(stt.unload_if_idle)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(unload_when_idle())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="LifeOS STT", lifespan=lifespan, docs_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "model": stt.model_name, "loaded": stt.loaded, "device": stt.device}

    @app.post("/transcribe", response_model=None)
    async def transcribe(
        file: UploadFile,
        mime: Annotated[str, Form(max_length=40)],
        max_seconds: Annotated[int, Form(ge=1, le=4 * 3600)] = settings.voice_max_seconds,
    ) -> dict[str, Any] | JSONResponse:
        data = await file.read(settings.documents_max_bytes + 1)
        if len(data) > settings.documents_max_bytes:
            return JSONResponse({"code": "too_large"}, status_code=413)
        detected = sniff(data)
        if detected is None or detected.kind != "audio" or detected.mime != mime:
            return JSONResponse({"code": "unsupported_type"}, status_code=415)
        try:
            await asyncio.wait_for(slot.acquire(), QUEUE_WAIT_S)
        except TimeoutError:
            return JSONResponse({"code": "busy"}, status_code=503)
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(
                stt.transcribe, data, mime=detected.mime, max_seconds=max_seconds
            )
        except SttFailure as exc:
            log.info("transcription_refused", mime=mime, code=exc.code)
            return JSONResponse({"code": exc.code}, status_code=exc.status)
        finally:
            slot.release()
        elapsed = time.monotonic() - started
        # Never the content: only its shape.
        log.info(
            "transcription_done",
            mime=mime,
            device=stt.device,
            audio_seconds=round(result.duration, 1),
            speech_seconds=round(result.speech, 1),
            chars=len(result.text),
            seconds=round(elapsed, 2),
            # Memory of the isolated decoding (the model's is fixed): sizes the service.
            decode_peak_mb=result.decode_peak_mb,
        )
        return {
            "text": result.text,
            "language": "fr",
            "duration": round(result.duration, 2),
            "speech": round(result.speech, 2),
            "model": stt.model_name,
            "device": stt.device,
        }

    return app


def _download(settings: Settings) -> int:
    from faster_whisper import download_model  # the `stt` extra

    try:
        path = download_model(settings.stt_model, cache_dir=settings.stt_cache_dir)
    except Exception as exc:  # network, disk, unknown model name
        print(f"échec : {exc}", file=sys.stderr)
        return 1
    print(f"modèle {settings.stt_model} prêt ({path})")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lifeos-stt")
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "download"],
        help="serve (default) or download the speech model into the cache, then exit",
    )
    args = parser.parse_args(argv)
    if args.command == "download":
        settings = get_settings()
        configure_logging(settings)
        raise SystemExit(_download(settings))
    uvicorn.run(create_stt_app(), host="0.0.0.0", port=8200, log_config=None)  # noqa: S104
