"""Active content of a PDF received from a third party (ADR 0011 §6): a warning, never a
refusal. PDFium executes none of it, but the owner may open the original elsewhere.

The PDF's names are read in the file and in its compressed streams (object streams hide
the dictionaries), with escaped names (`/J#61vaScript`) decoded. Bounded: a few thousand
streams, a few dozen MB once inflated; what lies beyond is not looked at.
"""

import re
import zlib

# Name of the PDF key -> stable code shown to the owner.
ACTIVE_NAMES: dict[bytes, str] = {
    b"JavaScript": "javascript",
    b"JS": "javascript",
    b"OpenAction": "open_action",
    b"AA": "automatic_action",
    b"Launch": "launch",
    b"EmbeddedFile": "embedded_file",
    b"EmbeddedFiles": "embedded_file",
    b"RichMedia": "rich_media",
    b"XFA": "xfa_form",
}
CODES: tuple[str, ...] = (
    "javascript",
    "open_action",
    "automatic_action",
    "launch",
    "embedded_file",
    "rich_media",
    "xfa_form",
)

_NAME = re.compile(rb"/((?:[^\s/\[\]<>(){}%#\x00]|#[0-9A-Fa-f]{2}){1,64})")
_HEX = re.compile(rb"#([0-9A-Fa-f]{2})")
_STREAM = re.compile(rb"stream\r?\n")
MAX_STREAMS = 2_000
MAX_INFLATED = 32 * 1024 * 1024
MAX_STREAM_INFLATED = 8 * 1024 * 1024


def _names(data: bytes, found: set[str]) -> None:
    for match in _NAME.finditer(data):
        raw = match.group(1)
        name = _HEX.sub(lambda m: bytes([int(m.group(1), 16)]), raw) if b"#" in raw else raw
        code = ACTIVE_NAMES.get(name)
        if code:
            found.add(code)


def active_content(data: bytes) -> list[str]:
    """Codes of the active content found, in a stable order (empty: none found)."""
    found: set[str] = set()
    _names(data, found)
    budget = MAX_INFLATED
    for count, match in enumerate(_STREAM.finditer(data)):
        if count >= MAX_STREAMS or budget <= 0:
            break
        end = data.find(b"endstream", match.end())
        if end < 0:
            break
        inflater = zlib.decompressobj()
        try:
            inflated = inflater.decompress(
                data[match.end() : end], min(MAX_STREAM_INFLATED, budget)
            )
        except zlib.error:
            continue  # not deflated (images, other filters): nothing to read
        budget -= len(inflated)
        _names(inflated, found)
    return [code for code in CODES if code in found]
