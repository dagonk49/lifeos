"""Internal `stt` service: local transcription of dictations (UC01, ADR 0009).

Reachable only on the internal Docker network, with no route to the outside world. It
receives an audio file, returns its French transcript, and stores nothing; audio and text
are never logged. The model is downloaded once (`lifeos-stt download`); the service then
runs offline.
"""

import argparse
import asyncio
import resource
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Histogram, generate_latest

from lifeos.config import Settings, get_settings
from lifeos.logging import configure_logging, get_logger
from lifeos.modules.documents.sniff import sniff
from lifeos.stt.engine import Engine, SttFailure, Transcript

log = get_logger("lifeos.stt")
# A request waits at most this long for the transcription slot, then gets 503 (the API
# transcribes one dictation at a time anyway).
QUEUE_WAIT_S = 60.0
IDLE_CHECK_S = 60.0
# How often a running transcription checks that its caller is still there.
DISCONNECT_POLL_S = 1.0
# Job identifiers given by the API (the dictation's id): short, plain.
JOB_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
# The container's peak memory (cgroup v2), when the service can read it.
CGROUP_PEAK = Path("/sys/fs/cgroup/memory.peak")


def peak_memory() -> dict[str, int | None]:
    """Peak resident memory of this process and of its container (MiB): what sizes the
    service's `mem_limit` (docs/exploitation.md §7)."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024  # kB on Linux
    try:
        cgroup: int | None = int(CGROUP_PEAK.read_text().strip()) // (1024 * 1024)
    except (OSError, ValueError):
        cgroup = None
    return {"peak_rss_mb": rss, "cgroup_peak_mb": cgroup}


def timings(result: Transcript, total: float) -> dict[str, float | None]:
    return {
        "decode": round(result.decode_s, 3),
        "load": round(result.load_s, 3) if result.load_s is not None else None,
        "transcribe": round(result.transcribe_s, 3),
        "total": round(total, 3),
    }


def real_time_factor(result: Transcript) -> float | None:
    """Seconds of model work per second of audio (loading excluded): < 1 is faster than
    real time."""
    if result.duration <= 0 or result.transcribe_s <= 0:
        return None
    return round(result.transcribe_s / result.duration, 3)


class _Job:
    """The transcription in progress (one at a time): what `GET /progress` reports and
    what `POST /cancel` stops. No content, only the shape."""

    def __init__(self, job: str | None) -> None:
        self.job = job
        self.progress = 0.0
        self.audio_seconds: float | None = None
        self.started = time.monotonic()
        self.cancel = threading.Event()

    def report(self, value: float) -> None:  # called from the transcription thread
        self.progress = max(self.progress, value)

    def decoded(self, seconds: float) -> None:  # idem
        self.audio_seconds = seconds


def create_stt_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    stt = engine or Engine(settings)
    slot = asyncio.Semaphore(1)
    current: dict[str, _Job] = {}
    # Own registry: the service's figures only.
    registry = CollectorRegistry()
    rtf_histogram = Histogram(
        "lifeos_stt_rtf",
        "Seconds of model work per second of audio",
        ["device"],
        buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0),
        registry=registry,
    )
    seconds_histogram = Histogram(
        "lifeos_stt_seconds",
        "Time to answer a transcription (decoding, loading and model work)",
        ["device"],
        buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800),
        registry=registry,
    )

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

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/progress")
    async def progress() -> dict[str, Any]:
        """The transcription in progress, if any (internal network only, like the rest)."""
        running = current.get("job")
        if running is None:
            return {
                "busy": False,
                "job": None,
                "progress": None,
                "audio_seconds": None,
                "elapsed": None,
            }
        audio = running.audio_seconds
        return {
            "busy": True,
            "job": running.job,
            "progress": round(running.progress, 3),
            "audio_seconds": round(audio, 1) if audio is not None else None,
            "elapsed": round(time.monotonic() - running.started, 1),
        }

    @app.post("/cancel")
    async def cancel(job: Annotated[str, Form(pattern=JOB_PATTERN)]) -> dict[str, bool]:
        """Stops the transcription of `job` (the dictation was deleted): the slot is free
        again within a segment. Unknown or finished job: nothing to do."""
        running = current.get("job")
        if running is None or running.job != job:
            return {"cancelled": False}
        running.cancel.set()
        return {"cancelled": True}

    async def watch(request: Request, running: _Job) -> None:
        # The API closed the connection (worker cancelled, dictation deleted): stop, so the
        # single slot does not stay busy for the length of the dictation.
        while not running.cancel.is_set():
            if await request.is_disconnected():
                running.cancel.set()
                return
            await asyncio.sleep(DISCONNECT_POLL_S)

    @app.post("/transcribe", response_model=None)
    async def transcribe(
        request: Request,
        file: UploadFile,
        mime: Annotated[str, Form(max_length=40)],
        max_seconds: Annotated[int, Form(ge=1, le=4 * 3600)] = settings.voice_max_seconds,
        job: Annotated[str | None, Form(pattern=JOB_PATTERN)] = None,
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
        running = _Job(job)
        current["job"] = running
        watcher = asyncio.create_task(watch(request, running))
        try:
            result = await asyncio.to_thread(
                stt.transcribe,
                data,
                mime=detected.mime,
                max_seconds=max_seconds,
                on_progress=running.report,
                cancel=running.cancel,
                on_decoded=running.decoded,
            )
        except SttFailure as exc:
            log.info("transcription_refused", mime=mime, code=exc.code)
            return JSONResponse({"code": exc.code}, status_code=exc.status)
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            current.pop("job", None)
            slot.release()
        elapsed = time.monotonic() - started
        rtf = real_time_factor(result)
        device = result.device or "none"
        seconds_histogram.labels(device=device).observe(elapsed)
        if rtf is not None:
            rtf_histogram.labels(device=device).observe(rtf)
        spent = timings(result, elapsed)
        # Never the content: only its shape.
        log.info(
            "transcription_done",
            mime=mime,
            device=result.device,
            compute_type=result.compute_type,
            audio_seconds=round(result.duration, 1),
            speech_seconds=round(result.speech, 1),
            chars=len(result.text),
            seconds=round(elapsed, 2),
            decode_seconds=spent["decode"],
            load_seconds=spent["load"],
            transcribe_seconds=spent["transcribe"],
            rtf=rtf,
            # Memory of the isolated decoding (the model's is fixed): sizes the service.
            decode_peak_mb=result.decode_peak_mb,
            **peak_memory(),
        )
        return {
            "text": result.text,
            "language": "fr",
            "duration": round(result.duration, 2),
            "speech": round(result.speech, 2),
            "model": stt.model_name,
            "device": result.device or stt.device,
            "compute_type": result.compute_type,
            "timings": spent,
            "rtf": rtf,
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
        choices=["serve", "download", "bench"],
        help=(
            "serve (default); download the speech model into the cache, then exit; "
            "bench <audio>: measure a transcription (lifeos-stt bench --help)"
        ),
    )
    parser.add_argument("options", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.command == "bench":
        from lifeos.stt import bench

        raise SystemExit(bench.main(args.options))
    if args.command == "download":
        settings = get_settings()
        configure_logging(settings)
        raise SystemExit(_download(settings))
    uvicorn.run(create_stt_app(), host="0.0.0.0", port=8200, log_config=None)  # noqa: S104
