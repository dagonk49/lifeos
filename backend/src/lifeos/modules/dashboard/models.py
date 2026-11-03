"""The owner's home page layout (task 23). Keep in sync with migration 0021 and
docs/modele-de-donnees.md.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base

VIEWS = ("day", "week")
WEEK_STARTS = ("monday", "today")


class DashboardPreference(Base):
    """Which cards are shown, in which order, and the default view. No row: defaults
    (dashboard.service.DEFAULT_CARDS). Cards missing from the stored list are appended with
    their defaults when read, so a new card is never lost to an older layout."""

    __tablename__ = "dashboard_preferences"
    __table_args__ = (
        CheckConstraint("default_view IN ('day', 'week')", name="default_view"),
        CheckConstraint("week_start IN ('monday', 'today')", name="week_start"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # [{"id": "<card id>", "visible": true}], in display order.
    cards: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    default_view: Mapped[str] = mapped_column(Text, default="day", server_default="day")
    # The week view starts on Monday (as in France) or on today.
    week_start: Mapped[str] = mapped_column(Text, default="monday", server_default="monday")
    updated_at: Mapped[datetime]
