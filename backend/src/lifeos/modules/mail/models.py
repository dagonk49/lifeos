"""Local copies of the e-mails of the labels the owner chose (task 17, ADR 0017).

Keep in sync with migration 0018 and docs/modele-de-donnees.md.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey


class MailMessage(UUIDPrimaryKey, Owned, Timestamps, Base):
    """One message of a mapped label, in the space of that label (its source) only.

    The same message in two labels mapped to two spaces is two rows, one per space: a
    message never crosses a space. Unique per source and provider id. Its texts are plain
    text written by third parties: data, never instructions (T10). Deleted with its source
    (cascade), when it disappears from the label at the provider, and `purge_after`.
    """

    __tablename__ = "mail_messages"
    __table_args__ = (
        UniqueConstraint("source_id", "provider_message_id", name="uq_mail_messages_provider"),
        Index("ix_mail_messages_space_received", "space_id", "received_at"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_sources.id", ondelete="CASCADE"), index=True
    )
    provider_message_id: Mapped[str] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)
    from_address: Mapped[str | None] = mapped_column(Text)
    from_name: Mapped[str | None] = mapped_column(Text)
    to_addresses: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    cc_addresses: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    subject: Mapped[str] = mapped_column(Text, default="")
    # Plain text only (HTML converted without rendering), at most about 20 KB.
    body_text: Mapped[str] = mapped_column(Text, default="")
    body_truncated: Mapped[bool] = mapped_column(default=False)
    received_at: Mapped[datetime]
    # [{part_id, filename, mime, size, document_id?}]: metadata only; a file is
    # downloaded only when the owner imports it (document_id then set).
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # Detected locally (identity, health, bank): never sent to the cloud.
    sensitivity: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    purge_after: Mapped[datetime] = mapped_column(index=True)
    # Last full pass that saw it: after a complete one, older ones left the label.
    seen_generation: Mapped[int] = mapped_column(default=0)
