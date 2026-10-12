import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import AGENTS, RESOURCES, AgentPermission


async def list_permissions(ctx: Ctx, *, include_inactive: bool = False) -> list[AgentPermission]:
    stmt = select(AgentPermission).where(AgentPermission.user_id == ctx.principal.user_id)
    if not include_inactive:
        stmt = stmt.where(AgentPermission.revoked_at.is_(None))
    rows = await ctx.db.scalars(stmt.order_by(AgentPermission.created_at.desc()))
    return list(rows)


async def grant(
    ctx: Ctx,
    *,
    agent: str,
    space_id: uuid.UUID,
    resource: str,
    access: str,
    purpose: str,
    expires_at: datetime | None,
) -> AgentPermission:
    """Only the owner, recently authenticated, can grant: agents never grant themselves."""
    if agent not in AGENTS or resource not in RESOURCES or access not in ("read", "write"):
        raise ProblemError(422, "invalid_permission", "Unknown agent, resource or access")
    ctx.principal.require_space(space_id)
    await authorize(ctx, "permission.grant", space_id)
    if expires_at is not None and expires_at <= utcnow():
        raise ProblemError(422, "invalid_permission", "Expiry must be in the future")
    permission = AgentPermission(
        user_id=ctx.principal.user_id,
        space_id=space_id,
        agent=agent,
        resource=resource,
        access=access,
        purpose=purpose,
        expires_at=expires_at,
        created_at=utcnow(),
    )
    ctx.db.add(permission)
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="permission.grant",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type="agent_permission",
        target_id=permission.id,
        details={"agent": agent, "resource": resource, "access": access},
    )
    return permission


async def revoke(ctx: Ctx, permission_id: uuid.UUID) -> None:
    """Immediate withdrawal; no re-authentication needed to reduce privileges."""
    permission = await ctx.db.scalar(
        select(AgentPermission).where(
            AgentPermission.id == permission_id,
            AgentPermission.user_id == ctx.principal.user_id,
            AgentPermission.revoked_at.is_(None),
        )
    )
    if permission is None:
        raise not_found("permission_not_found", "Permission not found")
    permission.revoked_at = utcnow()
    await audit.record(
        ctx.db,
        action="permission.revoke",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=permission.space_id,
        target_type="agent_permission",
        target_id=permission.id,
    )


async def set_bundle(
    ctx: Ctx,
    *,
    agent: str,
    space_id: uuid.UUID,
    resources: tuple[str, ...],
    purpose: str,
    enabled: bool,
    days: int,
) -> None:
    """Grants (or withdraws) read access to several resources for one purpose.

    Granting is a reinforced operation; withdrawing is always allowed immediately.
    """
    ctx.principal.require_space(space_id)
    if enabled:
        await authorize(ctx, "permission.grant", space_id)
    now = utcnow()
    current = await ctx.db.scalars(
        select(AgentPermission).where(
            AgentPermission.user_id == ctx.principal.user_id,
            AgentPermission.space_id == space_id,
            AgentPermission.agent == agent,
            AgentPermission.purpose == purpose,
            AgentPermission.revoked_at.is_(None),
        )
    )
    for permission in current:
        permission.revoked_at = now
    if enabled:
        for resource in resources:
            ctx.db.add(
                AgentPermission(
                    user_id=ctx.principal.user_id,
                    space_id=space_id,
                    agent=agent,
                    resource=resource,
                    access="read",
                    purpose=purpose,
                    expires_at=now + timedelta(days=days),
                    created_at=now,
                )
            )
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="permission.grant" if enabled else "permission.revoke",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        details={"agent": agent, "resources": ",".join(resources), "purpose_len": len(purpose)},
    )
