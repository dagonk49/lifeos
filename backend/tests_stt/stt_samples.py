"""Recordings for the `stt` tests, generated in code with PyAV: no binary fixture to trust.
What browsers record (`record`), and hostile or disguised files (ADR 0011), a few kilobytes
each.

Not a test module: imported by them.
"""

import io
import struct
from typing import Any

import av
import numpy as np
from av.audio.resampler import AudioResampler

RATE = 48_000


def speechlike(seconds: float, rate: int = RATE) -> Any:
    """A modulated harmonic tone with a pause: enough for the voice activity detector."""
    t = np.arange(int(rate * seconds)) / rate
    signal = (
        0.3 * np.sin(2 * np.pi * 220 * t)
        + 0.15 * np.sin(2 * np.pi * 440 * t)
        + 0.07 * np.sin(2 * np.pi * 880 * t)
    ) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))
    signal[int(1.0 * rate) : int(1.5 * rate)] = 0
    return np.stack([signal, signal]).astype(np.float32)


# (container, codec, container options, sample format): what browsers record.
FORMATS = {
    "audio/webm": ("webm", "libopus", {"live": "1"}, "fltp"),  # Chrome, Edge, Firefox
    "audio/ogg": ("ogg", "libopus", {}, "flt"),  # Firefox
    # Safari: fragmented AAC in MP4.
    "audio/mp4": ("mp4", "aac", {"movflags": "frag_keyframe+empty_moov+default_base_moof"}, "fltp"),
    "audio/wav": ("wav", "pcm_s16le", {}, "s16"),
}


def _audio_stream(container: Any, codec: str) -> Any:
    stream = container.add_stream(codec, rate=RATE)
    stream.layout = "stereo"
    return stream


def _video_stream(container: Any, codec: str) -> Any:
    stream = container.add_stream(codec, rate=10)
    stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
    return stream


def _write_audio(container: Any, stream: Any, sample_format: str, seconds: float) -> None:
    resampler = AudioResampler(format=sample_format, layout="stereo", rate=RATE)
    samples = speechlike(seconds)
    step = 4800
    for start in range(0, samples.shape[1], step):
        frame = av.AudioFrame.from_ndarray(
            samples[:, start : start + step].copy(), format="fltp", layout="stereo"
        )
        frame.sample_rate, frame.pts = RATE, start
        for converted in resampler.resample(frame):
            for packet in stream.encode(converted):
                container.mux(packet)
    for converted in resampler.resample(None):
        for packet in stream.encode(converted):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)


def _write_video(container: Any, stream: Any, frames: int = 10) -> None:
    for index in range(frames):
        frame = av.VideoFrame.from_ndarray(np.zeros((64, 64, 3), np.uint8), format="rgb24")
        frame = frame.reformat(format="yuv420p")
        frame.pts = index
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)


def record(mime: str, seconds: float = 3.0) -> bytes:
    """A recording as a browser makes it."""
    container_format, codec, options, sample_format = FORMATS[mime]
    buffer = io.BytesIO()
    with av.open(buffer, "w", format=container_format, options=options) as container:
        _write_audio(container, _audio_stream(container, codec), sample_format, seconds)
    return buffer.getvalue()


def encoded(
    container_format: str,
    *,
    audio: str | None = None,
    video: str | None = None,
    sample_format: str = "fltp",
    seconds: float = 1.0,
) -> bytes:
    """A file in this container with these audio and video codecs (either may be absent)."""
    buffer = io.BytesIO()
    with av.open(buffer, "w", format=container_format) as container:
        # Every stream is declared before the first packet (the header is written then).
        sound = _audio_stream(container, audio) if audio is not None else None
        picture = _video_stream(container, video) if video is not None else None
        if picture is not None:
            _write_video(container, picture)
        if sound is not None:
            _write_audio(container, sound, sample_format, seconds)
    return buffer.getvalue()


# --- Hostile or disguised recordings ---------------------------------------------------------


def mp4_video_only() -> bytes:
    """A video without sound, in an MP4 of a generic brand (`isom`, accepted as audio)."""
    return encoded("mp4", video="mpeg4")


def webm_video_only() -> bytes:
    return encoded("webm", video="libvpx")


def mp4_video_with_sound() -> bytes:
    """A video with an AAC track: only the sound is ever decoded."""
    return encoded("mp4", audio="aac", video="mpeg4")


def wav_adpcm() -> bytes:
    """A WAV whose samples use an old compressed codec, outside the allowed list."""
    return encoded("wav", audio="adpcm_ms", sample_format="s16")


def matroska_mp2() -> bytes:
    """Matroska (WebM's container) with MPEG-1 Layer II sound: not a browser recording."""
    return encoded("matroska", audio="mp2", sample_format="s16")


def mp4_ac3() -> bytes:
    return encoded("mp4", audio="ac3", sample_format="fltp")


def id3_then_ac3() -> bytes:
    """An ID3 tag (the signature of an MP3) in front of a raw AC-3 stream: FFmpeg would
    recognise the AC-3 if it guessed the format from the content."""
    tag = b"ID3\x04\x00\x00\x00\x00\x00\x00"
    return tag + encoded("ac3", audio="ac3", sample_format="fltp")


def _wav_field(data: bytearray, chunk: bytes, offset: int, value: bytes) -> bytes:
    start = data.find(chunk)
    data[start + offset : start + offset + len(value)] = value
    return bytes(data)


def wav_lying_sizes() -> bytes:
    """A one second WAV whose RIFF and data chunks each claim 4 GB."""
    data = bytearray(record("audio/wav", seconds=1.0))
    data[4:8] = b"\xff\xff\xff\xff"
    return _wav_field(data, b"data", 4, b"\xff\xff\xff\xff")


def wav_many_channels() -> bytes:
    """A WAV header announcing 65 535 channels."""
    return _wav_field(bytearray(record("audio/wav", seconds=1.0)), b"fmt ", 10, b"\xff\xff")


def wav_slow_rate() -> bytes:
    """One second of samples announced at 1 Hz: thirteen hours of audio for the decoder."""
    data = bytearray(record("audio/wav", seconds=1.0))
    return _wav_field(data, b"fmt ", 12, struct.pack("<I", 1))


def noise_after(signature: bytes) -> bytes:
    """A valid signature, then (reproducible) random bytes."""
    return signature + np.random.default_rng(0).bytes(50_000)


SIGNATURES = {
    "ogg": b"OggS",
    "wav": b"RIFF\x00\x00\x00\x00WAVE",
    "webm": b"\x1a\x45\xdf\xa3",
    "m4a": b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom",
    "mp3": b"ID3\x04\x00\x00\x00\x00\x00\x00",
}


def truncated_webm() -> bytes:
    """A browser recording whose content after the header was lost (zeros)."""
    data = record("audio/webm")
    return data[:40] + bytes(len(data) - 40)
