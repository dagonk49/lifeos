"""File type detection by signature (allowlist), never by name or declared type.

Only formats LifeOS can process safely are accepted; everything else (executables,
archives, HTML/SVG, Office macros...) is refused before being stored. The type found here
also imposes the decoder (`ml`, `stt`): a file is never parsed as another format.

A polyglot (a valid PDF or JPEG that is also something else further on) keeps the type of
its signature: images are re-encoded from their pixels, and originals are only ever served
as downloads (`nosniff`, `CSP sandbox`), so the other reading is never used (ADR 0011).
"""

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Detected:
    kind: str  # image | pdf | text | audio
    mime: str
    extension: str


def _ftyp_brand(data: bytes) -> bytes | None:
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return data[8:12]
    return None


_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heim", b"heis"}
# Generic brands too (browsers record into them): a video is accepted as such, but only
# its audio track is ever decoded, with an allowed audio codec (`lifeos.stt.engine`).
_MP4_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"mp42", b"isom", b"iso5", b"iso6", b"dash", b"mp41"}
# A "text" that opens as a web page, a script, an SVG picture or XML (after a byte order
# mark, spaces and HTML comments) is refused. Common fragments stay text: a Markdown note
# often opens with `<!-- markdownlint-disable -->`, `<p align="center">`, `<h1>` or `<b>`,
# and an original is only ever served as a `text/plain` download (`nosniff`, `CSP sandbox`):
# no browser renders it either way.
_MARKUP = re.compile(
    r"<(?:!doctype|\?xml|(?:html|head|body|script|style|iframe|object|embed|svg|math)"
    r"(?=[\s/>]|$))",
    re.IGNORECASE,
)
_LEADING = re.compile(r"(?:[\ufeff \t\n\r\f\v]+|<!--.*?(?:-->|\Z))*", re.DOTALL)


def _looks_like_text(data: bytes) -> bool:
    sample = data[:65536]
    if b"\x00" in sample:
        return False
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut at the end of the sample is fine.
        if exc.start < len(sample) - 4:
            return False
        text = sample[: exc.start].decode("utf-8")
    # A byte order mark (Windows editors) is not content.
    text = text.removeprefix("\ufeff")
    # A web page, a script or XML is refused as "text". (Further in, markup stays text:
    # browsers only sniff the start, and originals are served as `text/plain` downloads.)
    leading = _LEADING.match(text)
    if _MARKUP.match(text, leading.end() if leading else 0):
        return False
    # Spaces of French typography (non-breaking U+00A0, narrow U+202F...) are text too.
    printable = sum(
        1 for c in text if c.isprintable() or c in "\n\r\t" or unicodedata.category(c) == "Zs"
    )
    return not text or printable / len(text) > 0.97


def sniff(data: bytes) -> Detected | None:
    """The detected type of an allowed file, or None (refused)."""
    if data.startswith(b"%PDF-"):
        return Detected("pdf", "application/pdf", "pdf")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return Detected("image", "image/png", "png")
    if data.startswith(b"\xff\xd8\xff"):
        return Detected("image", "image/jpeg", "jpg")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return Detected("image", "image/webp", "webp")
    brand = _ftyp_brand(data)
    if brand in _HEIF_BRANDS:
        return Detected("image", "image/heic", "heic")
    if brand in _MP4_AUDIO_BRANDS:
        return Detected("audio", "audio/mp4", "m4a")
    if data.startswith(b"\x1a\x45\xdf\xa3"):  # EBML: WebM/Matroska (browser recordings)
        return Detected("audio", "audio/webm", "webm")
    if data.startswith(b"OggS"):
        return Detected("audio", "audio/ogg", "ogg")
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return Detected("audio", "audio/wav", "wav")
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return Detected("audio", "audio/mpeg", "mp3")
    if _looks_like_text(data):
        return Detected("text", "text/plain", "txt")
    return None
