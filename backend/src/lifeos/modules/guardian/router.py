import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from lifeos.api.deps import CtxDep
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.guardian import actions, gateway, permissions
from lifeos.modules.guardian.models import LLMCall
from lifeos.modules.guardian.policy import ACTIONS
from lifeos.modules.guardian.schemas import (
    ActionOut,
    ApprovalOut,
    ApproveRequest,
    AuditEventOut,
    AuditPage,
    LLMCallOut,
    LLMUsageOut,
    PermissionIn,
    PermissionOut,
)

approvals_router = APIRouter(prefix="/approvals", tags=["guardian"])
router = APIRouter(prefix="/guardian", tags=["guardian"])


@approvals_router.get("")
async def list_approvals(
    ctx: CtxDep, status_filter: str | None = Query(default=None, alias="status")
) -> list[ApprovalOut]:
    rows = await actions.list_approvals(ctx, status=status_filter)
    return [ApprovalOut.model_validate(a) for a in rows]


@approvals_router.get("/{approval_id}")
async def get_approval(approval_id: uuid.UUID, ctx: CtxDep) -> ApprovalOut:
    return ApprovalOut.model_validate(await actions.get_approval(ctx, approval_id))


@approvals_router.post("/{approval_id}/approve")
async def approve(approval_id: uuid.UUID, body: ApproveRequest, ctx: CtxDep) -> ApprovalOut:
    return ApprovalOut.model_validate(await actions.approve(ctx, approval_id, body.params))


@approvals_router.post("/{approval_id}/reject")
async def reject(approval_id: uuid.UUID, ctx: CtxDep) -> ApprovalOut:
    return ApprovalOut.model_validate(await actions.reject(ctx, approval_id))


@router.get("/actions")
async def list_actions(ctx: CtxDep) -> list[ActionOut]:
    return [
        ActionOut(
            name=s.name,
            description=s.description,
            user_level=s.user_level.value,
            agent_level=s.agent_level.value if s.agent_level else None,
        )
        for s in ACTIONS.values()
    ]


@router.get("/permissions")
async def list_permissions(ctx: CtxDep) -> list[PermissionOut]:
    return [PermissionOut.model_validate(p) for p in await permissions.list_permissions(ctx)]


@router.post("/permissions", status_code=status.HTTP_201_CREATED)
async def grant_permission(body: PermissionIn, ctx: CtxDep) -> PermissionOut:
    permission = await permissions.grant(
        ctx,
        agent=body.agent,
        space_id=body.space_id,
        resource=body.resource,
        access=body.access,
        purpose=body.purpose,
        expires_at=body.expires_at,
    )
    return PermissionOut.model_validate(permission)


@router.delete("/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_permission(permission_id: uuid.UUID, ctx: CtxDep) -> None:
    await permissions.revoke(ctx, permission_id)


@router.get("/audit")
async def audit_events(
    ctx: CtxDep,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None, description="Return events with a smaller id"),
    action: str | None = None,
) -> AuditPage:
    stmt = select(AuditEvent).where(AuditEvent.user_id == ctx.principal.user_id)
    if cursor is not None:
        stmt = stmt.where(AuditEvent.id < cursor)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    rows = list(await ctx.db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(limit + 1)))
    items = rows[:limit]
    next_cursor = str(items[-1].id) if len(rows) > limit else None
    return AuditPage(
        items=[AuditEventOut.model_validate(e) for e in items], next_cursor=next_cursor
    )


@router.get("/llm-usage")
async def llm_usage(ctx: CtxDep) -> LLMUsageOut:
    calls = await ctx.db.scalars(
        select(LLMCall)
        .where(LLMCall.user_id == ctx.principal.user_id)
        .order_by(LLMCall.created_at.desc())
        .limit(50)
    )
    return LLMUsageOut(
        **await gateway.usage_summary(ctx),
        recent_calls=[LLMCallOut.model_validate(c) for c in calls],
    )
