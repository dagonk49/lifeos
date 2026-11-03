"""Local copies of the e-mails of the labels the owner chose (task 17, ADR 0017), and the
owner's drafts sent under approval (task 18, ADR 0018).

Keep in sync with migrations 0018, 0019 and docs/modele-de-donnees.md.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint, Uuid
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
    # Its RFC 5322 Message-ID, what a reply refers to (In-Reply-To, References).
    message_id_header: Mapped[str | None] = mapped_column(Text)


# draft: being written (or back after a refusal, an expiry, an edit); awaiting_approval: a
# send approval was requested (its state tells whether it is pending, running, uncertain
# or failed); sent: the provider accepted it. Keep in sync with migration 0019 and
# docs/modele-de-donnees.md.
DRAFT_STATUSES = ("draft", "awaiting_approval", "sent")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class MailDraft(UUIDPrimaryKey, Owned, Timestamps, Base):
    """An e-mail written by the owner (or proposed by an agent), sent only after an
    approval showing exactly this content (ADR 0018).

    Editing it bumps `version` and invalidates the pending approval. Once sent, it can no
    longer be edited; it is purged `mail_retention_days` after it was sent.
    """

    __tablename__ = "mail_drafts"
    __table_args__ = (CheckConstraint(_in("status", DRAFT_STATUSES), name="status"),)

    # The account it goes through; None once that account is gone.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connector_accounts.id", ondelete="SET NULL"), index=True
    )
    to_addresses: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    cc_addresses: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    bcc_addresses: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    subject: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    # Documents of the same space, in order.
    attachment_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list)
    # A reply: the message answered, and what threads it (kept if that copy is purged).
    in_reply_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mail_messages.id", ondelete="SET NULL")
    )
    reply_message_id_header: Mapped[str | None] = mapped_column(Text)
    reply_thread_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="draft")
    # The latest send approval (its state: pending, running, uncertain, failed).
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL")
    )
    # Set when sent: the Message-ID LifeOS gave it, and the provider's ids.
    message_id_header: Mapped[str | None] = mapped_column(Text, unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    provider_thread_id: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
