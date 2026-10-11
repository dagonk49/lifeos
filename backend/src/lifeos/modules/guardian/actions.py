"""Action execution through the Guardian: authorise, then run a registered handler.

Every action that may require an approval is registered with a parameter schema (what the
owner sees and approves, exactly), a preview builder and a handler.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval
from lifeos.modules.guardian.policy import ACTIONS, Decision, Level, evaluate

Handler = Callable[[Ctx, uuid.UUID, Any], Awaitable[dict[str, Any]]]
Previewer = Callable[[Any], dict[str, Any]]

APPROVAL_TTL = timedelta(days=7)


@dataclass(frozen=True)
class RegisteredAction:
    params: type[BaseModel]
    handler: Handler
    preview: Previewer


REGISTRY: dict[str, RegisteredAction] = {}


def register(action: str, params: type[BaseModel], handler: Handler, preview: Previewer) -> None:
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}: declare it in guardian.policy")
    REGISTRY[action] = RegisteredAction(params=params, handler=handler, preview=preview)


def _registered(action: str) -> RegisteredAction:
    entry = REGISTRY.get(action)
    if entry is None:
        raise ProblemError(501, "action_not_available", "This action is not available yet")
    return entry


def _parse(entry: RegisteredAction, params: dict[str, Any]) -> BaseModel:
    try:
        return entry.params.model_validate(params)
    except ValidationError as exc:
        raise ProblemError(422, "invalid_action_params", "Invalid action parameters") from exc


async def _refuse(ctx: Ctx, action: str, space_id: uuid.UUID | None, decision: Decision) -> None:
    await audit.record_detached(
        ctx.database,
        action=action,
        outcome="denied",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        details={"reason": decision.code},
    )
    if decision.code == "approval_required":
        raise ProblemError(
            403, "approval_required", "This action requires the owner's explicit approval"
        )
    if decision.code == "reauthentication_required":
        raise ProblemError(
            403,
            "reauthentication_required",
            "Recent authentication required",
            "Sign in again with a passkey to perform this operation.",
        )
    raise ProblemError(403, "action_denied", "Action not allowed", extra={"reason": decision.code})


async def authorize(
    ctx: Ctx, action: str, space_id: uuid.UUID | None, approval: Approval | None = None
) -> Decision:
    """Raises (and audits the refusal) unless the principal may perform `action` now."""
    decision = evaluate(ctx.principal, action, space_id, approval)
    if not decision.allowed:
        await _refuse(ctx, action, space_id, decision)
    return decision


async def execute(
    ctx: Ctx,
    action: str,
    space_id: uuid.UUID,
    params: dict[str, Any],
    *,
    approval: Approval | None = None,
) -> dict[str, Any]:
    entry = _registered(action)
    parsed = _parse(entry, params)
    decision = await authorize(ctx, action, space_id, approval)
    result = await entry.handler(ctx, space_id, parsed)
    await audit.record(
        ctx.db,
        action=action,
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type="approval" if approval else None,
        target_id=approval.id if approval else None,
        details={"level": decision.level.value if decision.level else None},
    )
    return result


# --- Approvals ----------------------------------------------------------------------------


async def request_approval(
    ctx: Ctx,
    action: str,
    space_id: uuid.UUID,
    params: BaseModel,
    *,
    requested_by: str,
) -> Approval:
    entry = _registered(action)
    ctx.principal.require_space(space_id)
    spec = ACTIONS[action]
    level = spec.agent_level if ctx.principal.kind == "agent" else spec.user_level
    now = utcnow()
    approval = Approval(
        user_id=ctx.principal.user_id,
        space_id=space_id,
        action=action,
        params=params.model_dump(mode="json"),
        preview=entry.preview(params),
        level="reinforced" if level == Level.REINFORCED else "approval",
        status="pending",
        requested_by=requested_by,
        expires_at=now + APPROVAL_TTL,
        created_at=now,
    )
    ctx.db.add(approval)
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="approval.request",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type="approval",
        target_id=approval.id,
        details={"action": action},
    )
    return approval


async def get_approval(ctx: Ctx, approval_id: uuid.UUID, *, for_update: bool = False) -> Approval:
    stmt = select(Approval).where(
        Approval.id == approval_id,
        Approval.user_id == ctx.principal.user_id,
        Approval.space_id.in_(ctx.principal.space_ids),
    )
    if for_update:
        stmt = stmt.with_for_update()
    approval = await ctx.db.scalar(stmt)
    if approval is None:
        raise not_found("approval_not_found", "Approval not found")
    return approval


async def list_approvals(ctx: Ctx, *, status: str | None, limit: int = 100) -> list[Approval]:
    stmt = select(Approval).where(
        Approval.user_id == ctx.principal.user_id,
        Approval.space_id.in_(ctx.principal.space_ids),
    )
    if status:
        stmt = stmt.where(Approval.status == status)
    rows = await ctx.db.scalars(stmt.order_by(Approval.created_at.desc()).limit(limit))
    return list(rows)


async def _pending(ctx: Ctx, approval_id: uuid.UUID) -> Approval:
    approval = await get_approval(ctx, approval_id, for_update=True)
    if approval.status == "pending" and approval.expires_at <= utcnow():
        approval.status = "expired"
    if approval.status != "pending":
        raise ProblemError(
            409,
            "approval_not_pending",
            "Approval is not pending",
            extra={"status": approval.status},
        )
    return approval


async def approve(
    ctx: Ctx, approval_id: uuid.UUID, edited_params: dict[str, Any] | None = None
) -> Approval:
    """The owner approves (optionally after editing the exact parameters), then it runs."""
    if ctx.principal.kind != "user":
        raise ProblemError(403, "action_denied", "Only the owner can approve")
    approval = await _pending(ctx, approval_id)
    if approval.level == "reinforced" and not ctx.principal.recent_auth:
        raise ProblemError(403, "reauthentication_required", "Recent authentication required")
    entry = _registered(approval.action)
    if edited_params is not None:
        parsed = _parse(entry, edited_params)
        approval.params = parsed.model_dump(mode="json")
        approval.preview = entry.preview(parsed)
    approval.status = "approved"
    approval.decided_at = utcnow()
    await audit.record(
        ctx.db,
        action="approval.approve",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=approval.space_id,
        target_type="approval",
        target_id=approval.id,
        details={"action": approval.action, "edited": edited_params is not None},
    )
    try:
        async with ctx.db.begin_nested():
            approval.result = await execute(
                ctx, approval.action, approval.space_id, approval.params, approval=approval
            )
        approval.status = "executed"
        approval.executed_at = utcnow()
    except ProblemError as exc:
        approval.status = "failed"
        approval.error = exc.code
    return approval


async def reject(ctx: Ctx, approval_id: uuid.UUID) -> Approval:
    approval = await _pending(ctx, approval_id)
    approval.status = "rejected"
    approval.decided_at = utcnow()
    await audit.record(
        ctx.db,
        action="approval.reject",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=approval.space_id,
        target_type="approval",
        target_id=approval.id,
        details={"action": approval.action},
    )
    return approval


async def expire_stale(session: Any) -> int:
    result = await session.execute(
        update(Approval)
        .where(Approval.status == "pending", Approval.expires_at <= utcnow())
        .values(status="expired")
    )
    return int(result.rowcount or 0)
