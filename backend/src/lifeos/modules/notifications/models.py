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


class PushDevice(UUIDPrimaryKey, Base):
    """A browser subscribed to Web Push (ADR 0019). `endpoint`, `p256dh` and `auth_secret`
    form a capability: whoever holds them can send to the phone. They are never logged,
    audited nor exported."""

    __tablename__ = "push_devices"
    __table_args__ = (CheckConstraint("preview IN ('generic', 'full')", name="preview"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The session (and its passkey) that registered it: revoking them revokes the device.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="SET NULL"), index=True
    )
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("passkey_credentials.id", ondelete="SET NULL"), index=True
    )
    label: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)
    auth_secret: Mapped[str] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    # What the lock screen shows: a generic text, or the notification's title.
    preview: Mapped[str] = mapped_column(Text, default="generic", server_default="generic")
    consented_at: Mapped[datetime]
    last_success_at: Mapped[datetime | None]
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    revoked_at: Mapped[datetime | None]
    # user, session, passkey, logout, others, all, expired, account.
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]


class PushDelivery(UUIDPrimaryKey, Base):
    """One notification for one device: the per-device sent marker (at most one row per
    pair). `notification_id` NULL: a test push asked from the settings."""

    __tablename__ = "push_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'expired', 'cancelled')", name="status"
        ),
        UniqueConstraint("notification_id", "device_id", name="uq_push_deliveries_pair"),
    )

    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE")
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("push_devices.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(Text, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Next try; while a send is in flight, the end of its lease.
    next_attempt_at: Mapped[datetime] = mapped_column(index=True)
    sent_at: Mapped[datetime | None]
    # HTTP status or failure kind (http_429, timeout...), never a response body.
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(index=True)
