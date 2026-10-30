"""Revoking push devices (ADR 0019), kept apart from notifications.devices so that
identity.service can call it when a session or a passkey is revoked (no import cycle)."""

import uuid

from sqlalchemy import ColumnElement, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.identity.models import UserSession
from lifeos.modules.notifications.models import PushDelivery, PushDevice


async def revoke(session: AsyncSession, where: list[ColumnElement[bool]], reason: str) -> int:
    """Marks the matching active devices revoked and cancels their pending deliveries."""
    now = utcnow()
    revoked = list(
        await session.scalars(
            update(PushDevice)
            .where(*where, PushDevice.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
            .returning(PushDevice.id)
            .execution_options(synchronize_session=False)
        )
    )
    if revoked:
        await session.execute(
            update(PushDelivery)
            .where(PushDelivery.device_id.in_(revoked), PushDelivery.status == "pending")
            .values(status="cancelled", error_code="device_revoked")
            .execution_options(synchronize_session=False)
        )
    return len(revoked)


async def revoke_for_sessions(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    reason: str,
    session_id: uuid.UUID | None = None,
    credential_id: uuid.UUID | None = None,
    except_session_id: uuid.UUID | None = None,
    everything: bool = False,
) -> int:
    """The devices registered by a revoked session or passkey stop receiving pushes.
    `except_session_id`: every device but the current session's (« Déconnecter les autres
    sessions »), including those whose session is gone; `everything`: all of them.
    Audited by count, never with an endpoint."""
    where: list[ColumnElement[bool]] = [PushDevice.user_id == user_id]
    if session_id is not None:
        where.append(PushDevice.session_id == session_id)
    elif credential_id is not None:
        where.append(PushDevice.credential_id == credential_id)
    elif except_session_id is not None:
        where.append(
            or_(PushDevice.session_id.is_(None), PushDevice.session_id != except_session_id)
        )
    elif not everything:
        raise ValueError("nothing to revoke")
    count = await revoke(session, where, reason)
    if count:
        await audit.record(
            session,
            action="notification.device.revoke",
            actor_type="system",
            actor_id="identity",
            user_id=user_id,
            details={"reason": reason, "count": count},
        )
    return count


async def relink_session(session: AsyncSession, old: uuid.UUID, new: UserSession) -> None:
    """After a re-authentication the new session replaces the old one: so do its devices."""
    await session.execute(
        update(PushDevice)
        .where(PushDevice.session_id == old, PushDevice.revoked_at.is_(None))
        .values(session_id=new.id, credential_id=new.credential_id)
        .execution_options(synchronize_session=False)
    )
