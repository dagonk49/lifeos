"""Local speech-to-text with faster-whisper (UC01, ADR 0009).

French only, on the VM: Whisper large-v3-turbo quantised to int8 runs on the CPU (about
half real time on 4 cores) or on a Pascal GPU (int8 weights, float32 compute: these cards
have no fast float16). The model is loaded on the first dictation and unloaded after a
while without any, to give its memory back.

Whisper invents text on silence or noise, typically the credits of the subtitled videos it
was trained on: voice activity detection skips silences, and the known French phantom
sentences are removed.
"""

import io
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from lifeos.config import Settings
from lifeos.logging import get_logger

log = get_logger("lifeos.stt")

SAMPLE_RATE = 16_000
LANGUAGE = "fr"
# Below this, there is nothing to transcribe (a tap on the record button).
MIN_SECONDS = 0.3
# A recording stopped automatically at the maximum length overshoots it by a fraction of a
# second (more in a background tab): accepted, never a lost dictation.
TOLERANCE_S = 5.0
VAD_PARAMETERS = {"min_silence_duration_ms": 700, "speech_pad_ms": 300}
# Normalised (lowercase, no accents) fragments of sentences Whisper hallucinates in French.
_PHANTOMS = re.compile(
    r"amara\.org|^sous.titr|radio.canada|merci d.avoir regarde|abonnez.vous"
    r"|thank you for watching|^\W*$"
)


class SttFailure(Exception):  # noqa: N818  (mirrors UnreadableDocument on the API side)
    """`status` 422: this audio cannot be transcribed (not retried); 503: retry later."""

    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Transcript:
    text: str
    duration: float  # seconds of audio
    speech: float  # seconds of detected speech


def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def clean_segments(texts: list[str]) -> str:
    """Drops phantom sentences and repetition loops, joins the rest."""
    kept: list[str] = []
    for raw in texts:
        text = " ".join(raw.split())
        if not text or _PHANTOMS.search(_normalise(text)):
            continue
        # A decoding loop repeats the same segment: kept once.
        if kept and _normalise(kept[-1]) == _normalise(text):
            continue
        kept.append(text)
    return " ".join(kept)


def decode(data: bytes, *, max_seconds: float) -> Any:
    """The audio as 16 kHz mono float32 samples (PyAV, i.e. FFmpeg, in this sandbox).

    Decoded progressively and refused as soon as it exceeds `max_seconds` (plus the
    tolerance): a small file can hold hours of (silent) audio, which must never be
    expanded in memory first.
    """
    import gc

    import av  # the `stt` extra
    import numpy as np
    from faster_whisper.audio import (
        _group_frames,
        _ignore_invalid_frames,
        _resample_frames,
    )

    allowed = max_seconds + TOLERANCE_S
    limit = int(allowed * SAMPLE_RATE)
    chunks: list[Any] = []
    count = 0
    try:
        with av.open(io.BytesIO(data), mode="r", metadata_errors="ignore") as container:
            if container.duration is not None and container.duration / av.time_base > allowed:
                raise SttFailure("too_long")
            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=SAMPLE_RATE
            )
            frames = _ignore_invalid_frames(container.decode(audio=0))
            for frame in _resample_frames(_group_frames(frames, 500_000), resampler):
                samples = frame.to_ndarray().reshape(-1)
                count += samples.shape[0]
                if count > limit:
                    raise SttFailure("too_long")
                chunks.append(samples)
            del resampler
    except SttFailure:
        raise
    except Exception as exc:  # av.error.*: corrupt, truncated or not audio at all
        raise SttFailure("unreadable") from exc
    finally:
        gc.collect()  # PyAV resampler objects are only freed by the collector
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(chunks).astype(np.float32)
    audio /= 32768.0
    return audio


class Engine:
    """Holds the Whisper model; thread-safe, one transcription at a time."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any = None
        self.device: str | None = None
        self._lock = threading.Lock()
        self._last_used = 0.0

    @property
    def model_name(self) -> str:
        return self._settings.stt_model

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _candidates(self) -> list[tuple[str, str]]:
        cpu = ("cpu", "int8")
        # Pascal GPUs (GTX 10xx) have no fast float16: int8 weights, float32 compute.
        cuda = ("cuda", "int8_float32")
        if self._settings.stt_device == "cpu":
            return [cpu]
        if self._settings.stt_device == "cuda":
            return [cuda, cpu]
        import ctranslate2  # the `stt` extra

        try:
            gpus = ctranslate2.get_cuda_device_count()
        except Exception:  # no CUDA runtime at all
            gpus = 0
        return [cuda, cpu] if gpus > 0 else [cpu]

    def _load(self) -> Any:
        from faster_whisper import WhisperModel  # the `stt` extra

        errors: list[str] = []
        for device, compute_type in self._candidates():
            try:
                model = WhisperModel(
                    self._settings.stt_model,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=self._settings.stt_threads,
                    download_root=self._settings.stt_cache_dir,
                    local_files_only=True,
                )
            except Exception as exc:  # missing model, CUDA driver mismatch, out of memory
                errors.append(f"{device}: {type(exc).__name__}: {str(exc)[:200]}")
                log.warning("stt_model_load_failed", device=device, error=str(exc)[:300])
                continue
            self.device = device
            log.info("stt_model_loaded", model=self._settings.stt_model, device=device)
            return model
        raise SttFailure("model_unavailable", status=503) from RuntimeError("; ".join(errors))

    def transcribe(self, data: bytes, *, max_seconds: float) -> Transcript:
        audio = decode(data, max_seconds=max_seconds)
        duration = len(audio) / SAMPLE_RATE
        if duration < MIN_SECONDS:
            return Transcript(text="", duration=duration, speech=0.0)
        with self._lock:
            if self._model is None:
                self._model = self._load()
            try:
                segments, info = self._model.transcribe(
                    audio,
                    language=LANGUAGE,
                    beam_size=self._settings.stt_beam_size,
                    vad_filter=True,
                    vad_parameters=VAD_PARAMETERS,
                    # Each segment decoded on its own: one hallucination cannot spread.
                    condition_on_previous_text=False,
                )
                texts = [segment.text for segment in segments]  # decoding happens here
            finally:
                self._last_used = time.monotonic()
        speech = float(getattr(info, "duration_after_vad", duration) or 0.0)
        return Transcript(text=clean_segments(texts), duration=duration, speech=speech)

    def unload_if_idle(self) -> bool:
        idle = self._settings.stt_idle_unload_s
        if self._model is None or time.monotonic() - self._last_used < idle:
            return False
        if not self._lock.acquire(blocking=False):  # transcribing right now
            return False
        try:
            self._model = None
            self.device = None
        finally:
            self._lock.release()
        log.info("stt_model_unloaded")
        return True
