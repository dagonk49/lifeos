import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, UUIDPrimaryKey

AGENTS = ("core", "planning", "memory", "coaching", "finance", "projects", "documents")
RESOURCES = ("tasks", "journal", "facts", "memories", "messages")


class AgentPermission(UUIDPrimaryKey, Base):
    """Per agent, space, resource, access and purpose; time-limited and revocable."""

    __tablename__ = "agent_permissions"
    __table_args__ = (
        CheckConstraint(
            "agent IN ('core', 'planning', 'memory', 'coaching', 'finance', 'projects', "
            "'documents')",
            name="agent",
        ),
        CheckConstraint(
            "resource IN ('tasks', 'journal', 'facts', 'memories', 'messages')", name="resource"
        ),
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


class LLMCall(UUIDPrimaryKey, Base):
    """Metering and audit of cloud calls. Never stores prompts or outputs."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint("outcome IN ('ok', 'denied', 'budget_exceeded', 'error')", name="outcome"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[uuid.UUID | None]
    purpose: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    redactions: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(index=True)
