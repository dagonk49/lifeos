"""Plain text out of hostile mail parts (task 17, threats T10, T14, T17).

A message is written by anyone. LifeOS never stores nor renders its HTML: an HTML part is
reduced to text by the standard library's parser, which executes nothing and fetches
nothing. Scripts, styles, comments, images (tracking pixels) and embedded objects are
dropped; a link keeps its text and the bare domain it points to (never a `javascript:`
or `data:` target). Headers are decoded (RFC 2047), parts with their declared charset.

Every output is bounded, on one line where it is a header, without control characters
nor the invisible format characters that reorder or hide text (bidirectional overrides,
zero-width characters).
"""

import codecs
import re
import unicodedata
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.utils import getaddresses
from html.parser import HTMLParser
from urllib.parse import urlsplit

# A body is kept as plain text up to this length (about 20 KB): enough for a long letter.
BODY_CHARS = 20_000
# HTML parsed at most (pure Python, linear): beyond, the rest of the part is ignored.
HTML_INPUT_CHARS = 1_000_000
SUBJECT_CHARS = 300
NAME_CHARS = 200
ADDRESS_CHARS = 320
MAX_ADDRESSES = 50
FILENAME_CHARS = 200

# Elements whose content is never text for the reader.
_DROPPED = frozenset(
    {
        "script",
        "style",
        "head",
        "title",
        "noscript",
        "template",
        "svg",
        "math",
        "iframe",
        "object",
        "embed",
        "frameset",
        "select",
        "button",
        "canvas",
        "video",
        "audio",
    }
)
_BLOCKS = frozenset(
    {
        "p",
        "div",
        "br",
        "tr",
        "table",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "section",
        "article",
        "header",
        "footer",
        "pre",
        "hr",
        "dl",
        "dt",
        "dd",
        "center",
        "address",
    }
)
_SPACES = re.compile("[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _visible(char: str) -> bool:
    if char in "\n\t":
        return True
    # Cc: control characters; Cf: format characters (bidi overrides, zero width...);
    # Co/Cs: private use and lone surrogates.
    return unicodedata.category(char) not in ("Cc", "Cf", "Co", "Cs")


def clean_text(text: str, limit: int = BODY_CHARS) -> tuple[str, bool]:
    """Normalised plain text, and whether it was cut at `limit` characters."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if _visible(c))
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    if len(text) > limit:
        return text[:limit].rstrip(), True
    return text, False


def one_line(value: str, limit: int) -> str:
    """A header value on one line, cleaned and bounded."""
    text = "".join(c if _visible(c) else " " for c in value).replace("\n", " ")
    return " ".join(text.replace("\t", " ").split())[:limit]


def decode_header_value(value: str) -> str:
    """An RFC 2047 encoded header (=?charset?B|Q?...?=) as text; the raw value if broken."""
    if "=?" not in value:
        return value
    try:
        return str(make_header(decode_header(value)))
    except (HeaderParseError, LookupError, UnicodeDecodeError, ValueError):
        return value


def subject(value: str | None) -> str:
    return one_line(decode_header_value(value or ""), SUBJECT_CHARS)


def addresses(value: str | None) -> list[tuple[str | None, str]]:
    """(display name, address) pairs of an address header, bounded."""
    if not value:
        return []
    found: list[tuple[str | None, str]] = []
    for name, address in getaddresses([value[: MAX_ADDRESSES * (ADDRESS_CHARS + NAME_CHARS)]]):
        address = one_line(address, ADDRESS_CHARS).lower()
        if "@" not in address:
            continue
        display = one_line(decode_header_value(name), NAME_CHARS) if name else ""
        found.append((display or None, address))
        if len(found) >= MAX_ADDRESSES:
            break
    return found


def filename(value: str | None) -> str:
    """An attachment's name, without any path, on one line."""
    name = decode_header_value(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    return one_line(name, FILENAME_CHARS) or "pièce jointe"


def decode_bytes(data: bytes, charset: str | None) -> str:
    """A part's bytes as text with its declared charset (UTF-8 when unknown or not a
    text encoding); undecodable bytes become U+FFFD."""
    name = (charset or "utf-8").strip().strip('"').lower()[:40] or "utf-8"
    try:
        if not codecs.lookup(name)._is_text_encoding:
            name = "utf-8"
    except LookupError:
        name = "utf-8"
    try:
        return data.decode(name, errors="replace")
    except (LookupError, ValueError):
        return data.decode("utf-8", errors="replace")


def _domain(href: str) -> str | None:
    try:
        parts = urlsplit(href.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname.lower().removeprefix("www.")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._dropped = 0
        self._link: tuple[str | None, int] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROPPED:
            self._dropped += 1
            return
        if self._dropped:
            return
        if tag in _BLOCKS:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in ("td", "th"):
            self.parts.append(" ")
        elif tag == "a":
            href = next((v for k, v in attrs if k == "href" and v), None)
            self._link = (_domain(href) if href else None, len(self.parts))
        # img, input, link, meta...: nothing (no alt text: a tracking pixel says nothing).

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROPPED:
            return  # <svg/>: no content to drop
        self.handle_starttag(tag, attrs)
        if tag == "a":
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROPPED:
            self._dropped = max(0, self._dropped - 1)
            return
        if self._dropped:
            return
        if tag == "a" and self._link is not None:
            domain, start = self._link
            self._link = None
            text = "".join(self.parts[start:])
            if domain and domain not in text.lower():
                self.parts.append(f" ({domain})")
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._dropped:
            self.parts.append(data)

    # Comments, declarations and processing instructions are never text.
    def handle_comment(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


def html_to_text(html: str) -> str:
    """The text a reader would see, without anything active."""
    parser = _TextExtractor()
    try:
        parser.feed(html[:HTML_INPUT_CHARS])
        parser.close()
    except (AssertionError, ValueError):
        pass  # malformed beyond repair: what was read so far
    return "".join(parser.parts)
