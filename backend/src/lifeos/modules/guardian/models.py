import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, UUIDPrimaryKey

AGENTS = ("core", "planning", "memory", "coaching", "finance", "projects", "documents")
RESOURCES = (
    "tasks",
    "journal",
    "facts",
    "memories",
    "messages",
    "goals",
    "reviews",
    "documents",
    "finances",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AgentPermission(UUIDPrimaryKey, Base):
    """Per agent, space, resource, access and purpose; time-limited and revocable."""

    __tablename__ = "agent_permissions"
    __table_args__ = (
        CheckConstraint(_in("agent", AGENTS), name="agent"),
        CheckConstraint(_in("resource", RESOURCES), name="resource"),
        CheckConstraint("access IN ('read', 'write')", name="access"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spaces.id", ondelete="CASCADE"))
    agent: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(Text)
    access: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


class Approval(UUIDPrimaryKey, Base):
    """An action awaiting the owner's decision. `params` are exactly what will be executed."""

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint("level IN ('approval', 'reinforced')", name="level"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'executed', 'failed')",
            name="status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spaces.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]]
    preview: Mapped[dict[str, Any]]
    level: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)
    requested_by: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
    executed_at: Mapped[datetime | None]
    result: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]


# "reserved": quota held while the call runs (ADR 0010), then settled into "ok" or "error",
# or released as "abandoned" when its process died. Keep in sync with the migrations and
# docs/modele-de-donnees.md.
LLM_OUTCOMES = ("ok", "denied", "budget_exceeded", "error", "reserved", "abandoned")


class LLMCall(UUIDPrimaryKey, Base):
    """Metering and audit of cloud calls. Never stores prompts or outputs."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint(_in("outcome", LLM_OUTCOMES), name="outcome"),
        # Pending reservations only: the release sweep and the in-flight count stay cheap.
        Index(
            "ix_llm_calls_reserved",
            "user_id",
            "created_at",
            postgresql_where="outcome = 'reserved'",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[uuid.UUID | None]
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(Text, default="fast", server_default="fast")
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Estimated from the configured price table; 0 on free billing.
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, server_default="0")
    redactions: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Estimate held before the call, counted only while outcome = 'reserved'; kept
    # afterwards to compare with the real usage.
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reserved_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=0, server_default="0"
    )
    # When the reservation was settled with the real usage, or released as abandoned.
    settled_at: Mapped[datetime | None]
    # Links the call to the run that made it (e.g. a workflow run); no foreign key.
    correlation_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(index=True)
