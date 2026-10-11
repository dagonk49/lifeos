"""File type detection by signature (allowlist), never by name or declared type.

Only formats LifeOS can process safely are accepted; everything else (executables,
archives, HTML/SVG, Office macros...) is refused before being stored.
"""

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
_MP4_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"mp42", b"isom", b"iso5", b"iso6", b"dash", b"mp41"}


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
    lowered = text.lstrip().lower()
    # Markup could be rendered by a browser: refused as "text".
    if lowered.startswith(("<!doctype", "<html", "<svg", "<?xml", "<script")):
        return False
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
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
