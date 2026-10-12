import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import structlog
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.security import utcnow
from lifeos.modules.audit.models import AuditEvent

if TYPE_CHECKING:
    from lifeos.db.session import Database

ActorType = Literal["user", "agent", "system", "anonymous"]
Outcome = Literal["success", "denied", "failure"]
DetailValue = str | int | float | bool | None

# Details are for identifiers, counters and reason codes. A long string is a sign that
# content is leaking into the audit trail, which would defeat erasure.
_MAX_DETAIL_STRING = 200


def _check_details(details: dict[str, DetailValue]) -> None:
    for key, value in details.items():
        if isinstance(value, str) and len(value) > _MAX_DETAIL_STRING:
            raise ValueError(f"audit detail {key!r} is too long; audit must not hold content")


async def record(
    session: AsyncSession,
    *,
    action: str,
    outcome: Outcome = "success",
    actor_type: ActorType,
    actor_id: str | uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    space_id: uuid.UUID | None = None,
    details: dict[str, DetailValue] | None = None,
) -> AuditEvent:
    details = details or {}
    _check_details(details)
    event = AuditEvent(
        occurred_at=utcnow(),
        user_id=user_id,
        actor_type=actor_type,
        actor_id=str(actor_id) if actor_id is not None else None,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        space_id=space_id,
        request_id=structlog.contextvars.get_contextvars().get("request_id"),
        details=details,
    )
    session.add(event)
    await session.flush()
    return event


async def purge_older_than(session: AsyncSession, *, days: int, now: datetime | None = None) -> int:
    """Retention purge: the only DELETE the append-only trigger allows."""
    cutoff = (now or utcnow()) - timedelta(days=days)
    await session.execute(text("SET LOCAL lifeos.audit_purge = 'on'"))
    result = await session.execute(delete(AuditEvent).where(AuditEvent.occurred_at < cutoff))
    await session.execute(text("SET LOCAL lifeos.audit_purge = 'off'"))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def record_detached(database: "Database", **kwargs: Any) -> None:
    """Records an audit event in its own transaction.

    Used for denials and failures: the request transaction is rolled back when the error
    propagates, but the trace of the refusal must survive.
    """
    async with database.sessionmaker() as session, session.begin():
        await record(session, **kwargs)
