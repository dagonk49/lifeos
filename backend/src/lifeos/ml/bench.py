"""`lifeos-ml bench`: measures how well pictures are read, before and after preprocessing
(F10, UC02, ADR 0008).

    lifeos-ml bench <dir> [--variant baseline|preprocess|both] [--json] [--verbose]
    lifeos-ml bench <dir> --synthetic   # writes a generated corpus into <dir> first

The corpus is a directory of pictures (or PDFs), each with a sibling `<name>.json`:
`{"total": "6.43", "date": "2026-10-10", "merchant": "CARREFOUR MARKET", "text": "..."}`
(every key optional). For each variant, every file goes through the same confined
extraction as the service (`run_isolated`), then:

- field accuracy: the receipt parser (`parse_receipt`) finds the annotated total, date and
  merchant;
- CER: character error rate against `text` (Levenshtein, whitespace collapsed);
- mean OCR confidence, seconds, peak memory, and how often each step was used.

Only aggregates are printed; `--verbose` adds one line per file (names and scores, never
the text) on the local terminal. A real corpus is personal data: mount it read-only
(`docker compose run --rm -v /chemin/corpus:/corpus:ro ml lifeos-ml bench /corpus`) and
keep it off the repository. Nothing leaves the machine.
"""

import argparse
import json
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from lifeos.ml.extract import ExtractFailure, run_isolated
from lifeos.modules.documents.sniff import sniff
from lifeos.modules.finance.receipts import parse_receipt

VARIANTS = ("baseline", "preprocess")
FIELDS = ("total", "date", "merchant")
EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".heic", ".pdf")

# --- Metrics -------------------------------------------------------------------------------


def levenshtein(a: str, b: str) -> int:
    """Edit distance (insertions, deletions, substitutions), two rows of memory."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        current = [i]
        for j, right in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return previous[-1]


def _collapse(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def cer(hypothesis: str, reference: str) -> float:
    """Character error rate: edits needed per reference character (whitespace collapsed).
    Can exceed 1 when the reading adds much noise."""
    expected, found = _collapse(reference), _collapse(hypothesis)
    if not expected:
        return 0.0 if not found else 1.0
    return levenshtein(found, expected) / len(expected)


def _same_text(a: str, b: str) -> bool:
    def norm(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())

    return norm(a) == norm(b)


def field_checks(text: str, annotation: dict[str, Any], today: date) -> dict[str, bool]:
    """For each annotated field, whether the receipt parser finds it in `text`."""
    proposal = parse_receipt(text, today)
    checks: dict[str, bool] = {}
    if isinstance(annotation.get("total"), str | int | float):
        try:
            expected = Decimal(str(annotation["total"]))
        except InvalidOperation:
            expected = None
        if expected is not None:
            checks["total"] = proposal.total is not None and proposal.total.value == expected
    if isinstance(annotation.get("date"), str):
        try:
            expected_date = date.fromisoformat(annotation["date"])
        except ValueError:
            expected_date = None
        if expected_date is not None:
            checks["date"] = (
                proposal.tx_date is not None and proposal.tx_date.value == expected_date
            )
    if isinstance(annotation.get("merchant"), str):
        checks["merchant"] = proposal.merchant is not None and _same_text(
            proposal.merchant.value, annotation["merchant"]
        )
    return checks


# --- Run -----------------------------------------------------------------------------------


@dataclass
class FileScore:
    name: str
    checks: dict[str, bool] = field(default_factory=dict)
    cer: float | None = None
    confidence: float | None = None
    seconds: float = 0.0
    peak_mb: int | None = None
    error: str | None = None
    rotation: int = 0
    skew: float = 0.0
    enhanced: bool = False
    passes: int = 1


def corpus_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in EXTENSIONS and not path.name.startswith(".")
    )


def _annotation(path: Path) -> dict[str, Any]:
    sidecar = path.with_suffix(".json")
    if not sidecar.is_file():
        return {}
    try:
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def score_file(path: Path, *, improve: bool, purpose: str, today: date) -> FileScore:
    score = FileScore(name=path.name)
    data = path.read_bytes()
    detected = sniff(data)
    if detected is None or detected.kind not in ("image", "pdf"):
        score.error = "unsupported_type"
        return score
    annotation = _annotation(path)
    started = time.monotonic()
    try:
        # The same confined child as the service: a corpus file is parsed like an upload.
        result = run_isolated(data, detected.mime, purpose=purpose, max_pages=30, improve=improve)
    except ExtractFailure as exc:
        score.error = exc.code
        score.seconds = time.monotonic() - started
        return score
    score.seconds = time.monotonic() - started
    score.peak_mb = result.peak_mb
    score.confidence = result.confidence
    if result.reading is not None:
        score.rotation = result.reading.rotation
        score.skew = result.reading.skew
        score.enhanced = result.reading.enhanced
        score.passes = result.reading.passes
    score.checks = field_checks(result.text, annotation, today)
    if isinstance(annotation.get("text"), str):
        score.cer = cer(result.text, annotation["text"])
    return score


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def aggregate(scores: list[FileScore]) -> dict[str, Any]:
    checks = [ok for score in scores for ok in score.checks.values()]
    per_field = {
        name: _mean([float(s.checks[name]) for s in scores if name in s.checks]) for name in FIELDS
    }
    errors: dict[str, int] = {}
    for score in scores:
        if score.error:
            errors[score.error] = errors.get(score.error, 0) + 1
    seconds = [score.seconds for score in scores]
    peaks = [score.peak_mb for score in scores if score.peak_mb is not None]
    return {
        "files": len(scores),
        "accuracy": _mean([float(ok) for ok in checks]),
        "fields": per_field,
        "cer": _mean([s.cer for s in scores if s.cer is not None]),
        "confidence": _mean([s.confidence for s in scores if s.confidence is not None]),
        "seconds": round(sum(seconds), 2),
        "seconds_mean": _mean(seconds),
        "seconds_max": round(max(seconds), 2) if seconds else None,
        "peak_mb": max(peaks) if peaks else None,
        "rotated": sum(1 for s in scores if s.rotation),
        "second_pass": sum(1 for s in scores if s.passes > 1),
        "enhanced": sum(1 for s in scores if s.enhanced),
        "errors": errors,
    }


def run_bench(
    directory: Path,
    variants: tuple[str, ...] = VARIANTS,
    *,
    purpose: str = "receipt",
    today: date | None = None,
    on_file: Any = None,
) -> dict[str, Any]:
    files = corpus_files(directory)
    today = today or date.today()
    report: dict[str, Any] = {"files": len(files), "purpose": purpose, "variants": {}}
    for variant in variants:
        scores = []
        for path in files:
            score = score_file(path, improve=variant == "preprocess", purpose=purpose, today=today)
            scores.append(score)
            if on_file is not None:
                on_file(variant, score)
        report["variants"][variant] = aggregate(scores)
    return report


# --- Synthetic corpus ----------------------------------------------------------------------

SYNTHETIC_RECEIPT = [
    "CARREFOUR MARKET",
    "12 rue de la Paix 75002 Paris",
    "Le 10/10/2026 a 18:42",
    "PAIN COMPLET          2,15",
    "LAIT DEMI ECREME      1,29",
    "POMMES 1KG            2,99",
    "TOTAL TTC             6,43",
    "CB                    6,43",
]
SYNTHETIC_ANNOTATION = {
    "total": "6.43",
    "date": "2026-10-10",
    "merchant": "CARREFOUR MARKET",
    "text": "\n".join(SYNTHETIC_RECEIPT),
}


def render_receipt(lines: list[str] | None = None, *, size: int = 36) -> Image.Image:
    """The receipt as a clean picture (Pillow's built-in font, black on white)."""
    lines = lines or SYNTHETIC_RECEIPT
    font = ImageFont.load_default(size=size)
    image = Image.new("RGB", (900, (size + 14) * len(lines) + 80), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((40, 40 + index * (size + 14)), line, fill="black", font=font)
    return image


def _perspective(image: Image.Image, squeeze: float = 0.12) -> Image.Image:
    """The receipt photographed from below: the top edge narrower than the bottom one."""
    width, height = image.size
    canvas = Image.new("RGB", (width + 200, height + 200), "white")
    canvas.paste(image, (100, 100))
    w, h = canvas.size
    inset = w * squeeze
    # Where each corner of the output comes from in the input (top-left, top-right,
    # bottom-right, bottom-left): solves the 8 perspective coefficients.
    target = [(0, 0), (w, 0), (w, h), (0, h)]
    source = [(-inset, 0), (w + inset, 0), (w, h), (0, h)]
    rows = []
    values = []
    for (x, y), (u, v) in zip(target, source, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values += [u, v]
    coefficients = np.linalg.solve(np.array(rows, dtype=float), np.array(values, dtype=float))
    return canvas.transform(
        canvas.size,
        Image.Transform.PERSPECTIVE,
        tuple(float(c) for c in coefficients),
        resample=Image.Resampling.BICUBIC,
        fillcolor="white",
    )


def _lighting(image: Image.Image, darkest: float = 0.35) -> Image.Image:
    """A lamp on one side: the paper fades to grey on the other."""
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32)
    gradient = np.linspace(1.0, darkest, pixels.shape[1], dtype=np.float32)
    shaded = pixels * gradient[np.newaxis, :, np.newaxis]
    return Image.fromarray(shaded.clip(0, 255).astype(np.uint8), mode="RGB")


def _save(image: Image.Image, path: Path, **params: Any) -> None:
    fmt = "JPEG" if path.suffix == ".jpg" else "PNG"
    image.save(path, format=fmt, **params)
    path.with_suffix(".json").write_text(
        json.dumps(SYNTHETIC_ANNOTATION, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def synthetic_corpus(out_dir: Path) -> list[Path]:
    """Generated receipts, one per kind of difficulty (no EXIF tag on any of them): upright,
    skewed by ±2/5/10°, turned a quarter or upside down, photographed at an angle, blurred,
    unevenly lit, heavily compressed. Synthetic: no personal data."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base = render_receipt()
    cases: dict[str, Image.Image] = {"upright.png": base}
    for angle in (-10, -5, -2, 2, 5, 10):
        cases[f"skew{angle:+d}.png"] = base.rotate(
            angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white"
        )
    for quarter in (90, 180, 270):
        cases[f"turned{quarter}.jpg"] = base.rotate(quarter, expand=True)
    cases["perspective.png"] = _perspective(base)
    cases["perspective-strong.png"] = _perspective(base, 0.4)
    cases["blur.png"] = base.filter(ImageFilter.GaussianBlur(2.2))
    cases["blur-strong.png"] = base.filter(ImageFilter.GaussianBlur(3.5))
    cases["lighting.png"] = _lighting(base, 0.2)
    for angle in (5, 7):
        cases[f"skew{angle}-lighting.jpg"] = _lighting(
            base.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
        )
    cases["jpeg40.jpg"] = base
    written = []
    for name, image in cases.items():
        path = out_dir / name
        _save(image, path, **({"quality": 40} if name == "jpeg40.jpg" else {}))
        written.append(path)
    return written


# --- Command -------------------------------------------------------------------------------


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f} %"


def _summary(report: dict[str, Any]) -> str:
    lines = [f"{report['files']} fichiers ({report['purpose']})"]
    for variant, numbers in report["variants"].items():
        fields = ", ".join(f"{k} {_percent(v)}" for k, v in numbers["fields"].items())
        lines.append(
            f"- {variant} : champs justes {_percent(numbers['accuracy'])} ({fields}) ; "
            f"CER {_percent(numbers['cer'])} ; confiance {_percent(numbers['confidence'])} ; "
            f"{numbers['seconds']} s (max {numbers['seconds_max']} s, "
            f"pic {numbers['peak_mb']} Mo) ; tournés {numbers['rotated']}, "
            f"2e passe {numbers['second_pass']}, gardée {numbers['enhanced']} ; "
            f"échecs {numbers['errors'] or 0}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lifeos-ml bench")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--variant", choices=[*VARIANTS, "both"], default="both")
    parser.add_argument("--purpose", choices=["receipt", "document"], default="receipt")
    parser.add_argument("--json", action="store_true", help="the report as JSON")
    parser.add_argument("--verbose", action="store_true", help="one line per file")
    parser.add_argument(
        "--synthetic", action="store_true", help="write a generated corpus into the directory"
    )
    args = parser.parse_args(argv)
    if args.synthetic:
        synthetic_corpus(args.directory)
    if not args.directory.is_dir():
        print(f"dossier introuvable : {args.directory}", file=sys.stderr)
        return 2
    variants = VARIANTS if args.variant == "both" else (args.variant,)

    def verbose(variant: str, score: FileScore) -> None:
        checks = " ".join(f"{k}={'ok' if v else 'KO'}" for k, v in score.checks.items())
        print(
            f"{variant:<10} {score.name:<28} {checks} "
            f"cer={'—' if score.cer is None else round(score.cer, 3)} "
            f"conf={'—' if score.confidence is None else round(score.confidence, 3)} "
            f"{score.seconds:.2f}s rot={score.rotation} skew={score.skew} "
            f"pass={'b' if score.enhanced else 'a'}/{score.passes}"
            + (f" erreur={score.error}" if score.error else ""),
            file=sys.stderr,
        )

    report = run_bench(
        args.directory, variants, purpose=args.purpose, on_file=verbose if args.verbose else None
    )
    print(json.dumps(report, ensure_ascii=False) if args.json else _summary(report))
    return 0
