import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey, search_vector_column


class Conversation(UUIDPrimaryKey, Owned, Timestamps, Base):
    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(Text)
    last_message_at: Mapped[datetime | None] = mapped_column(index=True)
    # Owner messages up to this instant were consolidated into memory proposals.
    consolidated_until: Mapped[datetime | None]


class Message(UUIDPrimaryKey, Owned, Base):
    """Raw conversation content: purged after 90 days unless explicitly retained."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        CheckConstraint("status IN ('ok', 'error')", name="status"),
        Index("ix_messages_search_vector", "search_vector", postgresql_using="gin"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="ok", server_default="ok")
    model: Mapped[str | None] = mapped_column(Text)
    # Specialised agent that produced an assistant message (None = general assistant).
    agent: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    purge_after: Mapped[datetime | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(index=True)
    search_vector: Mapped[Any] = search_vector_column("content")
