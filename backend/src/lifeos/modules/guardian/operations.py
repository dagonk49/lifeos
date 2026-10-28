"""Writes at external providers behind an approval: claimed once, never sent twice (ADR 0016).

An approved action that writes outside LifeOS (calendar event, e-mail) runs here:

1. claim: one `external_operations` row per approval, inserted and committed on its own
   (detached transaction) BEFORE the provider is called;
2. if a claim already exists (a replayed approval after a crash, a retried request), the
   provider is never written to blindly: the action's `reconcile` first asks the provider
   whether the earlier write happened; only if it did not is the write sent again, with
   the same idempotency key (client-chosen event id, If-Match), a few times at most;
3. the call: success and definitive failures are recorded at once (detached, so they
   survive a rollback of the request); a write whose outcome is unknown (timeout after
   sending) leaves the approval `failed` with `outcome_unknown` and is settled later by the
   `reconcile_external_operations` job.

Generic: calendar writes (task 16) and e-mail sending (task 18) plug in an
`ExternalAction`. No content is stored or logged: ids, states and reason codes only.
"""

import base64
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.connectors.base import (
    ConnectorAuthError,
    ConnectorConflictError,
    ConnectorError,
    ConnectorGoneError,
    OutcomeUnknownError,
)
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval, ExternalOperation
from lifeos.modules.guardian.principal import system_principal
from lifeos.modules.identity.models import User
from lifeos.modules.llm.fake import DisabledProvider

log = get_logger(__name__)

# Writes sent for one approval at most (the first and the retries after a crash).
MAX_ATTEMPTS = 3
# A claim older than this, still `claimed`, belongs to a request that died.
STALE_CLAIM = timedelta(minutes=5)
RECONCILE_ACTOR = "reconcile_external_operations"
RECONCILE_QUEUE = "connectors"


@dataclass(frozen=True)
class Attempt:
    approval_id: uuid.UUID
    # The same on every attempt: providers deduplicate on it.
    idempotency_key: str
    number: int


@dataclass(frozen=True)
class Outcome:
    # Recorded on the operation and the approval: ids and counts only, never content.
    result: dict[str, Any]
    provider_ref: str | None = None


Perform = Callable[[Ctx, uuid.UUID, Any, Attempt], Awaitable[Outcome]]
# The earlier write's outcome when it happened (local effects applied), None otherwise.
Reconcile = Callable[[Ctx, uuid.UUID, Any, Attempt], Awaitable[Outcome | None]]


def _no_account(_params: Any) -> uuid.UUID | None:
    return None


@dataclass(frozen=True)
class ExternalAction:
    perform: Perform
    reconcile: Reconcile
    # The connected account the write goes through (for the operation's record).
    account_id: Callable[[Any], uuid.UUID | None] = _no_account


def idempotency_key(approval_id: uuid.UUID) -> str:
    """Derived from the approval: 28 characters of base32hex (0-9, a-v), which Google
    Calendar accepts as a client-chosen event id."""
    return "lo" + base64.b32hexencode(approval_id.bytes).decode().rstrip("=").lower()


@dataclass(frozen=True)
class _Claim:
    id: uuid.UUID
    fresh: bool
    state: str
    attempts: int
    result: dict[str, Any] | None


async def _claim(
    database: Database, approval: Approval, action: str, account_id: uuid.UUID | None
) -> _Claim:
    now = utcnow()
    async with database.sessionmaker() as session, session.begin():
        inserted = await session.scalar(
            insert(ExternalOperation)
            .values(
                id=uuid.uuid4(),
                user_id=approval.user_id,
                space_id=approval.space_id,
                approval_id=approval.id,
                action=action,
                account_id=account_id,
                idempotency_key=idempotency_key(approval.id),
                state="claimed",
                attempts=1,
                claimed_at=now,
            )
            .on_conflict_do_nothing()
            .returning(ExternalOperation.id)
        )
        if inserted is not None:
            return _Claim(inserted, True, "claimed", 1, None)
        existing = await session.scalar(
            select(ExternalOperation)
            .where(ExternalOperation.approval_id == approval.id)
            .with_for_update()
        )
        assert existing is not None  # the conflict is on approval_id (or its derived key)
        return _Claim(existing.id, False, existing.state, existing.attempts, existing.result)


async def _record(
    database: Database,
    operation_id: uuid.UUID,
    state: str,
    *,
    outcome: Outcome | None = None,
    error: str | None = None,
    attempts: int | None = None,
) -> None:
    values: dict[str, Any] = {"state": state, "error_code": error}
    if state in ("succeeded", "failed"):
        values["finished_at"] = utcnow()
    if outcome is not None:
        values["result"] = outcome.result
        values["provider_ref"] = outcome.provider_ref
    if attempts is not None:
        values["attempts"] = attempts
        values["claimed_at"] = utcnow()
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(ExternalOperation).where(ExternalOperation.id == operation_id).values(**values)
        )


async def _audit_failure(
    ctx: Ctx, approval: Approval, action: str, state: str, reason: str
) -> None:
    await audit.record_detached(
        ctx.database,
        action="external_operation",
        outcome="failure",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=approval.space_id,
        target_type="approval",
        target_id=approval.id,
        details={"action": action, "state": state, "reason": reason[:64]},
    )


def queue_reconcile() -> bool:
    from lifeos.workers.queue import enqueue

    return enqueue(RECONCILE_ACTOR, queue=RECONCILE_QUEUE)


def _problem(exc: ConnectorError) -> ProblemError:
    if isinstance(exc, ConnectorConflictError):
        return ProblemError(409, exc.reason, "The remote object changed since the preview")
    if isinstance(exc, ConnectorGoneError):
        return ProblemError(409, "target_gone", "The remote object no longer exists")
    if isinstance(exc, ConnectorAuthError):
        return ProblemError(
            409, "connector_reauth_required", "This account must be connected again"
        )
    return ProblemError(
        503, "connector_unavailable", "The provider did not answer", extra={"reason": exc.reason}
    )


async def run_external(
    ctx: Ctx, approval: Approval, action: str, external: ExternalAction, params: Any
) -> dict[str, Any]:
    """Runs the approved write once. Raises ProblemError when it did not happen (or its
    outcome is unknown: `outcome_unknown`); any other exception leaves the claim to the
    reconciliation (the request died: nothing is resent blindly)."""
    claim = await _claim(ctx.database, approval, action, external.account_id(params))
    key = idempotency_key(approval.id)
    number = claim.attempts
    if not claim.fresh:
        # An earlier attempt claimed this approval (its request died, or the outcome is
        # unknown): ask the provider first.
        attempt = Attempt(approval.id, key, number)
        try:
            outcome = await external.reconcile(ctx, approval.space_id, params, attempt)
        except ConnectorError as exc:
            if claim.state == "succeeded" and claim.result is not None:
                return claim.result
            await _record(ctx.database, claim.id, "unknown", error=exc.reason[:64])
            raise ProblemError(
                502, "outcome_unknown", "The outcome of the earlier attempt is not known yet"
            ) from exc
        if outcome is not None:
            await _record(ctx.database, claim.id, "succeeded", outcome=outcome)
            log.info("external_operation_reconciled", action=action, approval=str(approval.id))
            return outcome.result
        if claim.state == "succeeded" and claim.result is not None:
            # Done earlier and changed since by someone else: the record is the truth.
            return claim.result
        if claim.attempts >= MAX_ATTEMPTS:
            await _record(ctx.database, claim.id, "failed", error="too_many_attempts")
            raise ProblemError(409, "too_many_attempts", "This approval was attempted too often")
        number = claim.attempts + 1
        await _record(ctx.database, claim.id, "claimed", attempts=number)
    attempt = Attempt(approval.id, key, number)
    try:
        outcome = await external.perform(ctx, approval.space_id, params, attempt)
    except OutcomeUnknownError as exc:
        await _record(ctx.database, claim.id, "unknown", error=exc.reason[:64])
        await _audit_failure(ctx, approval, action, "unknown", exc.reason)
        queue_reconcile()
        raise ProblemError(
            502,
            "outcome_unknown",
            "The provider did not confirm the change",
            "LifeOS checks with the provider shortly; nothing is sent again meanwhile.",
        ) from exc
    except ConnectorError as exc:
        await _record(ctx.database, claim.id, "failed", error=exc.reason[:64])
        await _audit_failure(ctx, approval, action, "failed", exc.reason)
        raise _problem(exc) from exc
    except ProblemError as exc:
        await _record(ctx.database, claim.id, "failed", error=exc.code[:64])
        raise
    await _record(ctx.database, claim.id, "succeeded", outcome=outcome)
    return outcome.result


# --- Reconciliation (worker) ------------------------------------------------------------------


async def due_operations(database: Database, *, now: datetime, limit: int = 50) -> list[uuid.UUID]:
    async with database.sessionmaker() as session:
        return list(
            await session.scalars(
                select(ExternalOperation.id)
                .where(
                    or_(
                        ExternalOperation.state == "unknown",
                        (ExternalOperation.state == "claimed")
                        & (ExternalOperation.claimed_at < now - STALE_CLAIM),
                    )
                )
                .order_by(ExternalOperation.claimed_at)
                .limit(limit)
            )
        )


async def _reconcile_one(
    database: Database, settings: Settings, connectors: Any, operation_id: uuid.UUID
) -> str:
    from lifeos.modules.guardian.actions import REGISTRY

    async with database.sessionmaker() as session, session.begin():
        operation = await session.get(ExternalOperation, operation_id, with_for_update=True)
        if operation is None or operation.state not in ("claimed", "unknown"):
            return "skipped"
        if (
            await session.scalar(select(Approval.id).where(Approval.id == operation.approval_id))
            is None
        ):
            operation.state, operation.error_code = "failed", "approval_gone"
            operation.finished_at = utcnow()
            return "failed"
        # The deciding request may still hold the approval: skipped, tried next time.
        approval = await session.scalar(
            select(Approval)
            .where(Approval.id == operation.approval_id)
            .with_for_update(skip_locked=True)
        )
        user = await session.get(User, operation.user_id)
        entry = REGISTRY.get(operation.action)
        if approval is None or user is None or entry is None or entry.external is None:
            return "skipped"
        ctx = Ctx(
            db=session,
            database=database,
            settings=settings,
            principal=system_principal(user.id, RECONCILE_ACTOR, frozenset({approval.space_id})),
            user=user,
            llm=DisabledProvider(),
            connectors=connectors,
        )
        params = entry.params.model_validate(approval.params)
        attempt = Attempt(approval.id, operation.idempotency_key, operation.attempts)
        try:
            async with session.begin_nested():
                outcome = await entry.external.reconcile(ctx, approval.space_id, params, attempt)
        except (ConnectorError, ProblemError) as exc:
            reason = exc.reason if isinstance(exc, ConnectorError) else exc.code
            log.info("external_operation_unresolved", operation=str(operation.id), error=reason)
            return "unknown"
        now = utcnow()
        if outcome is not None:
            operation.state, operation.error_code = "succeeded", None
            operation.result, operation.provider_ref = outcome.result, outcome.provider_ref
            operation.finished_at = now
            # The owner decided and the write happened: the approval says so.
            if approval.status in ("pending", "approved", "failed"):
                approval.status, approval.error = "executed", None
                approval.result = outcome.result
                approval.executed_at = now
                approval.decided_at = approval.decided_at or now
            state = "succeeded"
        else:
            # Never applied: nothing to undo. A pending approval may be approved again.
            operation.state, operation.error_code = "failed", "not_applied"
            operation.finished_at = now
            if approval.status == "failed" and approval.error == "outcome_unknown":
                approval.error = "not_applied"
            state = "not_applied"
        await audit.record(
            session,
            action="external_operation.reconcile",
            actor_type="system",
            actor_id=RECONCILE_ACTOR,
            user_id=operation.user_id,
            space_id=operation.space_id,
            target_type="approval",
            target_id=approval.id,
            details={"action": operation.action, "state": state},
        )
        return state


async def reconcile_due(
    database: Database, settings: Settings, connectors: Any, *, now: datetime | None = None
) -> dict[str, int]:
    """Operations whose outcome is unknown, or whose request died after claiming them."""
    counts: dict[str, int] = {}
    for operation_id in await due_operations(database, now=now or utcnow()):
        state = await _reconcile_one(database, settings, connectors, operation_id)
        counts[state] = counts.get(state, 0) + 1
    return counts
