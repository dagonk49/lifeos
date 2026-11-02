"""`lifeos-ml bench`: metrics, synthetic corpus and the command's report."""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")
pytest.importorskip("pillow_heif")

from lifeos.ml import bench

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="needs Tesseract (tesseract-ocr-fra)"
)


def test_edit_distance_and_character_error_rate() -> None:
    assert bench.levenshtein("", "abc") == 3
    assert bench.levenshtein("chat", "chats") == 1
    assert bench.levenshtein("kitten", "sitting") == 3
    assert bench.cer("TOTAL  6,43\n", "TOTAL 6,43") == 0.0  # whitespace collapsed
    assert bench.cer("TOTAL 6,48", "TOTAL 6,43") == pytest.approx(0.1)
    assert bench.cer("", "") == 0.0
    assert bench.cer("bruit", "") == 1.0


def test_fields_are_checked_with_the_receipt_parser() -> None:
    text = "\n".join(bench.SYNTHETIC_RECEIPT)
    checks = bench.field_checks(text, bench.SYNTHETIC_ANNOTATION, date(2026, 10, 30))
    assert checks == {"total": True, "date": True, "merchant": True}
    wrong = bench.field_checks(
        text.replace("6,43", "6,48"),
        {"total": "6.43", "merchant": "carrefour market"},
        date(2026, 10, 30),
    )
    assert wrong == {"total": False, "merchant": True}
    assert bench.field_checks(text, {"total": "pas un nombre"}, date(2026, 10, 30)) == {}


def test_synthetic_corpus_is_annotated(tmp_path: Path) -> None:
    written = bench.synthetic_corpus(tmp_path)
    assert len(written) >= 15
    for path in written:
        annotation = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        assert annotation["total"] == "6.43"
    assert bench.corpus_files(tmp_path) == sorted(written)


@needs_tesseract
def test_bench_command_reports_each_variant(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bench.synthetic_corpus(tmp_path)
    keep = {"upright.png", "skew+10.png", "turned90.jpg"}
    for path in tmp_path.iterdir():
        if path.with_suffix(".png").name not in keep and path.with_suffix(".jpg").name not in keep:
            path.unlink()
    assert bench.main([str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["files"] == 3
    for variant in ("baseline", "preprocess"):
        numbers = report["variants"][variant]
        for key in ("accuracy", "cer", "confidence", "seconds", "fields", "peak_mb"):
            assert key in numbers
        assert isinstance(numbers["peak_mb"], int)
    assert report["variants"]["preprocess"]["accuracy"] > report["variants"]["baseline"]["accuracy"]
