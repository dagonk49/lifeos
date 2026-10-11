from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey


class Review(UUIDPrimaryKey, Owned, Timestamps, Base):
    """Daily, weekly or monthly review (F05).

    `facts` are computed locally and deterministically; `narrative` is the assistant's
    reading of those facts. They are stored and shown separately: facts are never mixed
    with interpretations.
    """

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("period IN ('daily', 'weekly', 'monthly')", name="period"),
        CheckConstraint(
            "narrative_status IN ('none', 'generated', 'unavailable')", name="narrative_status"
        ),
        UniqueConstraint("user_id", "space_id", "period", "period_start", name="uq_reviews_period"),
    )

    period: Mapped[str] = mapped_column(Text)
    period_start: Mapped[date]
    period_end: Mapped[date]
    facts: Mapped[dict[str, Any]]
    narrative: Mapped[dict[str, Any] | None]
    narrative_status: Mapped[str] = mapped_column(Text, default="none", server_default="none")
    narrative_error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime]
