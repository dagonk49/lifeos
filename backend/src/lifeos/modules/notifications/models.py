import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
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
    # Non-critical notifications are held until the end of quiet hours.
    deliver_after: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(Text, default="pending")
    source_type: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid.UUID | None]
    read_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
