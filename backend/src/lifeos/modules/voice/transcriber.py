"""Speech-to-text clients (UC01, ADR 0009). Transcription is local: dictations go to the
internal `stt` service, never to the cloud.

Contract of the stt service, `POST {stt_url}/transcribe` (multipart/form-data):

Request fields:
- `file`: the decrypted recording, with the detected media type as part type;
- `mime`: the type detected by signature (`sniff`): `audio/webm`, `audio/ogg`,
  `audio/mp4`, `audio/wav` or `audio/mpeg`;
- `max_seconds`: longer recordings are refused (422 `too_long`), never truncated;
- `job` (optional, `[A-Za-z0-9_-]{1,64}`): an identifier of the caller's choice (the
  dictation's id), for `GET /progress` and `POST /cancel`.

Responses:
- `200` JSON `{"text", "language", "duration", "speech", "model", "device",
  "compute_type", "timings", "rtf"}`: French transcript (empty if nothing was said), audio
  and speech durations in seconds, the device actually used (`cpu` or `cuda`: a CUDA ->
  CPU fallback shows here) and its `compute_type`; `timings`
  `{"decode", "load", "transcribe", "total"}` in seconds (`load` null when the model was
  already loaded); `rtf`: model seconds per second of audio (null for an empty recording).
  `compute_type`, `timings` and `rtf` are newer: absent from an older service, and only
  informative (a malformed value is ignored);
- `422` JSON `{"code": ...}`, `413` and `415`: not retried. Codes: `unreadable`
  (corrupt, no audio track, or the decoder failed on it), `too_long`,
  `unsupported_codec` (an audio codec outside the allowed list), `too_slow` (the decoder
  was killed at its time limit);
- `500` or a dropped connection: the service failed on this recording (retried a few
  times, then `transcription_crashed`). That includes JSON
  `{"code": "transcription_crashed"}`: the isolated decoder was ended by a signal (the
  kernel's OOM killer, or a crash), which says nothing final about the recording;
- `503` (`model_unavailable`, `busy`), any other error, timeout: retried later.
- `422` `cancelled`: the transcription was stopped by `POST /cancel`, or because the
  caller closed the connection (the API does both when a dictation is deleted while it is
  transcribed): the slot is free again within one segment (≈ 30 s of audio).

Progress, `GET {stt_url}/progress`: `{"busy": bool, "job": str | null, "progress": float in
[0, 1] | null, "audio_seconds": float | null, "elapsed": seconds | null}` for the
transcription in progress (one at a time; coarse steps, one per decoded segment;
`audio_seconds` once the recording is decoded). Cancel, `POST {stt_url}/cancel` with the
form field `job`: `{"cancelled": bool}` (false when that job is not the one running).

Recordings are decoded in a confined child process of the service (ADR 0011), with the
demuxer imposed by the detected type: a hostile file is refused before reaching the model.
The service stores nothing and never logs audio or text.
"""

import asyncio
import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from lifeos.config import Settings

# Worst case on the CPU: about real time for the longest dictation, plus queueing.
TIMEOUT_S = 30 * 60.0


# Devices the service may report (anything else is ignored).
DEVICES = ("cpu", "cuda")
# The progress request is a side channel: short, never in the way of the transcription.
PROGRESS_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class Transcription:
    text: str
    duration: float  # seconds of audio
    # Device actually used, model seconds per second of audio, seconds of service work:
    # measured by the service, for the record (`Document.analysis`) and estimates.
    device: str | None = None
    rtf: float | None = None
    elapsed: float | None = None


@dataclass(frozen=True)
class Progress:
    job: str
    progress: float  # share of the audio transcribed, in [0, 1]
    audio_seconds: float | None = None  # length of the recording, once decoded
    elapsed: float | None = None  # seconds since the service started it


class TranscriptionError(Exception):
    """Transcription is unavailable right now (service down, model missing): retry later."""


class TranscriptionCrashError(TranscriptionError):
    """The service failed while transcribing THIS recording (connection lost, hang,
    server error): retried, but the dictation fails after a few such failures in a row."""


class UntranscribableAudio(Exception):  # noqa: N818  (name used by the stt contract)
    """The recording cannot be transcribed (corrupt, too long): do not retry."""

    def __init__(self, code: str = "unreadable") -> None:
        super().__init__(code)
        self.code = code


class Transcriber(Protocol):
    name: str

    async def transcribe(
        self, data: bytes, mime: str, *, max_seconds: int, job: str | None = None
    ) -> Transcription: ...

    async def progress(self, job: str) -> Progress | None:
        """How far `job` is, or None (unknown, finished, service unreachable)."""
        ...

    async def cancel(self, job: str) -> None:
        """Best effort: asks the service to stop `job` (never raises)."""
        ...


FakeHandler = Callable[[bytes, str], Transcription]


class FakeTranscriber:
    """Deterministic transcriber (tests, offline development): returns `text`, or what
    `handler` returns (a handler may also raise TranscriptionError or UntranscribableAudio).

    `gate`: when given, a transcription waits until it is set (a long dictation), and
    `progress` walks through `steps` meanwhile; `cancelled` lists the jobs cancelled."""

    name = "fake"

    def __init__(
        self,
        text: str = "",
        *,
        handler: FakeHandler | None = None,
        gate: asyncio.Event | None = None,
        steps: tuple[float, ...] = (),
        audio_seconds: float | None = None,
        device: str | None = "cpu",
        elapsed: float | None = None,
    ) -> None:
        self.text = text
        self.handler = handler
        self.gate = gate
        self.steps = list(steps)
        self.audio_seconds = audio_seconds
        self.device = device
        self.elapsed = elapsed
        self.calls = 0
        self.running: str | None = None
        self.cancelled: list[str] = []
        self.started = asyncio.Event()

    async def transcribe(
        self, data: bytes, mime: str, *, max_seconds: int, job: str | None = None
    ) -> Transcription:
        self.calls += 1
        self.running = job
        self.started.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
        finally:
            self.running = None
        if self.handler is not None:
            return self.handler(data, mime)
        return Transcription(text=self.text, duration=1.0, device=self.device, elapsed=self.elapsed)

    async def progress(self, job: str) -> Progress | None:
        if self.running != job:
            return None
        value = self.steps.pop(0) if len(self.steps) > 1 else (self.steps or [0.0])[0]
        return Progress(job=job, progress=value, audio_seconds=self.audio_seconds)

    async def cancel(self, job: str) -> None:
        self.cancelled.append(job)


def parse_response(payload: Any) -> Transcription:
    """Validates the stt service's answer: nothing it returns is trusted blindly."""
    if not isinstance(payload, dict):
        raise TranscriptionError("stt service returned a malformed answer")
    text, duration = payload.get("text"), payload.get("duration")
    if not isinstance(text, str):
        raise TranscriptionError("stt service returned a malformed answer")
    if not isinstance(duration, int | float) or isinstance(duration, bool) or duration < 0:
        raise TranscriptionError("stt service returned a malformed duration")
    device = payload.get("device")
    timings = payload.get("timings")
    return Transcription(
        text=text,
        duration=float(duration),
        # Informative fields: kept only when well formed, never a reason to fail.
        device=device if device in DEVICES else None,
        rtf=_seconds(payload.get("rtf")),
        elapsed=_seconds(timings.get("total")) if isinstance(timings, dict) else None,
    )


def _seconds(value: Any) -> float | None:
    """A finite, non-negative number of seconds (or ratio), else None."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 24 * 3600:
        return None
    return round(number, 3)


def parse_progress(payload: Any, job: str) -> Progress | None:
    """The service's `GET /progress` answer for `job`, or None."""
    if not isinstance(payload, dict) or payload.get("job") != job:
        return None
    value = _seconds(payload.get("progress"))
    if value is None:
        return None
    return Progress(
        job=job,
        progress=min(1.0, value),
        audio_seconds=_seconds(payload.get("audio_seconds")),
        elapsed=_seconds(payload.get("elapsed")),
    )


def _error_code(response: httpx.Response) -> str:
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        code = None
    if isinstance(code, str) and code.isascii() and 0 < len(code) <= 40:
        return code
    return "unreadable"


class RemoteTranscriber:
    name = "remote"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._base = settings.stt_url.rstrip("/")
        self._url = self._base + "/transcribe"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=3.0))

    async def transcribe(
        self, data: bytes, mime: str, *, max_seconds: int, job: str | None = None
    ) -> Transcription:
        fields = {"mime": mime, "max_seconds": str(max_seconds)}
        if job is not None:
            fields["job"] = job
        try:
            response = await self._client.post(
                self._url,
                files={"file": ("dictation", data, mime)},
                data=fields,
                timeout=TIMEOUT_S,
            )
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout) as exc:
            # The request reached the service, which died or hung on this recording.
            raise TranscriptionCrashError(f"stt service failed: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"stt service unavailable: {type(exc).__name__}") from exc
        if response.status_code == 422:
            raise UntranscribableAudio(_error_code(response))
        if response.status_code == 413:
            raise UntranscribableAudio("too_large")
        if response.status_code == 415:
            raise UntranscribableAudio("unsupported_type")
        if response.status_code == 500:
            raise TranscriptionCrashError("stt service failed on this recording")
        if response.status_code != 200:
            raise TranscriptionError(f"stt service answered {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise TranscriptionError("stt service returned invalid JSON") from exc
        return parse_response(payload)

    async def progress(self, job: str) -> Progress | None:
        try:
            response = await self._client.get(self._base + "/progress", timeout=PROGRESS_TIMEOUT_S)
            payload = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        return parse_progress(payload, job)

    async def cancel(self, job: str) -> None:
        # Closing the transcription's connection stops it too (the service watches it):
        # this makes it explicit, and immediate.
        with suppress(httpx.HTTPError):
            await self._client.post(
                self._base + "/cancel", data={"job": job}, timeout=PROGRESS_TIMEOUT_S
            )


def build_transcriber(settings: Settings) -> Transcriber | None:
    if settings.stt_provider == "fake":
        return FakeTranscriber("Dictée de test.")
    if settings.stt_provider == "remote":
        return RemoteTranscriber(settings)
    return None
