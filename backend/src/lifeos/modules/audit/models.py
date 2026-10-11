import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Identity, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base


class AuditEvent(Base):
    """Append-only (enforced by a database trigger). Never stores content, only identifiers."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'agent', 'system', 'anonymous')", name="actor_type"
        ),
        CheckConstraint("outcome IN ('success', 'denied', 'failure')", name="outcome"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor_type: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, index=True)
    outcome: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    space_id: Mapped[uuid.UUID | None]
    request_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")
