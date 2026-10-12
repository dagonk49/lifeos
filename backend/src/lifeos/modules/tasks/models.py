import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey, search_vector_column

TASK_STATUSES = ("todo", "in_progress", "done", "cancelled")
TASK_PRIORITIES = ("low", "normal", "high")


class Task(UUIDPrimaryKey, Owned, Timestamps, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('todo', 'in_progress', 'done', 'cancelled')", name="status"),
        CheckConstraint("priority IN ('low', 'normal', 'high')", name="priority"),
        Index("ix_tasks_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_tasks_remind_pending", "remind_at", postgresql_where="reminded_at IS NULL"),
    )

    title: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="todo", server_default="todo")
    priority: Mapped[str] = mapped_column(Text, default="normal", server_default="normal")
    due_at: Mapped[datetime | None] = mapped_column(index=True)
    remind_at: Mapped[datetime | None]
    reminded_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    source_type: Mapped[str] = mapped_column(Text, default="manual", server_default="manual")
    source_id: Mapped[uuid.UUID | None]
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goals.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    search_vector: Mapped[Any] = search_vector_column(
        "coalesce(title, '') || ' ' || coalesce(notes, '')"
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
