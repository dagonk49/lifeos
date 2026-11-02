"""Progress, cancellation, timings and the bench command of the `stt` service (ADR 0009).

Same tiny random Whisper as `test_stt` (no download): it transcribes nothing meaningful,
but runs the real chain, so the timings, progress steps and cancellation are real.
"""

import asyncio
import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")
pytest.importorskip("faster_whisper")

from stt_samples import record  # noqa: E402
from test_stt import build_tiny_whisper  # noqa: E402

from lifeos.config import Settings  # noqa: E402
from lifeos.modules.voice.transcriber import RemoteTranscriber  # noqa: E402
from lifeos.stt import bench  # noqa: E402
from lifeos.stt.app import create_stt_app  # noqa: E402
from lifeos.stt.engine import Engine, SttFailure, decode_isolated  # noqa: E402


@pytest.fixture(scope="module")
def tiny_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_tiny_whisper(tmp_path_factory.mktemp("whisper") / "tiny")


@pytest.fixture
def settings(tiny_model: Path) -> Settings:
    return Settings(
        stt_model=str(tiny_model),
        stt_device="cpu",
        stt_threads=2,
        stt_beam_size=1,
        stt_url="http://stt:8200",
        stt_provider="remote",
    )


def test_progress_is_monotonic_and_reaches_the_end(settings: Settings) -> None:
    engine = Engine(settings)
    seen: list[float] = []
    lengths: list[float] = []
    transcript = engine.transcribe(
        record("audio/ogg", seconds=40.0),
        mime="audio/ogg",
        max_seconds=60,
        on_progress=seen.append,
        on_decoded=lengths.append,
    )
    assert seen and all(0.0 <= value <= 1.0 for value in seen)
    assert seen == sorted(seen) and seen[-1] >= 0.9
    assert lengths == [pytest.approx(transcript.duration)]
    # Timings are measured, and say what ran.
    assert transcript.load_s is not None and transcript.load_s > 0
    assert transcript.decode_s > 0 and transcript.transcribe_s > 0
    assert (transcript.device, transcript.compute_type) == ("cpu", "int8")
    again = engine.transcribe(record("audio/ogg"), mime="audio/ogg", max_seconds=60)
    assert again.load_s is None  # already loaded


def test_a_cancelled_transcription_stops_and_frees_the_engine(settings: Settings) -> None:
    engine = Engine(settings)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(SttFailure) as error:
        engine.transcribe(record("audio/ogg"), mime="audio/ogg", max_seconds=60, cancel=cancel)
    assert (error.value.code, error.value.status) == ("cancelled", 422)
    # The lock was released: the next dictation goes through.
    assert engine.transcribe(record("audio/ogg"), mime="audio/ogg", max_seconds=60).duration > 2

    # Cancelled between two segments of a longer recording.
    stop = threading.Event()

    def halfway(value: float) -> None:
        if value > 0:
            stop.set()

    with pytest.raises(SttFailure) as error:
        engine.transcribe(
            record("audio/ogg", seconds=90.0),
            mime="audio/ogg",
            max_seconds=120,
            on_progress=halfway,
            cancel=stop,
        )
    assert error.value.code == "cancelled"


def _silent_ogg(seconds: int, rate: int = 8000) -> bytes:
    from av.audio.resampler import AudioResampler

    buffer = io.BytesIO()
    with av.open(buffer, "w", format="ogg") as container:
        stream = container.add_stream("flac", rate=rate)
        stream.layout = "mono"
        resampler = AudioResampler(format="s16", layout="mono", rate=rate)
        silence = np.zeros((1, rate), dtype=np.int16)
        for second in range(seconds):
            frame = av.AudioFrame.from_ndarray(silence, format="s16", layout="mono")
            frame.sample_rate, frame.pts = rate, second * rate
            for converted in resampler.resample(frame):
                for packet in stream.encode(converted):
                    container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


def test_cancel_during_decoding_kills_the_decoder(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lifeos import isolation

    # Decoding ten minutes takes a fraction of a second here: check the event often.
    monkeypatch.setattr(isolation, "CANCEL_POLL_S", 0.005)
    data = _silent_ogg(600)  # ten minutes
    started = time.monotonic()
    decode_isolated(data, mime="audio/ogg", max_seconds=900)
    full = time.monotonic() - started
    cancel = threading.Event()
    timer = threading.Timer(min(0.02, full / 4), cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(SttFailure) as error:
            decode_isolated(data, mime="audio/ogg", max_seconds=900, cancel=cancel)
    finally:
        timer.cancel()
    assert error.value.code == "cancelled"
    assert time.monotonic() - started < max(0.6, full)
    import multiprocessing

    assert multiprocessing.active_children() == []


class _HeldEngine(Engine):
    """Waits before transcribing, until released or cancelled: a long dictation."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.release = threading.Event()
        self.waiting = threading.Event()

    def transcribe(self, data: bytes, **options: Any) -> Any:
        self.waiting.set()
        cancel = options.get("cancel")
        while not self.release.is_set() and not (cancel is not None and cancel.is_set()):
            time.sleep(0.01)
        return super().transcribe(data, **options)


def _client(settings: Settings, engine: Engine | None = None) -> httpx.AsyncClient:
    app = create_stt_app(settings, engine=engine)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://stt")


async def test_progress_and_cancel_over_http(settings: Settings) -> None:
    engine = _HeldEngine(settings)
    async with _client(settings, engine) as client:
        idle = (await client.get("/progress")).json()
        assert idle == {
            "busy": False,
            "job": None,
            "progress": None,
            "audio_seconds": None,
            "elapsed": None,
        }
        remote = RemoteTranscriber(settings, client=client)
        assert await remote.progress("job-1") is None

        async def post() -> httpx.Response:
            return await client.post(
                "/transcribe",
                files={"file": ("dictation", record("audio/ogg"), "audio/ogg")},
                data={"mime": "audio/ogg", "job": "job-1"},
            )

        running = asyncio.create_task(post())
        await asyncio.to_thread(engine.waiting.wait, 5)
        busy = (await client.get("/progress")).json()
        assert busy["busy"] is True and busy["job"] == "job-1" and busy["progress"] == 0.0
        progress = await remote.progress("job-1")
        assert progress is not None and progress.progress == 0.0
        assert await remote.progress("another") is None
        # Another job is not stopped; this one is.
        assert (await client.post("/cancel", data={"job": "other"})).json() == {"cancelled": False}
        assert (await client.post("/cancel", data={"job": "job-1"})).json() == {"cancelled": True}
        response = await asyncio.wait_for(running, 30)
        assert (response.status_code, response.json()) == (422, {"code": "cancelled"})
        assert (await client.get("/progress")).json()["busy"] is False
        # A malformed job name is refused before anything runs.
        refused = await client.post(
            "/transcribe",
            files={"file": ("dictation", record("audio/ogg"), "audio/ogg")},
            data={"mime": "audio/ogg", "job": "../../x"},
        )
        assert refused.status_code == 422

        engine.release.set()
        answer = await post()
        assert answer.status_code == 200
        body = answer.json()
        assert body["device"] == "cpu" and body["compute_type"] == "int8"
        assert set(body["timings"]) == {"decode", "load", "transcribe", "total"}
        assert body["timings"]["load"] is not None  # loaded by this call
        assert isinstance(body["rtf"], float) and body["rtf"] > 0
        result = await remote.transcribe(record("audio/ogg"), "audio/ogg", max_seconds=60)
        assert result.device == "cpu" and result.elapsed is not None and result.rtf is not None
        metrics = (await client.get("/metrics")).text
        assert 'lifeos_stt_rtf_count{device="cpu"} 2.0' in metrics


def test_word_and_character_error_rates() -> None:
    assert bench.wer("a b c", "a x c") == pytest.approx(1 / 3)
    assert bench.wer("Bonjour, à demain !", "bonjour a demain") == 0.0
    assert bench.wer("", "") == 0.0 and bench.wer("", "bruit") == 1.0
    assert bench.wer("un deux", "") == 1.0
    assert bench.cer("abc", "abd") == pytest.approx(1 / 3)
    assert bench.cer("L'été", "l ete") == 0.0


def test_bench_command_reports_measurements(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    audio = tmp_path / "dictee.ogg"
    audio.write_bytes(record("audio/ogg"))
    reference = tmp_path / "texte.txt"
    reference.write_text("Rappelle-moi d'acheter du pain.", encoding="utf-8")
    code = bench.main(
        [str(audio), "--reference", str(reference), "--runs", "2", "--threads", "1", "2"],
        settings=settings,
    )
    assert code == 0
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert [row["threads"] for row in rows] == [1, 2]
    for row in rows:
        assert (row["device"], row["compute_type"], row["runs"]) == ("cpu", "int8", 2)
        assert isinstance(row["cold_load_s"], float) and isinstance(row["rtf"], float)
        assert isinstance(row["wer"], float) and isinstance(row["cer"], float)
        assert isinstance(row["peak_rss_mb"], int)
        assert row["cgroup_peak_mb"] is None or isinstance(row["cgroup_peak_mb"], int)
    assert "facteur temps réel" in captured.err
    assert "pain" not in captured.out + captured.err  # never the text
