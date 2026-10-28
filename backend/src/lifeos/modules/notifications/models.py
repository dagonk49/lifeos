import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, UUIDPrimaryKey


class Notification(UUIDPrimaryKey, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("category IN ('critical', 'important', 'suggestion')", name="category"),
        CheckConstraint("status IN ('pending', 'read', 'dismissed')", name="status"),
        UniqueConstraint("user_id", "dedup_key", name="uq_notifications_dedup"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("spaces.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(Text)
    # Computed by notifications.policy.next_delivery: quiet hours, delivery windows, digest
    # times and the maximum per day (critical alerts: quiet hours only, unless the owner
    # allowed them to bypass them).
    deliver_after: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(Text, default="pending")
    # 'digest' for a group of suggestions (its items point to it through digest_id).
    source_type: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid.UUID | None]
    # The digest this suggestion was delivered in (NULL: not grouped, or not yet).
    digest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="SET NULL"), index=True
    )
    read_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


class NotificationPreference(Base):
    """The owner's rules for one category (F09). No row: notifications.policy.DEFAULTS."""

    __tablename__ = "notification_preferences"
    __table_args__ = (
        CheckConstraint("category IN ('important', 'suggestion')", name="category"),
        CheckConstraint("max_per_day IS NULL OR max_per_day BETWEEN 1 AND 100", name="max_per_day"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    max_per_day: Mapped[int | None] = mapped_column(Integer)
    # important: [{"start": "HH:MM", "end": "HH:MM"}]; empty means any time.
    windows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    # suggestion: ["HH:MM"], the local times of the digests.
    digest_times: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    updated_at: Mapped[datetime]
