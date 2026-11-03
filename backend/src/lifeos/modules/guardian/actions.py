"""Action execution through the Guardian: authorise, then run a registered handler.

Every action that may require an approval is registered with a parameter schema (what the
owner sees and approves, exactly), a preview builder and a handler. Approved parameters are
bound by their SHA-256: anything else is refused at execution. A write at an external
provider (calendar, e-mail) is registered with an `ExternalAction` and runs through
`operations.run_external`: claimed once, never sent twice (ADR 0016).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.guardian import operations
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval, ExternalOperation, params_digest
from lifeos.modules.guardian.operations import ExternalAction
from lifeos.modules.guardian.policy import ACTIONS, Decision, Level, evaluate

Handler = Callable[[Ctx, uuid.UUID, Any], Awaitable[dict[str, Any]]]
Previewer = Callable[[Any], dict[str, Any]]

APPROVAL_TTL = timedelta(days=7)
# Decided approvals stay in the owner's history this long, then are purged: their
# parameters and preview hold the proposed texts (docs/politique-de-donnees.md §1).
DECIDED_RETENTION = timedelta(days=90)


@dataclass(frozen=True)
class RegisteredAction:
    params: type[BaseModel]
    handler: Handler | None
    preview: Previewer
    # A write outside LifeOS: claimed, idempotent, reconciled (operations.py).
    external: ExternalAction | None = None
    # May the owner edit the parameters when approving? Not when the preview was built
    # from the provider's state: it would no longer describe what runs.
    editable: bool = True


REGISTRY: dict[str, RegisteredAction] = {}


def register(
    action: str,
    params: type[BaseModel],
    handler: Handler | None,
    preview: Previewer,
    *,
    external: ExternalAction | None = None,
    editable: bool = True,
) -> None:
    spec = ACTIONS.get(action)
    if spec is None:
        raise ValueError(f"unknown action {action!r}: declare it in guardian.policy")
    if (handler is None) == (external is None):
        raise ValueError(f"{action!r}: give either a handler or an external action")
    if external is not None and spec.user_level != Level.APPROVAL:
        # The claim, its key and its reconciliation hang on an approval.
        raise ValueError(f"{action!r}: an external write must require an approval")
    REGISTRY[action] = RegisteredAction(
        params=params, handler=handler, preview=preview, external=external, editable=editable
    )


def digest(params: BaseModel) -> str:
    """What binds an approval to its parameters (canonical JSON, SHA-256)."""
    return params_digest(params.model_dump(mode="json"))


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
    if decision.code == "approval_params_mismatch":
        raise ProblemError(
            403, "approval_params_mismatch", "These parameters are not the ones approved"
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
    if approval is not None and digest(parsed) != approval.params_sha256:
        # Exactly what was approved, or nothing (an edited row, a forged call).
        await _refuse(ctx, action, space_id, Decision(False, "approval_params_mismatch"))
    decision = await authorize(ctx, action, space_id, approval)
    if entry.external is not None:
        if approval is None:  # unreachable: authorize() requires one for these actions
            await _refuse(ctx, action, space_id, Decision(False, "approval_required"))
        assert approval is not None
        result = await operations.run_external(ctx, approval, action, entry.external, parsed)
    else:
        assert entry.handler is not None
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


async def invalidate_targets(
    session: AsyncSession,
    user_id: uuid.UUID,
    targets: list[str],
    *,
    reason: str,
    keep: uuid.UUID | None = None,
) -> int:
    """Pending approvals about these objects can no longer be approved: the object changed,
    disappeared, or a newer proposal replaced them. Each is audited; `keep` is spared."""
    if not targets:
        return 0
    stmt = (
        update(Approval)
        .where(
            Approval.user_id == user_id,
            Approval.status == "pending",
            Approval.target.in_(targets),
        )
        .values(status="invalidated", error=reason, decided_at=utcnow())
        .returning(Approval.id, Approval.space_id)
    )
    if keep is not None:
        stmt = stmt.where(Approval.id != keep)
    rows = (await session.execute(stmt)).all()
    for approval_id, space_id in rows:
        await audit.record(
            session,
            action="approval.invalidate",
            actor_type="system",
            actor_id="guardian",
            user_id=user_id,
            space_id=space_id,
            target_type="approval",
            target_id=approval_id,
            details={"reason": reason[:64]},
        )
    return len(rows)


async def request_approval(
    ctx: Ctx,
    action: str,
    space_id: uuid.UUID,
    params: BaseModel,
    *,
    requested_by: str,
    target: str | None = None,
) -> Approval:
    """A proposal for the owner.

    The same proposal (action, space, exact parameters) still pending is returned rather
    than a second one (a replayed request, a double click). A new proposal about the same
    `target` replaces the older pending ones: one pending proposal per object.
    """
    entry = _registered(action)
    ctx.principal.require_space(space_id)
    spec = ACTIONS[action]
    level = spec.agent_level if ctx.principal.kind == "agent" else spec.user_level
    now = utcnow()
    dump = params.model_dump(mode="json")
    params_sha256 = params_digest(dump)
    existing = await ctx.db.scalar(
        select(Approval).where(
            Approval.user_id == ctx.principal.user_id,
            Approval.space_id == space_id,
            Approval.action == action,
            Approval.params_sha256 == params_sha256,
            Approval.status == "pending",
            Approval.expires_at > now,
        )
    )
    if existing is not None:
        return existing
    if target is not None:
        await invalidate_targets(ctx.db, ctx.principal.user_id, [target], reason="superseded")
    reinforced = level == Level.REINFORCED or spec.approval_level == "reinforced"
    approval = Approval(
        user_id=ctx.principal.user_id,
        space_id=space_id,
        action=action,
        params=dump,
        params_sha256=params_sha256,
        target=target,
        preview=entry.preview(params),
        level="reinforced" if reinforced else "approval",
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
        details={"action": action, "level": approval.level},
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


async def _deny_decision(
    ctx: Ctx,
    approval_id: uuid.UUID,
    *,
    action: str,
    space_id: uuid.UUID | None,
    details: dict[str, audit.DetailValue],
) -> None:
    await audit.record_detached(
        ctx.database,
        action=action,
        outcome="denied",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type="approval",
        target_id=approval_id,
        details=details,
    )


async def _pending(ctx: Ctx, approval_id: uuid.UUID, *, operation: str) -> Approval:
    """The approval, locked, if a decision can still be taken on it.

    A second decision (a replayed request, a double click, a decision on an expired,
    rejected or invalidated proposal) is refused and audited: an approval is used once at
    most.
    """
    approval = await get_approval(ctx, approval_id, for_update=True)
    if approval.status == "pending" and approval.expires_at <= utcnow():
        approval.status = "expired"
    if approval.status != "pending":
        await _deny_decision(
            ctx,
            approval.id,
            action="approval.replay",
            space_id=approval.space_id,
            details={
                "operation": operation,
                "status": approval.status,
                "action": approval.action,
            },
        )
        raise ProblemError(
            409,
            "approval_not_pending",
            "Approval is not pending",
            extra={"approval_status": approval.status},
        )
    return approval


async def approve(
    ctx: Ctx, approval_id: uuid.UUID, edited_params: dict[str, Any] | None = None
) -> Approval:
    """The owner approves (optionally after editing the exact parameters), then it runs."""
    if ctx.principal.kind != "user":
        # Agents propose, the owner decides: never an approval by an agent or a job.
        await _deny_decision(
            ctx,
            approval_id,
            action="approval.approve",
            space_id=None,
            details={"reason": "owner_only"},
        )
        raise ProblemError(403, "action_denied", "Only the owner can approve")
    approval = await _pending(ctx, approval_id, operation="approve")
    if approval.level == "reinforced" and not ctx.principal.recent_auth:
        await _deny_decision(
            ctx,
            approval.id,
            action="approval.approve",
            space_id=approval.space_id,
            details={"reason": "reauthentication_required", "action": approval.action},
        )
        raise ProblemError(
            403,
            "reauthentication_required",
            "Recent authentication required",
            "Sign in again with a passkey to perform this operation.",
        )
    entry = _registered(approval.action)
    if edited_params is not None:
        if not entry.editable:
            raise ProblemError(
                422,
                "approval_not_editable",
                "These parameters cannot be edited: propose the change again",
            )
        parsed = _parse(entry, edited_params)
        approval.params = parsed.model_dump(mode="json")
        approval.params_sha256 = digest(parsed)
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
    approval = await _pending(ctx, approval_id, operation="reject")
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


async def purge_decided(session: Any, *, now: datetime | None = None) -> int:
    """Approvals decided (or expired) more than DECIDED_RETENTION ago, with their external
    operations; audited by count."""
    cutoff = (now or utcnow()) - DECIDED_RETENTION
    result = await session.execute(
        delete(Approval).where(
            Approval.status != "pending",
            func.coalesce(Approval.decided_at, Approval.expires_at) < cutoff,
        )
    )
    count = int(result.rowcount or 0)
    # No foreign key from the claims (operations.py): they go with their approval.
    await session.execute(
        delete(ExternalOperation).where(
            ~select(Approval.id).where(Approval.id == ExternalOperation.approval_id).exists()
        )
    )
    if count:
        await audit.record(
            session,
            action="approval.purge",
            actor_type="system",
            actor_id="purge_approvals",
            details={"count": count},
        )
    return count
