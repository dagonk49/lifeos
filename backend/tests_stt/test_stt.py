"""The `stt` service pipeline, offline (CI job `ml-model`; needs the `stt` extra).

The real model (1.6 GB) is not downloaded in CI: a tiny Whisper with random weights and
the same vocabulary layout is built here (a few MB, no network). It transcribes nothing
meaningful, but runs the real chain: browser audio formats decoded by PyAV, voice activity
detection, CTranslate2 decoding, the HTTP contract and the API's client.
"""

import io
import multiprocessing
from pathlib import Path

import httpx
import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")
pytest.importorskip("faster_whisper")

import stt_samples as samples  # noqa: E402
from ctranslate2.specs import whisper_spec  # noqa: E402
from stt_samples import FORMATS, record  # noqa: E402
from tokenizers import Tokenizer, decoders, models, pre_tokenizers  # noqa: E402

from lifeos.config import Settings  # noqa: E402
from lifeos.modules.voice.transcriber import RemoteTranscriber, UntranscribableAudio  # noqa: E402
from lifeos.stt.app import create_stt_app  # noqa: E402
from lifeos.stt.engine import Engine, SttFailure  # noqa: E402

# Whisper's 99 languages, in token order.
LANGUAGES = (  # noqa: SIM905  (readable as one block)
    "en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro da hu ta "
    "no th ur hr bg lt la mi ml cy sk te fa lv bn sr az sl kn et mk br eu is hy ne mn bs kk "
    "sq sw gl mr pa si km sn yo so af oc ka be tg sd gu am yi lo uz fo ht ps tk nn mt sa lb "
    "my bo tl mg as tt haw ln ha ba jw su"
).split()
assert len(LANGUAGES) == 99


def build_tiny_whisper(out: Path) -> Path:
    """A 1-layer multilingual Whisper (CTranslate2, int8) with random weights, and a
    word-level tokenizer over the real token layout (ids of special tokens match)."""
    dim, heads, mels, vocabulary = 64, 2, 80, 51865
    rng = np.random.default_rng(0)

    def rand(*shape: int) -> object:
        return rng.standard_normal(shape, dtype=np.float32) * 0.02

    def zeros(size: int) -> object:
        return np.zeros(size, np.float32)

    def norm(spec: object) -> None:
        spec.gamma, spec.beta = np.ones(dim, np.float32), zeros(dim)  # type: ignore[attr-defined]

    def linear(spec: object, rows: int, cols: int) -> None:
        spec.weight, spec.bias = rand(rows, cols), zeros(rows)  # type: ignore[attr-defined]

    spec = whisper_spec.WhisperSpec(1, heads, 1, heads)
    encoder = spec.encoder
    encoder.conv1.weight, encoder.conv1.bias = rand(dim, mels, 3), zeros(dim)
    encoder.conv2.weight, encoder.conv2.bias = rand(dim, dim, 3), zeros(dim)
    encoder.position_encodings.encodings = rand(1500, dim)
    norm(encoder.layer_norm)
    for layer in encoder.layer:
        norm(layer.self_attention.layer_norm)
        linear(layer.self_attention.linear[0], 3 * dim, dim)
        linear(layer.self_attention.linear[1], dim, dim)
        norm(layer.ffn.layer_norm)
        linear(layer.ffn.linear_0, 4 * dim, dim)
        linear(layer.ffn.linear_1, dim, 4 * dim)
    decoder = spec.decoder
    decoder.embeddings.weight = rand(vocabulary, dim)
    decoder.position_encodings.encodings = rand(448, dim)
    norm(decoder.layer_norm)
    decoder.projection.weight = decoder.embeddings.weight
    for layer in decoder.layer:
        norm(layer.self_attention.layer_norm)
        linear(layer.self_attention.linear[0], 3 * dim, dim)
        linear(layer.self_attention.linear[1], dim, dim)
        norm(layer.attention.layer_norm)
        linear(layer.attention.linear[0], dim, dim)
        linear(layer.attention.linear[1], 2 * dim, dim)
        linear(layer.attention.linear[2], dim, dim)
        norm(layer.ffn.layer_norm)
        linear(layer.ffn.linear_0, 4 * dim, dim)
        linear(layer.ffn.linear_1, dim, 4 * dim)
    # Token 50256 must decode to nothing (faster-whisper strips it), then the specials.
    tokens = (
        [f"w{i}" for i in range(50256)]
        + ["", "<|endoftext|>", "<|startoftranscript|>"]
        + [f"<|{language}|>" for language in LANGUAGES]
        + ["<|translate|>", "<|transcribe|>", "<|startoflm|>", "<|startofprev|>"]
        + ["<|nocaptions|>", "<|notimestamps|>"]
        + [f"<|{i * 0.02:.2f}|>" for i in range(1501)]
    )
    assert len(tokens) == vocabulary
    spec.register_vocabulary(tokens)
    config = spec.config
    config.suppress_ids = [50257]
    config.suppress_ids_begin = [220, 50257]
    config.lang_ids = list(range(50259, 50259 + len(LANGUAGES)))
    config.alignment_heads = [(0, 0)]
    spec.validate()
    spec.optimize(quantization="int8")
    out.mkdir(parents=True, exist_ok=True)
    spec.save(str(out))
    tokenizer = Tokenizer(
        models.WordLevel({t: i for i, t in enumerate(tokens)}, unk_token="w0")  # noqa: S106
    )
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tokenizer.decoder = decoders.WordPiece(prefix="##")
    tokenizer.add_special_tokens(["<|endoftext|>"])
    tokenizer.save(str(out / "tokenizer.json"))
    return out


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


@pytest.mark.parametrize("mime", sorted(FORMATS))
def test_browser_recordings_are_transcribed_offline(settings: Settings, mime: str) -> None:
    engine = Engine(settings)
    data = record(mime)
    transcript = engine.transcribe(data, mime=mime, max_seconds=60)
    assert transcript.duration == pytest.approx(3.0, abs=0.15)
    assert isinstance(transcript.text, str)
    assert engine.loaded and engine.device == "cpu"


def test_limits_and_unreadable_audio(settings: Settings) -> None:
    engine = Engine(settings)
    with pytest.raises(SttFailure) as error:
        engine.transcribe(record("audio/ogg", seconds=8.0), mime="audio/ogg", max_seconds=2)
    assert (error.value.code, error.value.status) == ("too_long", 422)
    # A recording stopped at the limit overshoots it slightly: accepted.
    ogg = record("audio/ogg", seconds=3.0)
    assert engine.transcribe(ogg, mime="audio/ogg", max_seconds=2).duration > 2
    webm = record("audio/webm")
    with pytest.raises(SttFailure) as error:
        engine.transcribe(webm[:40] + bytes(len(webm) - 40), mime="audio/webm", max_seconds=60)
    assert error.value.code == "unreadable"
    # A tap on the button: nothing to transcribe, the model is not even loaded.
    fresh = Engine(settings)
    tap = fresh.transcribe(record("audio/wav", seconds=0.1), mime="audio/wav", max_seconds=60)
    assert tap.text == "" and not fresh.loaded


def test_model_is_unloaded_when_idle(settings: Settings) -> None:
    engine = Engine(settings.model_copy(update={"stt_idle_unload_s": 0}))
    engine.transcribe(record("audio/ogg"), mime="audio/ogg", max_seconds=60)
    assert engine.loaded
    assert engine.unload_if_idle()
    assert not engine.loaded and engine.device is None


def test_missing_model_is_unavailable_not_unreadable(settings: Settings, tmp_path: Path) -> None:
    engine = Engine(settings.model_copy(update={"stt_model": str(tmp_path / "absent")}))
    with pytest.raises(SttFailure) as error:
        engine.transcribe(record("audio/ogg"), mime="audio/ogg", max_seconds=60)
    assert (error.value.code, error.value.status) == ("model_unavailable", 503)


def _client(settings: Settings) -> httpx.AsyncClient:
    app = create_stt_app(settings)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://stt")


async def test_http_contract_and_api_client(settings: Settings) -> None:
    async with _client(settings) as client:
        remote = RemoteTranscriber(settings, client=client)
        result = await remote.transcribe(record("audio/mp4"), "audio/mp4", max_seconds=60)
        assert result.duration == pytest.approx(3.0, abs=0.15)
        assert isinstance(result.text, str)

        with pytest.raises(UntranscribableAudio) as error:
            await remote.transcribe(record("audio/ogg", seconds=8.0), "audio/ogg", max_seconds=1)
        assert error.value.code == "too_long"

        # The declared type must match the signature; images are not audio.
        response = await client.post(
            "/transcribe",
            files={"file": ("dictation", record("audio/ogg"), "audio/webm")},
            data={"mime": "audio/webm"},
        )
        assert (response.status_code, response.json()) == (415, {"code": "unsupported_type"})
        health = (await client.get("/healthz")).json()
        assert health["loaded"] is True and health["device"] == "cpu"


async def test_hostile_recordings_over_http_leave_the_service_up(settings: Settings) -> None:
    """Refused with a stable code that the API never retries (ADR 0011); the decoding child
    died, not the service: the model stays loaded and the next dictation is transcribed."""
    async with _client(settings) as client:
        remote = RemoteTranscriber(settings, client=client)
        await remote.transcribe(record("audio/ogg"), "audio/ogg", max_seconds=60)
        refused = {
            "unreadable": (samples.mp4_video_only(), "audio/mp4"),
            "unsupported_codec": (samples.wav_adpcm(), "audio/wav"),
            "too_long": (samples.wav_slow_rate(), "audio/wav"),
        }
        for code, (data, mime) in refused.items():
            with pytest.raises(UntranscribableAudio) as error:
                await remote.transcribe(data, mime, max_seconds=60)
            assert error.value.code == code
        # Disguised: the declared type is not the one found by signature.
        with pytest.raises(UntranscribableAudio) as error:
            await remote.transcribe(record("audio/ogg"), "audio/webm", max_seconds=60)
        assert error.value.code == "unsupported_type"
        health = (await client.get("/healthz")).json()
        assert health["loaded"] is True
        result = await remote.transcribe(record("audio/webm"), "audio/webm", max_seconds=60)
        assert result.duration == pytest.approx(3.0, abs=0.15)
    assert multiprocessing.active_children() == []


def test_long_audio_is_refused_while_decoding(settings: Settings) -> None:
    """Ten minutes of silence in a small file: refused once the decoded part passes the
    limit, never expanded in memory first."""
    from av.audio.resampler import AudioResampler

    rate, seconds = 8000, 600
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
    data = buffer.getvalue()
    assert len(data) < 200_000
    from lifeos.stt.engine import decode

    with pytest.raises(SttFailure) as error:
        decode(data, mime="audio/ogg", max_seconds=30)
    assert error.value.code == "too_long"
    # Resampled to 16 kHz.
    assert len(decode(data, mime="audio/ogg", max_seconds=900)) == rate * seconds * 2
