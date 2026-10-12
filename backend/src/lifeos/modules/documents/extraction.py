"""Local text extraction and OCR clients (F10).

Extraction always runs on the server: plain text is decoded in-process, images and PDFs go
to the internal `ml` service (no route to the outside world). Document content is never
sent to the cloud.

Contract of the ml service, `POST {ml_url}/extract` (multipart/form-data):

Request fields:
- `file`: the decrypted original (bytes), with the detected media type as part type;
- `mime`: the type detected by signature (`sniff`), one of `application/pdf`, `image/png`,
  `image/jpeg`, `image/webp`, `image/heic`;
- `purpose`: `document`, `receipt` or `attachment` (lets the service tune OCR, e.g. a
  receipt is a narrow, single column ticket);
- `max_pages`: the service refuses longer PDFs (422 `too_many_pages`) instead of
  truncating them.

Responses:
- `200` JSON `{"text", "source", "pages", "confidence", "sanitized_b64", "sanitized_mime"}`:
  - `text` (str): extracted text, reading order, pages separated by a form feed;
  - `source`: `"pdf"` (text layer of a PDF) or `"ocr"` (image, or scanned PDF);
  - `pages` (int | null): page count (1 for an image);
  - `confidence` (float in [0, 1] | null): mean OCR confidence, null for a text layer;
  - `sanitized_b64` (str | null): images only, the picture re-encoded from its pixels
    (orientation applied, every metadata block such as EXIF/GPS dropped), base64; the API
    stores it INSTEAD of the upload;
  - `sanitized_mime` (`image/png` | `image/jpeg` | `image/webp` | null).
- `422` JSON `{"code": "unreadable" | "too_many_pages" | "encrypted" | ...}`: the file
  cannot be read; it is not retried.
- `413` (too large) and `415` (unsupported type): not retried either.
- `503`, any other error, timeout: extraction unavailable, retried later.

The service stores nothing and never logs content.
"""

import base64
import binascii
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from lifeos.config import Settings
from lifeos.modules.documents.sniff import sniff

TextSource = Literal["pdf", "ocr", "text"]
SANITIZED_MIMES = ("image/png", "image/jpeg", "image/webp")
# The ml service queues a request up to 90 s, then reads it within 240 s.
TIMEOUT_S = 360.0


@dataclass(frozen=True)
class Extraction:
    text: str
    source: TextSource
    pages: int | None = None
    confidence: float | None = None
    # Re-encoded image without metadata, stored instead of the upload.
    sanitized: bytes | None = None
    sanitized_mime: str | None = None


class ExtractionError(Exception):
    """Extraction is unavailable right now (service down, timeout): retry later."""


class ExtractionCrashError(ExtractionError):
    """The service failed while reading THIS document (connection lost, hang, server
    error): retried, but the document fails after a few such failures in a row."""


class UnreadableDocument(Exception):  # noqa: N818  (name used by the ml contract)
    """The document cannot be read (corrupt, encrypted, too many pages): do not retry."""

    def __init__(self, code: str = "unreadable") -> None:
        super().__init__(code)
        self.code = code


class Extractor(Protocol):
    name: str

    async def extract(
        self, data: bytes, mime: str, *, purpose: str, max_pages: int
    ) -> Extraction: ...


def decode_text(data: bytes) -> Extraction:
    """Plain text files (already checked by `sniff`): decoded in-process, never sent."""
    text = data.decode("utf-8", errors="replace").removeprefix("﻿")
    return Extraction(text=text, source="text")


def _source_for(mime: str) -> TextSource:
    return "pdf" if mime == "application/pdf" else "ocr"


# --- Fake (tests, offline development) -----------------------------------------------------

FakeHandler = Callable[[bytes, str, str], Extraction]


class FakeExtractor:
    """Deterministic extractor: text files are decoded; images and PDFs return `text`,
    or the text registered for their SHA-256 in `texts`, or what `handler` returns
    (a handler may also raise ExtractionError or UnreadableDocument)."""

    name = "fake"

    def __init__(
        self,
        text: str = "",
        *,
        texts: dict[str, str] | None = None,
        handler: FakeHandler | None = None,
        pages: int = 1,
    ) -> None:
        self.text = text
        self.texts = dict(texts or {})
        self.handler = handler
        self.pages = pages
        self.calls = 0

    def register(self, data: bytes, text: str) -> None:
        self.texts[hashlib.sha256(data).hexdigest()] = text

    async def extract(self, data: bytes, mime: str, *, purpose: str, max_pages: int) -> Extraction:
        self.calls += 1
        if mime == "text/plain":
            return decode_text(data)
        if self.handler is not None:
            return self.handler(data, mime, purpose)
        text = self.texts.get(hashlib.sha256(data).hexdigest(), self.text)
        return Extraction(
            text=text,
            source=_source_for(mime),
            pages=self.pages,
            confidence=0.9 if text else None,
        )


# --- Remote (the internal `ml` service) ----------------------------------------------------


def _sanitized(payload: dict[str, Any]) -> tuple[bytes | None, str | None]:
    encoded, mime = payload.get("sanitized_b64"), payload.get("sanitized_mime")
    if encoded is None:
        return None, None
    if not isinstance(encoded, str) or mime not in SANITIZED_MIMES:
        raise ExtractionError("ml service returned a malformed sanitized image")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ExtractionError("ml service returned a malformed sanitized image") from exc
    detected = sniff(data)
    # Only a re-encoded picture of an allowed type may replace the upload.
    if detected is None or detected.mime != mime:
        raise ExtractionError("ml service returned a sanitized file of another type")
    return data, mime


def parse_response(payload: Any) -> Extraction:
    """Validates the ml service's answer: nothing it returns is trusted blindly."""
    if not isinstance(payload, dict):
        raise ExtractionError("ml service returned a malformed answer")
    text, source = payload.get("text"), payload.get("source")
    pages, confidence = payload.get("pages"), payload.get("confidence")
    if not isinstance(text, str) or source not in ("pdf", "ocr", "text"):
        raise ExtractionError("ml service returned a malformed answer")
    if pages is not None and (not isinstance(pages, int) or isinstance(pages, bool) or pages < 0):
        raise ExtractionError("ml service returned a malformed page count")
    if confidence is not None:
        if not isinstance(confidence, int | float) or isinstance(confidence, bool):
            raise ExtractionError("ml service returned a malformed confidence")
        confidence = min(1.0, max(0.0, float(confidence)))
    sanitized, sanitized_mime = _sanitized(payload)
    return Extraction(
        text=text,
        source=source,
        pages=pages,
        confidence=confidence,
        sanitized=sanitized,
        sanitized_mime=sanitized_mime,
    )


def _error_code(response: httpx.Response) -> str:
    try:
        code = response.json().get("code")
    except (ValueError, AttributeError):
        code = None
    if isinstance(code, str) and code.isascii() and 0 < len(code) <= 40:
        return code
    return "unreadable"


class RemoteExtractor:
    name = "remote"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._url = settings.ml_url.rstrip("/") + "/extract"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=3.0))

    async def extract(self, data: bytes, mime: str, *, purpose: str, max_pages: int) -> Extraction:
        if mime == "text/plain":
            return decode_text(data)
        try:
            response = await self._client.post(
                self._url,
                files={"file": ("document", data, mime)},
                data={"mime": mime, "purpose": purpose, "max_pages": str(max_pages)},
                timeout=TIMEOUT_S,
            )
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout) as exc:
            # The request reached the service, which died or hung while reading the file.
            raise ExtractionCrashError(f"ml service failed: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise ExtractionError(f"ml service unavailable: {type(exc).__name__}") from exc
        if response.status_code == 422:
            raise UnreadableDocument(_error_code(response))
        if response.status_code == 413:
            raise UnreadableDocument("too_large")
        if response.status_code == 415:
            raise UnreadableDocument("unsupported_type")
        if response.status_code == 500:
            raise ExtractionCrashError("ml service failed on this document")
        if response.status_code != 200:
            raise ExtractionError(f"ml service answered {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExtractionError("ml service returned invalid JSON") from exc
        return parse_response(payload)


def build_extractor(settings: Settings) -> Extractor | None:
    if settings.extraction_provider == "fake":
        return FakeExtractor()
    if settings.extraction_provider == "remote":
        return RemoteExtractor(settings)
    # "disabled", and "local", which is reserved for a future in-process extractor: only
    # plain text documents are processed (decoded in-process).
    return None
