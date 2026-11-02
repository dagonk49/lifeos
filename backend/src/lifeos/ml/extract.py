"""Local text extraction in the `ml` service (F10, ADR 0008).

Untrusted files are parsed here, in the sandboxed `ml` container (no route to the
outside world, read-only, unprivileged), never in the API:
- PDF: text layer read with PDFium; pages without text (scans) are rendered and OCRed;
- images: decoded by Pillow (only the decoder of the type detected by signature),
  orientation applied (EXIF, or else Tesseract's orientation detection, in quarter turns),
  re-encoded from the pixels alone (EXIF, GPS, XMP, ICC... dropped), then OCRed. The
  re-encoded picture replaces the upload;
- OCR: Tesseract 5 with the French model, in a subprocess with a time limit. A skewed or
  poorly read picture gets a second pass on a cleaned-up copy (`preprocess`: straightened,
  lighting evened out), kept only when clearly better (`_read`).

Nothing is stored and no content is logged. The HTTP contract is described in
`lifeos.modules.documents.extraction`.
"""

import csv
import io
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from lifeos import isolation
from lifeos.ml import preprocess

register_heif_opener()

ALLOWED_MIMES = ("application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic")
# Decoder allowed for each type: Pillow never guesses the format from the content.
PIL_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/heic": "HEIF"}
# Decompression bomb guard, checked on the header before any pixel is decoded.
MAX_PIXELS = 50_000_000
# A progressive JPEG is decoded scan by scan, each one going over the whole picture: a few
# kilobytes of tiny scans make a CPU bomb. Real pictures have about ten (libjpeg-turbo
# advises refusing more than a few hundred); markers of an embedded thumbnail count too.
MAX_JPEG_SCANS = 500
# Stored and OCRed pictures are at most this large (a 12 Mpx phone photo is 4032 px).
MAX_IMAGE_SIDE = 4096
OCR_DPI = 300
MAX_RENDER_SIDE = 4000
# A PDF page with less text than this is treated as a scan and OCRed.
MIN_PAGE_CHARS = 20
# Small pictures (screenshots, crops) are enlarged before OCR: Tesseract reads best at
# about 30 px per capital letter.
MIN_OCR_SIDE = 1500
# Time budget of one document (the API waits longer, queueing included): a document that
# cannot be read in time fails with `too_slow` instead of being retried forever.
DEADLINE_S = 240.0
OCR_LANGUAGE = "fra"
# Tesseract page segmentation: automatic for documents, one column of variable-size text
# for receipts (narrow tickets, prices aligned to the right).
PSM_DOCUMENT = 3
PSM_RECEIPT = 4
JPEG_QUALITY = 90
EXIF_ORIENTATION = 0x0112
# Orientation detection (Tesseract OSD, `tesseract-ocr-osd`): run only when the plain
# reading is poor, never when the EXIF tag said how the camera was held. Its confidence was
# 3.4 to 4.1 on the synthetic receipts turned a quarter or upside down (and 4.1 upright, for
# 0°): a turn is tried only above this, and kept only when it reads clearly better.
OSD_MIN_CONFIDENCE = 2.0
# OSD reads a copy at most this large (it only needs the letters' shapes) within this time.
OSD_SIDE = 2000
OSD_TIMEOUT_S = 30.0
# Orientation detection and the cleaned-up pass (`_read`) are tried when the plain
# reading's mean confidence is under this (clean synthetic receipts read at 0.86 to 0.95,
# the web warns under 0.6)...
RETRY_CONFIDENCE = 0.75
# ... kept only when its confidence is at least this much higher (ties keep the first), and
# when it keeps this share of the first reading's text.
MIN_GAIN = 0.03
MIN_KEPT_TEXT = 0.5
# A page whose lines slope by this much is always tried straightened. Tesseract still reads
# 2-3° right, but from about 5° it starts to split a receipt into columns (prices torn from
# their labels), while its word confidence stays high: on the synthetic receipts at 5-7°,
# the straightened reading found every field and the plain one often missed the total,
# with a confidence up to 0.07 LOWER for the straightened one (resampling). So for such a
# page the straightened reading is kept unless clearly worse.
SKEW_PREFER_DEG = 4.0
SKEW_TOLERANCE = 0.10
# Time left over twice the first pass, plus this, to try the second one.
RETRY_MARGIN_S = 5.0

# Memory of one extraction (child process): a hostile file (huge image inside a small PDF)
# fails with `too_large` instead of exhausting the service, which also holds the embedding
# model.
DEFAULT_MEMORY_MB = 1280

# Checked by `_decode_image` on the header, with its own error (instead of a warning).
Image.MAX_IMAGE_PIXELS = None
# PDFium is not thread-safe: one document at a time in this process.
_pdfium_lock = threading.Lock()


class ExtractFailure(Exception):  # noqa: N818  (mirrors UnreadableDocument on the API side)
    """The file cannot be processed. `status` 422: not retried; 503: retried later."""

    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        # Peak memory of the isolated extraction (MiB), when known: logged, never content.
        self.peak_mb: int | None = None
        # The signal that ended the child (crash or kill), when that is what happened.
        self.signal: int | None = None


@dataclass(frozen=True)
class OcrText:
    text: str
    # Mean word confidence in [0, 1]; None when no word was recognised.
    confidence: float | None


@dataclass(frozen=True)
class Reading:
    """How a picture was read (no content): returned to the API, logged, kept in
    `Document.analysis["reading"]`, so a result can be explained afterwards."""

    rotation: int = 0  # clockwise quarter turn applied (orientation detection), degrees
    skew: float = 0.0  # degrees straightened in the kept pass (0.0: not straightened)
    enhanced: bool = False  # the kept pass used the cleaned-up picture
    passes: int = 1  # OCR passes run (2: the cleaned-up picture was tried)

    @property
    def kept(self) -> str:
        return "b" if self.enhanced else "a"

    def as_dict(self) -> dict[str, Any]:
        return {
            "rotation": self.rotation,
            "skew": self.skew,
            "enhanced": self.enhanced,
            "pass": self.kept,
        }


@dataclass(frozen=True)
class Result:
    text: str
    source: str  # "pdf" | "ocr"
    pages: int
    confidence: float | None = None
    sanitized: bytes | None = None
    sanitized_mime: str | None = None
    # Peak memory of the isolated extraction (MiB), set by `run_isolated`.
    peak_mb: int | None = None
    # How the picture(s) were read (OCR only; None for a text layer).
    reading: Reading | None = None


# --- OCR -----------------------------------------------------------------------------------


def tesseract_path() -> str | None:
    return shutil.which("tesseract")


def _tsv_text(tsv: str) -> OcrText:
    """Rebuilds the text from Tesseract's TSV output: words joined into lines, blocks
    separated by a blank line; confidence is the mean over recognised words."""
    lines: dict[tuple[int, int, int, int], list[str]] = {}
    confidences: list[float] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        word = (row.get("text") or "").strip()
        if row.get("level") != "5" or not word:
            continue
        try:
            key = (
                int(row["page_num"]),
                int(row["block_num"]),
                int(row["par_num"]),
                int(row["line_num"]),
            )
            word_confidence = float(row["conf"])
        except (KeyError, TypeError, ValueError):
            continue
        lines.setdefault(key, []).append(word)
        if word_confidence >= 0:
            confidences.append(word_confidence)
    parts: list[str] = []
    previous_block: tuple[int, int] | None = None
    for key in sorted(lines):
        block = (key[0], key[1])
        if previous_block is not None and block != previous_block:
            parts.append("")
        parts.append(" ".join(lines[key]))
        previous_block = block
    mean = sum(confidences) / len(confidences) / 100 if confidences else None
    return OcrText("\n".join(parts), mean)


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise ExtractFailure("too_slow")
    return left


def _prepare_gray(image: Image.Image) -> Image.Image:
    """The picture as Tesseract reads it: transparency on white, greyscale, small pictures
    enlarged."""
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        # Transparent pixels are read as black: dark text would vanish into its background.
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, (255, 255, 255))
        image.paste(rgba, mask=rgba.getchannel("A"))
    gray = image.convert("L")
    if max(gray.size) < MIN_OCR_SIDE:
        factor = min(3.0, MIN_OCR_SIDE / max(1, max(gray.size)))
        size = (round(gray.width * factor), round(gray.height * factor))
        gray = gray.resize(size, Image.Resampling.LANCZOS)
    return gray


def _png(gray: Image.Image) -> bytes:
    buffer = io.BytesIO()
    gray.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def _tesseract(png: bytes, args: list[str], timeout: float) -> str:
    """Runs Tesseract on a PNG (stdin) and returns its output. Shared by OCR and the
    orientation detection: same binary, environment, time limit and error codes."""
    binary = tesseract_path()
    if binary is None:
        raise ExtractFailure("ocr_unavailable", status=503)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        # One OCR at a time already; more threads only add contention on 4 cores.
        "OMP_THREAD_LIMIT": os.environ.get("LIFEOS_OCR_THREADS", "2"),
    }
    if "TESSDATA_PREFIX" in os.environ:
        environment["TESSDATA_PREFIX"] = os.environ["TESSDATA_PREFIX"]
    try:
        completed = subprocess.run(  # noqa: S603  (fixed binary, no shell, no user arguments)
            [binary, "stdin", "stdout", *args],
            input=png,
            capture_output=True,
            timeout=timeout,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractFailure("too_slow") from exc
    except OSError as exc:
        raise ExtractFailure("ocr_unavailable", status=503) from exc
    if completed.returncode != 0:
        # Missing language data or a broken install: the service is unusable, not the file.
        raise ExtractFailure("ocr_unavailable", status=503)
    return completed.stdout.decode("utf-8", errors="replace")


def _ocr_gray(gray: Image.Image, *, psm: int, deadline: float, dpi: int | None) -> OcrText:
    args = ["-l", OCR_LANGUAGE, "--psm", str(psm)]
    if dpi is not None:
        args += ["--dpi", str(dpi)]
    args.append("tsv")
    return _tsv_text(_tesseract(_png(gray), args, _remaining(deadline)))


def ocr(image: Image.Image, *, psm: int, deadline: float, dpi: int | None = None) -> OcrText:
    """One plain OCR pass (no orientation detection, no clean-up)."""
    if tesseract_path() is None:
        raise ExtractFailure("ocr_unavailable", status=503)
    _remaining(deadline)
    return _ocr_gray(_prepare_gray(image), psm=psm, deadline=deadline, dpi=dpi)


def _osd_value(output: str, key: str) -> str | None:
    for line in output.splitlines():
        name, _, value = line.partition(":")
        if name.strip() == key:
            return value.strip()
    return None


def detect_rotation(gray: Image.Image, deadline: float) -> tuple[int, float]:
    """Tesseract's orientation detection (`--psm 0`, osd.traineddata): the clockwise
    rotation (0, 90, 180 or 270) that puts the text upright, and its confidence. Any
    failure (too little text, data missing, slow) means no rotation: (0, 0.0)."""
    if tesseract_path() is None:
        return 0, 0.0
    small = gray
    if max(gray.size) > OSD_SIDE:
        small = gray.copy()
        small.thumbnail((OSD_SIDE, OSD_SIDE), Image.Resampling.BILINEAR)
    timeout = min(_remaining(deadline), OSD_TIMEOUT_S)
    try:
        output = _tesseract(_png(small), ["--psm", "0", "-l", "osd"], timeout)
    except ExtractFailure:
        return 0, 0.0
    rotate, confidence = _osd_value(output, "Rotate"), _osd_value(output, "Orientation confidence")
    try:
        degrees, score = int(rotate or ""), float(confidence or "")
    except ValueError:
        return 0, 0.0
    if degrees not in (0, 90, 180, 270) or not score >= 0:  # also refuses NaN
        return 0, 0.0
    return degrees, score


def _turn(image: Image.Image, clockwise: int) -> Image.Image:
    """Exact quarter turns (no resampling)."""
    steps = {
        90: Image.Transpose.ROTATE_270,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }
    return image.transpose(steps[clockwise]) if clockwise in steps else image


def _confidence(found: OcrText) -> float:
    return found.confidence or 0.0


def _try(gray: Image.Image, *, psm: int, deadline: float, dpi: int | None) -> OcrText | None:
    """An extra pass: None when it runs out of time (the reading already made is kept)."""
    try:
        return _ocr_gray(gray, psm=psm, deadline=deadline, dpi=dpi)
    except ExtractFailure as exc:
        if exc.code != "too_slow":
            raise
        return None


def _read(
    gray: Image.Image,
    *,
    psm: int,
    deadline: float,
    dpi: int | None,
    improve: bool,
    orient: bool,
) -> tuple[OcrText, Reading]:
    """Reads one picture or scanned page.

    1. Pass A: the plain reading, exactly as before preprocessing existed.
    2. Only when A reads poorly (`RETRY_CONFIDENCE`) and `orient`: orientation detection;
       a detected quarter turn is kept only when the turned reading is clearly better.
    3. Pass B, on the cleaned-up picture (`preprocess.prepare`), only when the page is
       clearly skewed or still reads poorly; kept when its confidence is clearly better,
       or, for a skewed page, not clearly worse.

    An upright document that reads well gets none of it: same text, same time. Each extra
    pass needs twice the first one's time left (`RETRY_MARGIN_S`), so it never turns a
    document that reads in time into `too_slow`."""
    started = time.monotonic()
    first = _ocr_gray(gray, psm=psm, deadline=deadline, dpi=dpi)
    if not improve:
        return first, Reading()
    spent = time.monotonic() - started

    def time_left() -> bool:
        return deadline - time.monotonic() >= spent * 2 + RETRY_MARGIN_S

    passes, rotation = 1, 0
    if orient and _confidence(first) < RETRY_CONFIDENCE and time_left():
        detected = _upright(gray, deadline)
        if detected:
            turned_gray = _turn(gray, detected)
            turned = _try(turned_gray, psm=psm, deadline=deadline, dpi=dpi)
            passes += 1
            if turned is not None and _confidence(turned) >= _confidence(first) + MIN_GAIN:
                gray, first, rotation = turned_gray, turned, detected
    skew = preprocess.estimate_skew(gray)
    skewed = abs(skew) >= SKEW_PREFER_DEG
    if (not skewed and _confidence(first) >= RETRY_CONFIDENCE) or not time_left():
        return first, Reading(rotation=rotation, passes=passes)
    prepared, applied = preprocess.prepare(gray, skew)
    second = _try(prepared, psm=psm, deadline=deadline, dpi=dpi)
    passes += 1
    if second is None:
        return first, Reading(rotation=rotation, passes=passes)
    gain = _confidence(second) - _confidence(first)
    better = gain >= MIN_GAIN or (skewed and applied.skew != 0.0 and gain >= -SKEW_TOLERANCE)
    # Mean confidence over fewer words can rise by dropping the hard ones: the cleaned-up
    # reading must also keep most of the text.
    if better and len(second.text.strip()) >= len(first.text.strip()) * MIN_KEPT_TEXT:
        return second, Reading(rotation=rotation, skew=applied.skew, enhanced=True, passes=passes)
    return first, Reading(rotation=rotation, passes=passes)


def _upright(gray: Image.Image, deadline: float) -> int:
    """The quarter turn (clockwise) detected with enough confidence, else 0."""
    rotation, confidence = detect_rotation(gray, deadline)
    return rotation if confidence >= OSD_MIN_CONFIDENCE else 0


# --- Images --------------------------------------------------------------------------------


def _fit(image: Image.Image, max_side: int) -> Image.Image:
    if max(image.size) <= max_side:
        return image
    copy = image.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return copy


def _decode_image(data: bytes, mime: str) -> tuple[Image.Image, bool]:
    """The first picture, EXIF orientation applied; True when the EXIF tag turned it (the
    camera knew how it was held: orientation detection is then not needed)."""
    # Start-of-scan markers (0xFFDA cannot occur inside entropy-coded data, where 0xFF bytes
    # are stuffed).
    if mime == "image/jpeg" and data.count(b"\xff\xda") > MAX_JPEG_SCANS:
        raise ExtractFailure("unreadable")
    try:
        image = Image.open(io.BytesIO(data), formats=[PIL_FORMATS[mime]])
        if mime == "image/jpeg" and max(image.size) > MAX_IMAGE_SIDE:
            # JPEG decodes at 1/2, 1/4 or 1/8 scale: a 50-200 Mpx phone photo is read
            # directly at about the size kept, without allocating the full picture.
            # (The requested box keeps the aspect ratio: Pillow scales by the smaller ratio.)
            factor = MAX_IMAGE_SIDE / max(image.size)
            image.draft(None, (int(image.width * factor), int(image.height * factor)))
        if image.width * image.height > MAX_PIXELS:
            raise ExtractFailure("image_too_large")
        image.seek(0)  # animations, bursts, HEIF collections: the first picture only
        image.load()
        tagged = image.getexif().get(EXIF_ORIENTATION, 1) in range(2, 9)
        # Applies the EXIF orientation to the pixels (the tag itself is dropped below).
        oriented = ImageOps.exif_transpose(image)
    except ExtractFailure:
        raise
    except (UnidentifiedImageError, OSError, ValueError, EOFError, SyntaxError) as exc:
        raise ExtractFailure("unreadable") from exc
    return oriented, tagged


def _pixels_only(image: Image.Image, *, keep_alpha: bool) -> Image.Image:
    """A new picture made of the pixels alone: no EXIF, GPS, XMP, ICC profile or comment."""
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha and keep_alpha:
        source = image.convert("RGBA")
    elif has_alpha:
        rgba = image.convert("RGBA")
        source = Image.new("RGB", image.size, (255, 255, 255))
        source.paste(rgba, mask=rgba.getchannel("A"))
    elif image.mode.startswith("I"):
        # 16-bit greyscale: scaled down, not clipped (a plain conversion would turn it white).
        source = image.convert("I").point(lambda value: value * (1 / 256)).convert("L")
    elif image.mode in ("L", "1"):
        source = image.convert("L")
    else:
        source = image.convert("RGB")
    return Image.frombytes(source.mode, source.size, source.tobytes())


def sanitize_image(image: Image.Image, mime: str) -> tuple[bytes, str]:
    """Re-encodes the picture. HEIC becomes JPEG: browsers cannot display it."""
    buffer = io.BytesIO()
    if mime == "image/png":
        _pixels_only(image, keep_alpha=True).save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), "image/png"
    if mime == "image/webp":
        _pixels_only(image, keep_alpha=True).save(buffer, format="WEBP", quality=JPEG_QUALITY)
        return buffer.getvalue(), "image/webp"
    _pixels_only(image, keep_alpha=False).save(
        buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True
    )
    return buffer.getvalue(), "image/jpeg"


def extract_image(
    data: bytes, mime: str, *, purpose: str, deadline: float, improve: bool = True
) -> Result:
    """`improve` False: the plain reading only (no orientation detection, no second
    pass), as before preprocessing existed; `lifeos-ml bench` compares both."""
    decoded, tagged = _decode_image(data, mime)
    image = _fit(decoded, MAX_IMAGE_SIDE)
    psm = PSM_RECEIPT if purpose == "receipt" else PSM_DOCUMENT
    # Screenshots, scans and some phones carry no EXIF orientation: the text itself tells
    # which way is up. The tag, when present, is trusted.
    found, reading = _read(
        _prepare_gray(image),
        psm=psm,
        deadline=deadline,
        dpi=None,
        improve=improve,
        orient=not tagged,
    )
    # Quarter turns only, never the deskew warp: the stored picture is upright, as the
    # owner would have turned it, and otherwise untouched.
    sanitized, sanitized_mime = sanitize_image(_turn(image, reading.rotation), mime)
    return Result(
        text=found.text,
        source="ocr",
        pages=1,
        confidence=found.confidence if found.confidence is not None else 0.0,
        sanitized=sanitized,
        sanitized_mime=sanitized_mime,
        reading=reading,
    )


# --- PDF -----------------------------------------------------------------------------------


def _page_text(page: pdfium.PdfPage) -> str:
    textpage = page.get_textpage()
    try:
        text: str = textpage.get_text_bounded()
    finally:
        textpage.close()
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _check_images(page: pdfium.PdfPage) -> None:
    """Refuses a page holding an image too large to decode (a few kB of compressed zeros
    can describe gigabytes of pixels), before PDFium decodes it to render the page."""
    for obj in page.get_objects(filter=(pdfium.raw.FPDF_PAGEOBJ_IMAGE,)):
        width, height = obj.get_px_size()
        if width * height > MAX_PIXELS:
            raise ExtractFailure("image_too_large")


def _render(page: pdfium.PdfPage) -> Image.Image:
    _check_images(page)
    width, height = page.get_size()  # points (1/72 inch)
    scale = min(OCR_DPI / 72, MAX_RENDER_SIDE / max(1.0, width, height))
    bitmap = page.render(scale=scale, grayscale=True)
    try:
        image: Image.Image = bitmap.to_pil()
        return image.copy()  # detached from PDFium's buffer
    finally:
        bitmap.close()


def _summary(readings: list[Reading]) -> Reading | None:
    """One record for a scanned PDF: the first quarter turn, the largest straightening,
    whether any page kept its cleaned-up reading, the most passes."""
    if not readings:
        return None
    return Reading(
        rotation=next((r.rotation for r in readings if r.rotation), 0),
        skew=max((r.skew for r in readings), key=abs),
        enhanced=any(r.enhanced for r in readings),
        passes=max(r.passes for r in readings),
    )


def extract_pdf(
    data: bytes, *, purpose: str, max_pages: int, deadline: float, improve: bool = True
) -> Result:
    with _pdfium_lock:
        try:
            document = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            code = "encrypted" if "password" in str(exc).lower() else "unreadable"
            raise ExtractFailure(code) from exc
        try:
            count = len(document)
            if count == 0:
                raise ExtractFailure("unreadable")
            if count > max_pages:
                raise ExtractFailure("too_many_pages")
            texts: list[str] = []
            confidences: list[float] = []
            readings: list[Reading] = []
            for index in range(count):
                page = document[index]
                try:
                    text = _page_text(page)
                    if len(text) < MIN_PAGE_CHARS:
                        _remaining(deadline)
                        found, reading = _read(
                            _prepare_gray(_render(page)),
                            psm=PSM_RECEIPT if purpose == "receipt" else PSM_DOCUMENT,
                            deadline=deadline,
                            dpi=OCR_DPI,
                            improve=improve,
                            # A scanner or a scanning app may store a page sideways.
                            orient=True,
                        )
                        # A page that has a little text and no readable picture keeps it.
                        if len(found.text.strip()) > len(text):
                            text = found.text
                            confidences.append(found.confidence or 0.0)
                            readings.append(reading)
                finally:
                    page.close()
                texts.append(text)
        except pdfium.PdfiumError as exc:
            raise ExtractFailure("unreadable") from exc
        finally:
            document.close()
    scanned = bool(confidences)
    return Result(
        text="\f".join(texts),
        source="ocr" if scanned else "pdf",
        pages=count,
        confidence=sum(confidences) / len(confidences) if scanned else None,
        reading=_summary(readings),
    )


def extract(
    data: bytes,
    mime: str,
    *,
    purpose: str,
    max_pages: int,
    budget_s: float = DEADLINE_S,
    improve: bool = True,
) -> Result:
    """Blocking (CPU-bound): run it in a worker thread."""
    if mime not in ALLOWED_MIMES:
        raise ExtractFailure("unsupported_type", status=415)
    deadline = time.monotonic() + budget_s
    if mime == "application/pdf":
        return extract_pdf(
            data, purpose=purpose, max_pages=max_pages, deadline=deadline, improve=improve
        )
    return extract_image(data, mime, purpose=purpose, deadline=deadline, improve=improve)


# --- Isolation -----------------------------------------------------------------------------
# Each extraction runs in a confined child process (`lifeos.isolation`): the decoders (PDFium,
# libheif, libjpeg...) parse untrusted files, and the memory limit (not only the pixel checks
# above, which cannot see everything, e.g. a PDF soft mask) keeps one file from exhausting
# the service, which also holds the embedding model. The fork server preloads this module: a
# child starts in milliseconds.
isolation.preload(__name__)


def _extract_message(
    data: bytes, mime: str, purpose: str, max_pages: int, improve: bool = True
) -> tuple[Any, ...]:
    """Runs in the child: the outcome as a picklable message."""
    try:
        return ("ok", extract(data, mime, purpose=purpose, max_pages=max_pages, improve=improve))
    except ExtractFailure as exc:
        return ("fail", exc.code, exc.status)
    except MemoryError:
        return ("fail", "too_large", 422)
    except Exception:  # a decoder bug on this file: not worth retrying
        return ("fail", "unreadable", 422)


def run_isolated(
    data: bytes,
    mime: str,
    *,
    purpose: str,
    max_pages: int,
    memory_mb: int = DEFAULT_MEMORY_MB,
    timeout_s: float = DEADLINE_S + 30,
    improve: bool = True,
) -> Result:
    """`extract` in a child process limited to `memory_mb`. Blocking: run it in a thread.

    Out of its memory, or crashed by the file, the child fails it with `too_large` (422:
    final). Killed (SIGKILL), it fails with `extraction_crashed` and status 500: the API
    retries a few times before giving up (`MAX_FAILURES`), and never drops the original for
    it. The kernel's OOM killer picks this child first when the container runs short,
    perhaps during a peak of the embedding model: the same file then reads fine a little
    later.

    Failures carry the child's peak memory (`peak_mb`) when it answered."""
    try:
        answer = isolation.run(
            _extract_message,
            (data, mime, purpose, max_pages, improve),
            memory_mb=memory_mb,
            timeout_s=timeout_s,
        )
    except isolation.ChildTimeoutError as exc:  # stuck in a decoder: killed
        raise ExtractFailure("too_slow") from exc
    except isolation.ChildDiedError as exc:
        if exc.signal != signal.SIGKILL:
            # Out of its own memory (a native decoder aborts when its allocation fails under
            # the limit) or crashed by this file: final.
            raise ExtractFailure("too_large") from exc
        # SIGKILL: the OOM killer (or an operator), not the file.
        crashed = ExtractFailure("extraction_crashed", status=500)
        crashed.signal = exc.signal
        raise crashed from exc
    message = answer.value
    if message[0] == "ok":
        result: Result = message[1]
        return replace(result, peak_mb=answer.peak_mb)
    failure = ExtractFailure(message[1], status=message[2])
    failure.peak_mb = answer.peak_mb
    raise failure
