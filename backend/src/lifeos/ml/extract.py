"""Local text extraction in the `ml` service (F10, ADR 0008).

Untrusted files are parsed here, in the sandboxed `ml` container (no route to the
outside world, read-only, unprivileged), never in the API:
- PDF: text layer read with PDFium; pages without text (scans) are rendered and OCRed;
- images: decoded by Pillow (only the decoder of the type detected by signature),
  orientation applied, re-encoded from the pixels alone (EXIF, GPS, XMP, ICC... dropped),
  then OCRed. The re-encoded picture replaces the upload;
- OCR: Tesseract 5 with the French model, in a subprocess with a time limit.

Nothing is stored and no content is logged. The HTTP contract is described in
`lifeos.modules.documents.extraction`.
"""

import csv
import io
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()

ALLOWED_MIMES = ("application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic")
# Decoder allowed for each type: Pillow never guesses the format from the content.
PIL_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/heic": "HEIF"}
# Decompression bomb guard, checked on the header before any pixel is decoded.
MAX_PIXELS = 50_000_000
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


@dataclass(frozen=True)
class OcrText:
    text: str
    # Mean word confidence in [0, 1]; None when no word was recognised.
    confidence: float | None


@dataclass(frozen=True)
class Result:
    text: str
    source: str  # "pdf" | "ocr"
    pages: int
    confidence: float | None = None
    sanitized: bytes | None = None
    sanitized_mime: str | None = None


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


def ocr(image: Image.Image, *, psm: int, deadline: float, dpi: int | None = None) -> OcrText:
    binary = tesseract_path()
    if binary is None:
        raise ExtractFailure("ocr_unavailable", status=503)
    timeout = _remaining(deadline)
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
    buffer = io.BytesIO()
    gray.save(buffer, format="PNG", compress_level=1)
    command = [binary, "stdin", "stdout", "-l", OCR_LANGUAGE, "--psm", str(psm)]
    if dpi is not None:
        command += ["--dpi", str(dpi)]
    command.append("tsv")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        # One OCR at a time already; more threads only add contention on 4 cores.
        "OMP_THREAD_LIMIT": os.environ.get("LIFEOS_OCR_THREADS", "2"),
    }
    if "TESSDATA_PREFIX" in os.environ:
        environment["TESSDATA_PREFIX"] = os.environ["TESSDATA_PREFIX"]
    try:
        completed = subprocess.run(  # noqa: S603  (fixed binary, no shell, no user arguments)
            command,
            input=buffer.getvalue(),
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
    return _tsv_text(completed.stdout.decode("utf-8", errors="replace"))


# --- Images --------------------------------------------------------------------------------


def _fit(image: Image.Image, max_side: int) -> Image.Image:
    if max(image.size) <= max_side:
        return image
    copy = image.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return copy


def _decode_image(data: bytes, mime: str) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data), formats=[PIL_FORMATS[mime]])
        if image.width * image.height > MAX_PIXELS:
            raise ExtractFailure("image_too_large")
        image.seek(0)  # animations, bursts, HEIF collections: the first picture only
        image.load()
        # Applies the EXIF orientation to the pixels (the tag itself is dropped below).
        oriented = ImageOps.exif_transpose(image)
    except ExtractFailure:
        raise
    except (UnidentifiedImageError, OSError, ValueError, EOFError, SyntaxError) as exc:
        raise ExtractFailure("unreadable") from exc
    return oriented


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


def extract_image(data: bytes, mime: str, *, purpose: str, deadline: float) -> Result:
    image = _fit(_decode_image(data, mime), MAX_IMAGE_SIDE)
    sanitized, sanitized_mime = sanitize_image(image, mime)
    found = ocr(image, psm=PSM_RECEIPT if purpose == "receipt" else PSM_DOCUMENT, deadline=deadline)
    return Result(
        text=found.text,
        source="ocr",
        pages=1,
        confidence=found.confidence if found.confidence is not None else 0.0,
        sanitized=sanitized,
        sanitized_mime=sanitized_mime,
    )


# --- PDF -----------------------------------------------------------------------------------


def _page_text(page: pdfium.PdfPage) -> str:
    textpage = page.get_textpage()
    try:
        text: str = textpage.get_text_bounded()
    finally:
        textpage.close()
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _render(page: pdfium.PdfPage) -> Image.Image:
    width, height = page.get_size()  # points (1/72 inch)
    scale = min(OCR_DPI / 72, MAX_RENDER_SIDE / max(1.0, width, height))
    bitmap = page.render(scale=scale, grayscale=True)
    try:
        image: Image.Image = bitmap.to_pil()
        return image.copy()  # detached from PDFium's buffer
    finally:
        bitmap.close()


def extract_pdf(data: bytes, *, purpose: str, max_pages: int, deadline: float) -> Result:
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
            for index in range(count):
                page = document[index]
                try:
                    text = _page_text(page)
                    if len(text) < MIN_PAGE_CHARS:
                        _remaining(deadline)
                        found = ocr(
                            _render(page),
                            psm=PSM_RECEIPT if purpose == "receipt" else PSM_DOCUMENT,
                            deadline=deadline,
                            dpi=OCR_DPI,
                        )
                        # A page that has a little text and no readable picture keeps it.
                        if len(found.text.strip()) > len(text):
                            text = found.text
                            confidences.append(found.confidence or 0.0)
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
    )


def extract(
    data: bytes, mime: str, *, purpose: str, max_pages: int, budget_s: float = DEADLINE_S
) -> Result:
    """Blocking (CPU-bound): run it in a worker thread."""
    if mime not in ALLOWED_MIMES:
        raise ExtractFailure("unsupported_type", status=415)
    deadline = time.monotonic() + budget_s
    if mime == "application/pdf":
        return extract_pdf(data, purpose=purpose, max_pages=max_pages, deadline=deadline)
    return extract_image(data, mime, purpose=purpose, deadline=deadline)
