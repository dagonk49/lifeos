import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, Text
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, UUIDPrimaryKey

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
    # Connected accounts (ADR 0012): on top of the owner's consent for the space.
    "calendar",
    "email",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def params_digest(params: Any) -> str:
    """SHA-256 of the canonical JSON of approval parameters (keys sorted, compact)."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _params_digest_default(context: DefaultExecutionContext) -> str:
    return params_digest(context.get_current_parameters()["params"])  # type: ignore[no-untyped-call]


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


# "invalidated": the proposal no longer matches reality (the event changed at the provider,
# a newer proposal replaced it); it can no longer be approved. Keep in sync with the
# migrations and docs/modele-de-donnees.md.
APPROVAL_STATUSES = (
    "pending",
    "approved",
    "rejected",
    "expired",
    "executed",
    "failed",
    "invalidated",
)


class Approval(UUIDPrimaryKey, Base):
    """An action awaiting the owner's decision. `params` are exactly what will be executed,
    bound by `params_sha256`: anything else is refused at execution."""

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint("level IN ('approval', 'reinforced')", name="level"),
        CheckConstraint(_in("status", APPROVAL_STATUSES), name="status"),
        # Pending proposals about one object (dedupe, invalidation when it changes).
        Index(
            "ix_approvals_pending_target",
            "user_id",
            "target",
            postgresql_where="status = 'pending'",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spaces.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]]
    # SHA-256 of the canonical JSON of `params` (actions.params_digest).
    # Nullable only so that the previous version can still insert during an update or a
    # rollback (docs/modele-de-donnees.md §7): a missing digest never matches, the
    # approval is then refused at execution.
    params_sha256: Mapped[str | None] = mapped_column(Text, default=_params_digest_default)
    preview: Mapped[dict[str, Any]]
    # The object the proposal acts on ("calendar_event:<uuid>"), if any.
    target: Mapped[str | None] = mapped_column(Text)
    level: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)
    requested_by: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime]
    decided_at: Mapped[datetime | None]
    executed_at: Mapped[datetime | None]
    result: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]


# claimed: the write may be under way (or its process died); succeeded / failed: settled;
# unknown: sent, outcome not known (timeout): reconciled with the provider, never resent
# blindly. Keep in sync with the migrations and docs/modele-de-donnees.md.
OPERATION_STATES = ("claimed", "succeeded", "unknown", "failed")


class ExternalOperation(UUIDPrimaryKey, Owned, Base):
    """A write at an external provider for an approval: claimed once, before the call.

    The claim is committed on its own (detached) before the provider is called, so a
    replayed approval, a retried request or a crash after the call never sends a second
    write: an existing claim is reconciled with the provider first (ADR 0016). No foreign
    key to `approvals`: the claim is inserted while the deciding transaction holds the
    approval's row lock (purged with the approval, actions.purge_decided).
    """

    __tablename__ = "external_operations"
    __table_args__ = (CheckConstraint(_in("state", OPERATION_STATES), name="state"),)

    approval_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    action: Mapped[str] = mapped_column(Text)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connector_accounts.id", ondelete="SET NULL")
    )
    # Sent to the provider (client-chosen event id, request id): the same on every attempt.
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    state: Mapped[str] = mapped_column(Text, index=True)
    # Provider id of the object written, and the result recorded (ids only, no content).
    provider_ref: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None]
    error_code: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    claimed_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]


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
