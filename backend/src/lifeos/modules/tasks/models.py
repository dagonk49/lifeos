import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey, search_vector_column

TASK_STATUSES = ("todo", "in_progress", "done", "cancelled")
TASK_PRIORITIES = ("low", "normal", "high")
# Classification (F03): free labels, normalised by schemas.normalize_tags.
MAX_TAGS = 10
MAX_TAG_LENGTH = 40


class Task(UUIDPrimaryKey, Owned, Timestamps, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('todo', 'in_progress', 'done', 'cancelled')", name="status"),
        CheckConstraint("priority IN ('low', 'normal', 'high')", name="priority"),
        Index("ix_tasks_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_tasks_remind_pending", "remind_at", postgresql_where="reminded_at IS NULL"),
        Index("ix_tasks_tags", "tags", postgresql_using="gin"),
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
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    search_vector: Mapped[Any] = search_vector_column(
        "coalesce(title, '') || ' ' || coalesce(notes, '')"
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class TaskDependency(Owned, Base):
    """`task_id` cannot move forward before `depends_on_id` is done (or cancelled).

    Both tasks belong to the same space; the graph stays acyclic (checked under a per-user
    advisory lock in service.add_dependency). Deleting either task removes the edge.
    """

    __tablename__ = "task_dependencies"
    __table_args__ = (CheckConstraint("task_id <> depends_on_id", name="not_self"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime]
