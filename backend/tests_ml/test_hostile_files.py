"""Hostile files against the `ml` service (ADR 0011; CI job `ml-model`).

Corrupt or booby-trapped PDFs, oversized or malformed pictures, disguised files: each one,
generated in code (`ml_samples`), must fail with a stable code, or give a bounded and
harmless result, quickly, in the confined child process (`lifeos.isolation`), leaving no
process behind. The service keeps answering meanwhile, and the API client turns each
refusal into a permanent failure (never retried).
"""

import asyncio
import io
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

pytest.importorskip("pypdfium2")
pytest.importorskip("pillow_heif")

import ml_samples as samples
from PIL import Image

from lifeos import isolation
from lifeos.config import Settings
from lifeos.ml import app as ml_app
from lifeos.ml import extract as extract_module
from lifeos.ml.app import create_ml_app
from lifeos.ml.extract import DEFAULT_MEMORY_MB, ExtractFailure, Result, run_isolated
from lifeos.modules.documents.extraction import RemoteExtractor, UnreadableDocument
from lifeos.modules.documents.sniff import sniff
from lifeos.modules.retrieval.embedders import FakeEmbedder

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="needs Tesseract (tesseract-ocr-fra)"
)
PDF = "application/pdf"
# A hostile file is dealt with in seconds, far from the service's own time limit.
QUICK_S = 20.0

# (builder, declared type, accepted outcomes: "ok" or a failure code)
CORPUS: dict[str, tuple[Callable[[], bytes], str, set[str]]] = {
    # Corrupt PDFs
    "pdf_truncated": (samples.pdf_truncated, PDF, {"unreadable"}),
    "pdf_garbage": (samples.pdf_garbage, PDF, {"unreadable"}),
    "pdf_page_tree_loop": (samples.pdf_page_tree_loop, PDF, {"unreadable"}),
    "pdf_xref_loop": (samples.pdf_xref_loop, PDF, {"ok"}),
    "pdf_without_xref": (samples.pdf_without_xref, PDF, {"ok"}),
    "pdf_length_loop": (samples.pdf_length_loop, PDF, {"ok"}),
    "pdf_deep_nesting": (samples.pdf_deep_nesting, PDF, {"ok", "unreadable"}),
    "pdf_huge_count": (samples.pdf_huge_count, PDF, {"ok"}),
    "pdf_huge_page": (samples.pdf_huge_page, PDF, {"ok"}),
    # Booby-trapped PDFs: nothing is executed, the text layer alone is read.
    "pdf_active_content": (samples.pdf_active_content, PDF, {"ok"}),
    "pdf_html_polyglot": (samples.pdf_html_polyglot, PDF, {"ok"}),
    "pdf_encrypted": (samples.pdf_encrypted, PDF, {"encrypted"}),
    "pdf_huge_image": (lambda: samples.image_pdf(8000, 8000), PDF, {"image_too_large"}),
    # Oversized or malformed pictures
    "png_text_bomb": (samples.png_text_bomb, "image/png", {"unreadable"}),
    "png_icc_bomb": (samples.png_icc_bomb, "image/png", {"unreadable"}),
    "png_truncated": (samples.png_truncated, "image/png", {"unreadable"}),
    "png_animated": (lambda: samples.apng(2000), "image/png", {"ok"}),
    "webp_huge_canvas": (lambda: samples.webp_huge_canvas(16384), "image/webp", {"unreadable"}),
    "jpeg_scan_bomb": (lambda: samples.jpeg_scans(2000, 2000), "image/jpeg", {"unreadable"}),
    "jpeg_huge_header": (
        lambda: samples.jpeg_huge_header(65000, 65000),
        "image/jpeg",
        {"image_too_large"},
    ),
    "heic_huge_header": (
        lambda: samples.heic_huge_header(20000),
        "image/heic",
        {"image_too_large"},
    ),
    "heic_lying_header": (
        lambda: samples.heic_huge_header(6000),
        "image/heic",
        {"unreadable"},
    ),
    # Disguised: a JPEG that is also a ZIP archive.
    "jpeg_zip_polyglot": (samples.jpeg_zip_polyglot, "image/jpeg", {"ok"}),
}


def _outcome(
    data: bytes, mime: str, *, memory_mb: int = DEFAULT_MEMORY_MB
) -> tuple[str, Result | ExtractFailure]:
    try:
        result = run_isolated(data, mime, purpose="document", max_pages=30, memory_mb=memory_mb)
    except ExtractFailure as failure:
        return failure.code, failure
    return "ok", result


@needs_tesseract
@pytest.mark.parametrize("name", sorted(CORPUS))
def test_hostile_files_fail_quickly_in_the_isolated_child(name: str) -> None:
    build, mime, accepted = CORPUS[name]
    data = build()
    detected = sniff(data)
    # Each sample passes the upload check: it reaches the reader, as an attacker's would.
    assert detected is not None and detected.mime == mime
    started = time.monotonic()
    code, outcome = _outcome(data, mime)
    assert time.monotonic() - started < QUICK_S
    assert code in accepted, (code, accepted)
    if isinstance(outcome, ExtractFailure):
        assert outcome.status == 422  # permanent: never retried
    else:
        assert outcome.pages == 1 and len(outcome.text) < 2000
        if outcome.sanitized is not None:
            # Re-encoded from the pixels: what followed the picture is gone.
            assert b"PK\x03\x04" not in outcome.sanitized
            assert samples.ZIP_PAYLOAD not in outcome.sanitized
            with Image.open(io.BytesIO(outcome.sanitized)) as clean:
                assert getattr(clean, "n_frames", 1) == 1
    assert multiprocessing.active_children() == []


def test_a_memory_bomb_fails_without_hurting_the_service() -> None:
    # 400 MB of drawing instructions in about 1 MB, against a 320 MB limit.
    code, failure = _outcome(samples.pdf_content_bomb(400), PDF, memory_mb=320)
    assert code == "too_large"
    assert isinstance(failure, ExtractFailure) and failure.status == 422
    # The next document is read normally.
    code, result = _outcome(samples.text_pdf(["Bonjour"]), PDF)
    assert code == "ok" and isinstance(result, Result) and result.text == "Bonjour"
    assert result.peak_mb is not None and result.peak_mb > 0


def test_the_child_is_confined() -> None:
    answer = isolation.run(samples.confinement, (), memory_mb=512, timeout_s=30)
    limits = answer.value
    assert limits["own_group"] is True
    assert limits["oom_score_adj"] == str(isolation.OOM_SCORE_ADJ)
    assert limits["core"] == (0, 0)
    assert limits["data"] == (512 * 1024 * 1024, 512 * 1024 * 1024)
    assert multiprocessing.active_children() == []


def test_a_crashing_decoder_takes_down_its_child_only() -> None:
    with pytest.raises(isolation.ChildDiedError) as error:
        isolation.run(samples.crash, (), memory_mb=512, timeout_s=30)
    assert error.value.signal == signal.SIGABRT
    assert multiprocessing.active_children() == []
    assert isolation.run(samples.confinement, (), memory_mb=512, timeout_s=30).value


def test_a_reader_killed_from_outside_is_a_crash_not_a_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kernel's OOM killer picks the reader first when the container runs short (the
    model's peak, not the file): a legitimate photo is then retried (500), never failed for
    good as too large (422), which would drop its original."""
    data = samples.encode(Image.new("RGB", (4000, 3000), "white"), "JPEG")

    def killed(*args: object, **kwargs: object) -> None:
        raise isolation.ChildDiedError(signal.SIGKILL)

    monkeypatch.setattr(extract_module.isolation, "run", killed)
    with pytest.raises(ExtractFailure) as error:
        run_isolated(data, "image/jpeg", purpose="document", max_pages=1)
    assert (error.value.code, error.value.status) == ("extraction_crashed", 500)
    assert error.value.signal == signal.SIGKILL
    monkeypatch.undo()
    # The same file reads fine once memory is back.
    code, result = _outcome(samples.text_pdf(["Bonjour"]), PDF)
    assert code == "ok" and isinstance(result, Result) and result.text == "Bonjour"


# The OCR engine replaced by a script that hangs: run in a fresh interpreter, whose fork
# server finds it first on its PATH.
_HANGING = """
import io, sys
from PIL import Image
from lifeos.ml.extract import ExtractFailure, run_isolated
buffer = io.BytesIO()
Image.new("RGB", (300, 100), "white").save(buffer, "PNG")
try:
    run_isolated(buffer.getvalue(), "image/png", purpose="document", max_pages=1, timeout_s=3)
except ExtractFailure as exc:
    print(exc.code)
"""


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state not in ("Z", "X")


def test_a_hung_extraction_is_killed_with_its_ocr(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    fake = tmp_path / "tesseract"
    fake.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 120\n")
    fake.chmod(0o755)
    environment = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    done = subprocess.run(  # noqa: S603  (this interpreter, a fixed script)
        [sys.executable, "-c", _HANGING],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.stdout.strip() == "too_slow", done.stderr[-2000:]
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        # Killed with the child (its process group), not left to run its course.
        assert not _alive(pid)
    finally:
        if _alive(pid):
            os.kill(pid, 9)


async def test_the_service_keeps_answering_during_a_hostile_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ml_app, "QUEUE_WAIT_S", 0.5)
    app = create_ml_app(Settings(), embedder=FakeEmbedder())
    transport = httpx.ASGITransport(app=app)
    # 450 passes over a 9 Mpx picture: a few seconds of CPU in the child.
    slow = samples.jpeg_scans(3000, 450)

    async def extract(data: bytes, mime: str) -> httpx.Response:
        return await client.post(
            "/extract",
            files={"file": ("document", data, mime)},
            data={"mime": mime, "purpose": "document", "max_pages": "30"},
            timeout=120,
        )

    async with httpx.AsyncClient(transport=transport, base_url="http://ml") as client:
        reading = asyncio.create_task(extract(slow, "image/jpeg"))
        await asyncio.sleep(1.0)  # the slow file holds the extraction slot
        assert not reading.done()
        started = time.monotonic()
        health = await client.get("/healthz")
        vectors = await client.post("/embed", json={"texts": ["bonjour"], "kind": "query"})
        assert health.status_code == 200 and vectors.status_code == 200
        assert time.monotonic() - started < 1.0
        # Another document waits a bounded time for the slot, then is retried later.
        busy = await extract(samples.text_pdf(["Bonjour"]), PDF)
        assert (busy.status_code, busy.json()) == (503, {"code": "busy"})
        answer = await reading
        assert answer.status_code in (200, 422) and answer.status_code != 500
    assert multiprocessing.active_children() == []


async def test_the_api_client_never_retries_a_hostile_file() -> None:
    settings = Settings(ml_url="http://ml:8100")
    app = create_ml_app(settings, embedder=FakeEmbedder())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ml") as client:
        remote = RemoteExtractor(settings, client=client)
        refused = {
            "unreadable": (samples.pdf_truncated(), PDF),
            "encrypted": (samples.pdf_encrypted(), PDF),
            "image_too_large": (samples.jpeg_huge_header(65000, 65000), "image/jpeg"),
            # Disguised: the declared type is not the one found by signature.
            "unsupported_type": (samples.encode(Image.new("RGB", (8, 8)), "PNG"), "image/jpeg"),
        }
        for code, (data, mime) in refused.items():
            with pytest.raises(UnreadableDocument) as error:
                await remote.extract(data, mime, purpose="document", max_pages=30)
            assert error.value.code == code
