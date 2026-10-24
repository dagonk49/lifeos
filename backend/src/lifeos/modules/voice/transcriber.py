"""Speech-to-text clients (UC01, ADR 0009). Transcription is local: dictations go to the
internal `stt` service, never to the cloud.

Contract of the stt service, `POST {stt_url}/transcribe` (multipart/form-data):

Request fields:
- `file`: the decrypted recording, with the detected media type as part type;
- `mime`: the type detected by signature (`sniff`): `audio/webm`, `audio/ogg`,
  `audio/mp4`, `audio/wav` or `audio/mpeg`;
- `max_seconds`: longer recordings are refused (422 `too_long`), never truncated.

Responses:
- `200` JSON `{"text", "language", "duration", "speech", "model", "device"}`: French
  transcript (empty if nothing was said), audio and speech durations in seconds;
- `422` JSON `{"code": ...}`, `413` and `415`: not retried. Codes: `unreadable`
  (corrupt, no audio track, or the decoder failed on it), `too_long`,
  `unsupported_codec` (an audio codec outside the allowed list), `too_slow` (the decoder
  was killed at its time limit);
- `500` or a dropped connection: the service failed on this recording (retried a few
  times, then `transcription_crashed`). That includes JSON
  `{"code": "transcription_crashed"}`: the isolated decoder was ended by a signal (the
  kernel's OOM killer, or a crash), which says nothing final about the recording;
- `503` (`model_unavailable`, `busy`), any other error, timeout: retried later.

Recordings are decoded in a confined child process of the service (ADR 0011), with the
demuxer imposed by the detected type: a hostile file is refused before reaching the model.
The service stores nothing and never logs audio or text.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from lifeos.config import Settings

# Worst case on the CPU: about real time for the longest dictation, plus queueing.
TIMEOUT_S = 30 * 60.0


@dataclass(frozen=True)
class Transcription:
    text: str
    duration: float  # seconds of audio


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

    async def transcribe(self, data: bytes, mime: str, *, max_seconds: int) -> Transcription: ...


FakeHandler = Callable[[bytes, str], Transcription]


class FakeTranscriber:
    """Deterministic transcriber (tests, offline development): returns `text`, or what
    `handler` returns (a handler may also raise TranscriptionError or UntranscribableAudio)."""

    name = "fake"

    def __init__(self, text: str = "", *, handler: FakeHandler | None = None) -> None:
        self.text = text
        self.handler = handler
        self.calls = 0

    async def transcribe(self, data: bytes, mime: str, *, max_seconds: int) -> Transcription:
        self.calls += 1
        if self.handler is not None:
            return self.handler(data, mime)
        return Transcription(text=self.text, duration=1.0)


def parse_response(payload: Any) -> Transcription:
    """Validates the stt service's answer: nothing it returns is trusted blindly."""
    if not isinstance(payload, dict):
        raise TranscriptionError("stt service returned a malformed answer")
    text, duration = payload.get("text"), payload.get("duration")
    if not isinstance(text, str):
        raise TranscriptionError("stt service returned a malformed answer")
    if not isinstance(duration, int | float) or isinstance(duration, bool) or duration < 0:
        raise TranscriptionError("stt service returned a malformed duration")
    return Transcription(text=text, duration=float(duration))


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
        self._url = settings.stt_url.rstrip("/") + "/transcribe"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=3.0))

    async def transcribe(self, data: bytes, mime: str, *, max_seconds: int) -> Transcription:
        try:
            response = await self._client.post(
                self._url,
                files={"file": ("dictation", data, mime)},
                data={"mime": mime, "max_seconds": str(max_seconds)},
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


def build_transcriber(settings: Settings) -> Transcriber | None:
    if settings.stt_provider == "fake":
        return FakeTranscriber("Dictée de test.")
    if settings.stt_provider == "remote":
        return RemoteTranscriber(settings)
    return None
