import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey, search_vector_column

DOCUMENT_KINDS = ("image", "pdf", "text", "audio")
DOCUMENT_STATUSES = ("pending", "processing", "ready", "failed", "rejected")
# Why the document was added: drives its processing (receipt parsing, transcription).
DOCUMENT_PURPOSES = ("document", "receipt", "attachment", "dictation")
# Detected locally (F10); a sensitive document's content never goes to the cloud.
SENSITIVITY_CATEGORIES = ("identity", "health", "bank")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Document(UUIDPrimaryKey, Owned, Timestamps, Base):
    """An uploaded file: encrypted original (optional, time-limited) + extracted text."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(_in("kind", DOCUMENT_KINDS), name="kind"),
        CheckConstraint(_in("status", DOCUMENT_STATUSES), name="status"),
        CheckConstraint(_in("purpose", DOCUMENT_PURPOSES), name="purpose"),
        CheckConstraint("size_bytes >= 0", name="size"),
        Index("ix_documents_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_documents_source", "source_type", "source_id"),
    )

    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text, index=True)
    purpose: Mapped[str] = mapped_column(Text, default="document", server_default="document")
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    # Consecutive times the reading (or transcription) service failed on this document
    # (crash, hang): after a few, the document fails instead of being retried forever.
    failures: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    sensitivity: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    # « Toujours garder en local »: the owner's own refusal, on top of the detection. Never
    # changed by a new reading, a correction or a new classification.
    local_only: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Extracted (or transcribed) text, correctable by the owner.
    extracted_text: Mapped[str | None] = mapped_column(Text)
    text_source: Mapped[str | None] = mapped_column(Text)
    text_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    text_corrected_at: Mapped[datetime | None]
    pages: Mapped[int | None] = mapped_column(Integer)
    # Structured, purpose-specific result (e.g. the receipt proposal), never authoritative.
    analysis: Mapped[dict[str, Any] | None]
    processed_at: Mapped[datetime | None]
    # Encrypted original on disk; None once purged (or never kept).
    storage_key: Mapped[str | None] = mapped_column(Text)
    # Purge date of the original; None while it is kept indefinitely (owner's choice).
    original_until: Mapped[datetime | None] = mapped_column(index=True)
    # Provenance: where the document comes from or what it is attached to.
    source_type: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid.UUID | None]
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    search_vector: Mapped[Any] = search_vector_column(
        "coalesce(title, '') || ' ' || coalesce(extracted_text, '')"
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
