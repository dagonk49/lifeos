import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey, search_vector_column

FACT_KINDS = ("fact", "event", "preference", "decision")
# Separates verifiable facts, model inferences and declared preferences.
ORIGINS = ("user_declared", "user_confirmed", "extracted", "inferred")


class Fact(UUIDPrimaryKey, Owned, Timestamps, Base):
    __tablename__ = "facts"
    __table_args__ = (
        CheckConstraint("kind IN ('fact', 'event', 'preference', 'decision')", name="kind"),
        CheckConstraint(
            "origin IN ('user_declared', 'user_confirmed', 'extracted', 'inferred')",
            name="origin",
        ),
        CheckConstraint("status IN ('active', 'superseded', 'retracted')", name="status"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence"),
        Index("ix_facts_search_vector", "search_vector", postgresql_using="gin"),
    )

    kind: Mapped[str] = mapped_column(Text)
    statement: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    status: Mapped[str] = mapped_column(Text, default="active", server_default="active")
    occurred_at: Mapped[datetime | None] = mapped_column(index=True)
    source_type: Mapped[str] = mapped_column(Text)
    source_id: Mapped[uuid.UUID | None]
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("facts.id", ondelete="SET NULL")
    )
    # Last explicit confirmation by the owner: preferences age from this date.
    last_confirmed_at: Mapped[datetime | None]
    search_vector: Mapped[Any] = search_vector_column("statement")


class FactPairDismissal(Owned, Base):
    """« Keep both »: a pair of facts the owner reviewed and does not want flagged again."""

    __tablename__ = "fact_pair_dismissals"
    __table_args__ = (CheckConstraint("fact_a < fact_b", name="ordered"),)

    fact_a: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True
    )
    fact_b: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime]


memory_facts = Table(
    "memory_facts",
    Base.metadata,
    Column("memory_id", ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True),
    Column("fact_id", ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True),
)


class Memory(UUIDPrimaryKey, Owned, Timestamps, Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("importance BETWEEN 1 AND 5", name="importance"),
        CheckConstraint(
            "origin IN ('user_declared', 'user_confirmed', 'extracted', 'inferred')",
            name="origin",
        ),
        Index("ix_memories_search_vector", "search_vector", postgresql_using="gin"),
    )

    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(SmallInteger, default=3, server_default="3")
    # Protected memories are never purged automatically.
    protected: Mapped[bool] = mapped_column(default=False, server_default="false")
    origin: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, default="manual", server_default="manual")
    source_id: Mapped[uuid.UUID | None]
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    search_vector: Mapped[Any] = search_vector_column(
        "coalesce(title, '') || ' ' || coalesce(content, '')"
    )


class MemoryRevision(UUIDPrimaryKey, Base):
    """Correction history: the state of a memory before each change."""

    __tablename__ = "memory_revisions"
    __table_args__ = (UniqueConstraint("memory_id", "revision", name="uq_memory_revisions"),)

    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(SmallInteger)
    protected: Mapped[bool]
    change_note: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime]
