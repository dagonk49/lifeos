from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, UUIDPrimaryKey

# Keep in sync with the migrations and docs/modele-de-donnees.md.
WORKFLOWS = ("purchase",)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AgentRun(UUIDPrimaryKey, Owned, Base):
    """One coordinated run of several agents (task 28): who read what, the figures and
    goals computed locally, and the synthesis built from them only.

    Personal data like messages: purged after 90 days, exported, erased with the goals it
    quotes. The cloud call it made carries its id (`llm_calls.correlation_id`).
    """

    __tablename__ = "agent_runs"
    __table_args__ = (CheckConstraint(_in("workflow", WORKFLOWS), name="workflow"),)

    workflow: Mapped[str] = mapped_column(Text)
    # The owner's question (amount, label): the input of every step.
    question: Mapped[dict[str, Any]]
    # Per agent: resources read, missing permissions, items used, duration.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Figures, goals and hypotheses computed locally, with the agent that produced each.
    findings: Mapped[dict[str, Any]]
    # The synthesis after deterministic checks; None in degraded mode.
    advice: Mapped[dict[str, Any] | None]
    error_code: Mapped[str | None] = mapped_column(Text)
    cloud_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    purge_after: Mapped[datetime] = mapped_column(index=True)
    created_at: Mapped[datetime]
