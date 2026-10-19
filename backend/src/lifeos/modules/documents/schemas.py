import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# "dictation" documents come from the voice module, never from an upload.
UploadPurpose = Literal["document", "receipt", "attachment"]
# Objects a document may come from or be attached to (provenance).
SourceType = Literal["journal_entry", "message", "conversation", "task", "goal", "transaction"]
# Longest extracted (or corrected) text kept for one document.
MAX_TEXT_CHARS = 200_000


class DocumentUploadFields(BaseModel):
    """Form fields of `POST /v1/documents` (multipart/form-data, next to `file`)."""

    space_id: uuid.UUID
    purpose: UploadPurpose = "document"
    title: str | None = Field(default=None, max_length=200)
    source_type: SourceType | None = None
    source_id: uuid.UUID | None = None
    # False: the encrypted original is purged after the retention period.
    keep_original: bool = False

    @model_validator(mode="after")
    def _source_pair(self) -> "DocumentUploadFields":
        if (self.source_type is None) != (self.source_id is None):
            raise ValueError("source_type and source_id go together")
        return self


class UploadFingerprint(DocumentUploadFields):
    """What an Idempotency-Key is bound to: the fields and the file's digest."""

    sha256: str


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    # Owner's correction of the extracted text.
    extracted_text: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    # True: keep the original indefinitely; False: purge it after the retention period.
    keep_original: bool | None = None
    # « Toujours garder en local »: True is immediate; False (sending the document to the
    # assistant again) is a reinforced operation, and never clears a detected category.
    local_only: bool | None = None


class DocumentSummary(BaseModel):
    id: uuid.UUID
    space_id: uuid.UUID
    title: str
    kind: str
    mime: str
    size_bytes: int
    purpose: str
    status: str
    # Machine-readable reason of a pending (retry) or failed status.
    error: str | None
    sensitivity: list[str]
    # Kept local by the owner, whatever the detection finds.
    local_only: bool
    text_source: str | None
    text_confidence: float | None
    pages: int | None
    processed_at: datetime | None
    text_corrected_at: datetime | None
    original_available: bool
    # Purge date of the original (in the past once purged); None while it is kept.
    original_until: datetime | None
    keep_original: bool
    source_type: str | None
    source_id: uuid.UUID | None
    # Purpose-specific, never authoritative: {"sensitivity_reasons": {...}, "receipt": {...}}.
    analysis: dict[str, Any] | None
    version: int
    created_at: datetime
    updated_at: datetime


class DocumentOut(DocumentSummary):
    extracted_text: str | None
