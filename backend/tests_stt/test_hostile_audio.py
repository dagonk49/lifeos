"""Hostile or disguised recordings against the `stt` service (ADR 0011; CI job `ml-model`).

Videos passed off as audio, codecs outside the allowed list, lying headers, noise behind a
valid signature: each one, generated in code (`stt_samples`), must fail with a stable code
(or give a bounded and harmless result), quickly, in the confined decoding child
(`lifeos.isolation`), before the speech model is even needed, leaving no process behind.
The HTTP contract and the service staying up: `test_stt.test_hostile_recordings_*`.
"""

import multiprocessing
import signal
import time
from collections.abc import Callable
from functools import partial

import pytest

pytest.importorskip("av")
pytest.importorskip("faster_whisper")

import stt_samples as samples

from lifeos import isolation
from lifeos.config import Settings
from lifeos.modules.documents.sniff import sniff
from lifeos.stt import engine as stt_engine
from lifeos.stt.engine import Engine, SttFailure, decode_isolated

# A hostile recording is dealt with in seconds, far from the decoding time limit.
QUICK_S = 20.0
MIMES = {
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "webm": "audio/webm",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
}

# (builder, type found by signature, accepted outcomes: "ok" or a failure code)
CORPUS: dict[str, tuple[Callable[[], bytes], str, set[str]]] = {
    # Videos passed off as audio (generic MP4 brands are accepted: browsers use them).
    "mp4_video_only": (samples.mp4_video_only, "audio/mp4", {"unreadable"}),
    "webm_video_only": (samples.webm_video_only, "audio/webm", {"unreadable"}),
    "mp4_video_with_sound": (samples.mp4_video_with_sound, "audio/mp4", {"ok"}),
    # Codecs outside the allowed list: their decoder is never opened.
    "wav_adpcm": (samples.wav_adpcm, "audio/wav", {"unsupported_codec"}),
    "matroska_mp2": (samples.matroska_mp2, "audio/webm", {"unsupported_codec"}),
    "mp4_ac3": (samples.mp4_ac3, "audio/mp4", {"unsupported_codec"}),
    # Another format behind a valid signature: read by the demuxer of the signature only.
    "id3_then_ac3": (samples.id3_then_ac3, "audio/mpeg", {"unreadable"}),
    # Lying headers.
    "wav_lying_sizes": (samples.wav_lying_sizes, "audio/wav", {"ok"}),
    "wav_many_channels": (samples.wav_many_channels, "audio/wav", {"unreadable"}),
    "wav_slow_rate": (samples.wav_slow_rate, "audio/wav", {"too_long"}),
    # Damaged recordings, noise after a valid signature.
    "truncated_webm": (samples.truncated_webm, "audio/webm", {"unreadable"}),
    **{
        f"noise_after_{name}": (
            partial(samples.noise_after, signature),
            MIMES[name],
            {"unreadable"},
        )
        for name, signature in samples.SIGNATURES.items()
    },
}


def _outcome(data: bytes, mime: str) -> tuple[str, object]:
    try:
        decoded = decode_isolated(data, mime=mime, max_seconds=60)
    except SttFailure as failure:
        return failure.code, failure
    return "ok", decoded


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_hostile_recordings_fail_quickly_in_the_isolated_child(name: str) -> None:
    build, mime, accepted = CORPUS[name]
    data = build()
    detected = sniff(data)
    # Each sample passes the upload check: it reaches the decoder, as an attacker's would.
    assert detected is not None and detected.mime == mime
    started = time.monotonic()
    code, outcome = _outcome(data, mime)
    assert time.monotonic() - started < QUICK_S
    assert code in accepted, (code, accepted)
    if isinstance(outcome, SttFailure):
        assert outcome.status == 422  # permanent: never retried
    else:
        assert isinstance(outcome, stt_engine.Decoded)
        # Bounded: the one second of sound the file really holds, nothing more.
        assert len(outcome.samples) / stt_engine.SAMPLE_RATE == pytest.approx(1.0, abs=0.1)
        assert outcome.peak_mb > 0
    assert multiprocessing.active_children() == []


@pytest.mark.parametrize("declared", ["audio/webm", "audio/mp4", "audio/wav", "audio/mpeg"])
def test_the_demuxer_is_imposed_by_the_detected_type(declared: str) -> None:
    """An Ogg recording is never read as another format, nor guessed from its content."""
    with pytest.raises(SttFailure) as error:
        decode_isolated(samples.record("audio/ogg"), mime=declared, max_seconds=60)
    assert (error.value.code, error.value.status) == ("unreadable", 422)
    with pytest.raises(SttFailure) as error:
        decode_isolated(samples.record("audio/ogg"), mime="audio/flac", max_seconds=60)
    assert (error.value.code, error.value.status) == ("unsupported_type", 415)


def test_hostile_recordings_never_reach_the_model() -> None:
    """Refused while decoding: the model is not needed, not even loaded (here, absent)."""
    engine = Engine(Settings(stt_model="/nonexistent", stt_device="cpu"))
    for build, mime, accepted in CORPUS.values():
        if "ok" in accepted:
            continue
        with pytest.raises(SttFailure) as error:
            engine.transcribe(build(), mime=mime, max_seconds=60)
        assert error.value.code in accepted
    assert not engine.loaded


def test_a_crash_or_a_hang_fails_this_recording_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child out of its memory or hung: a stable code, never retried. A child ended by a
    signal (the OOM killer, a decoder crash): 500, retried a few times by the API."""
    data = samples.record("audio/ogg")

    def died(*args: object, **kwargs: object) -> None:
        raise isolation.ChildDiedError

    def killed(*args: object, **kwargs: object) -> None:
        raise isolation.ChildDiedError(signal.SIGKILL)

    def hung(*args: object, **kwargs: object) -> None:
        raise isolation.ChildTimeoutError

    for fake, code, status in (
        (died, "unreadable", 422),
        (killed, "transcription_crashed", 500),
        (hung, "too_slow", 422),
    ):
        monkeypatch.setattr(stt_engine.isolation, "run", fake)
        with pytest.raises(SttFailure) as error:
            decode_isolated(data, mime="audio/ogg", max_seconds=60)
        assert (error.value.code, error.value.status) == (code, status)
    monkeypatch.undo()
    # The next recording is decoded normally.
    decoded = decode_isolated(data, mime="audio/ogg", max_seconds=60)
    assert len(decoded.samples) / stt_engine.SAMPLE_RATE == pytest.approx(3.0, abs=0.15)
