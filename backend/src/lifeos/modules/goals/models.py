import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey, search_vector_column

GOAL_STATUSES = ("active", "paused", "achieved", "abandoned", "archived")


class Goal(UUIDPrimaryKey, Owned, Timestamps, Base):
    """Measurable (target value) or qualitative (milestones) objective (F06)."""

    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint("kind IN ('measurable', 'qualitative')", name="kind"),
        CheckConstraint(
            "status IN ('active', 'paused', 'achieved', 'abandoned', 'archived')", name="status"
        ),
        CheckConstraint("review_cadence IN ('weekly', 'monthly')", name="review_cadence"),
        Index("ix_goals_search_vector", "search_vector", postgresql_using="gin"),
    )

    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # Why this goal matters: kept to support honest, contextual coaching.
    motivation: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="qualitative", server_default="qualitative")
    metric_unit: Mapped[str | None] = mapped_column(Text)
    start_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    current_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    start_date: Mapped[date | None]
    due_date: Mapped[date | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(Text, default="active", server_default="active")
    review_cadence: Mapped[str] = mapped_column(Text, default="weekly", server_default="weekly")
    achieved_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    search_vector: Mapped[Any] = search_vector_column(
        "coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || coalesce(motivation, '')"
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class GoalMilestone(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "goal_milestones"

    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date | None]
    done_at: Mapped[datetime | None]
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class GoalCheckin(UUIDPrimaryKey, Base):
    """Indicator history: each measured value or progress note, never overwritten."""

    __tablename__ = "goal_checkins"

    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), index=True
    )
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]
