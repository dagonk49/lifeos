from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Index, Integer, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey, search_vector_column


class JournalEntry(UUIDPrimaryKey, Owned, Timestamps, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint("mood IS NULL OR mood BETWEEN 1 AND 5", name="mood"),
        Index("ix_journal_entries_search_vector", "search_vector", postgresql_using="gin"),
    )

    entry_date: Mapped[date] = mapped_column(index=True)
    content: Mapped[str] = mapped_column(Text)
    # Feelings are only stored when shared voluntarily.
    mood: Mapped[int | None] = mapped_column(SmallInteger)
    analyzed_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    search_vector: Mapped[Any] = search_vector_column("content")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
