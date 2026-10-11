"""Cloud gateway: the only path from LifeOS data to a cloud LLM (ADR 0003).

Checks consent and budget, masks sensitive data locally, meters every call without
storing its content, and turns provider failures into explicit degraded-mode errors.
"""

import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select

from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import LLMCall
from lifeos.modules.guardian.policy import evaluate
from lifeos.modules.guardian.privacy import mask
from lifeos.modules.identity.models import Space
from lifeos.modules.llm.base import (
    LLMBlockedError,
    LLMDisabledError,
    LLMError,
    LLMInvalidOutputError,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
)
from lifeos.observability.metrics import LLM_CALLS, LLM_TOKENS


def month_start(now: datetime, timezone: str) -> datetime:
    local = now.astimezone(ZoneInfo(timezone))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def tokens_used_this_month(ctx: Ctx) -> int:
    since = month_start(utcnow(), ctx.user.timezone)
    used = await ctx.db.scalar(
        select(func.coalesce(func.sum(LLMCall.input_tokens + LLMCall.output_tokens), 0)).where(
            LLMCall.user_id == ctx.user.id,
            LLMCall.created_at >= since,
            LLMCall.outcome == "ok",
        )
    )
    return int(used or 0)


async def _meter(
    ctx: Ctx,
    *,
    space_id: uuid.UUID,
    purpose: str,
    outcome: str,
    response: LLMResponse | None = None,
    redactions: int = 0,
    error_code: str | None = None,
    started: float | None = None,
) -> None:
    provider = ctx.llm.name
    LLM_CALLS.labels(provider, purpose, outcome).inc()
    if response is not None:
        LLM_TOKENS.labels(provider, "input").inc(response.usage.input_tokens)
        LLM_TOKENS.labels(provider, "output").inc(response.usage.output_tokens)
    # Detached: the call happened (and may have cost tokens) even if the request fails.
    async with ctx.database.sessionmaker() as session, session.begin():
        session.add(
            LLMCall(
                user_id=ctx.user.id,
                space_id=space_id,
                purpose=purpose,
                provider=provider,
                model=response.model if response else None,
                input_tokens=response.usage.input_tokens if response else 0,
                output_tokens=response.usage.output_tokens if response else 0,
                redactions=redactions,
                outcome=outcome,
                error_code=error_code,
                duration_ms=int((time.perf_counter() - started) * 1000) if started else 0,
                created_at=utcnow(),
            )
        )


async def _deny(ctx: Ctx, space_id: uuid.UUID, purpose: str, code: str) -> None:
    await audit.record_detached(
        ctx.database,
        action="llm.cloud_call",
        outcome="denied",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.user.id,
        space_id=space_id,
        details={"purpose": purpose, "reason": code},
    )


def _masked(request: LLMRequest) -> tuple[LLMRequest, int]:
    total = 0
    system = None
    if request.system is not None:
        result = mask(request.system)
        system, total = result.text, result.total
    messages = []
    for message in request.messages:
        result = mask(message.text)
        total += result.total
        messages.append(LLMMessage(role=message.role, text=result.text))
    return request.model_copy(update={"system": system, "messages": messages}), total


async def _prepare(ctx: Ctx, space_id: uuid.UUID, request: LLMRequest) -> tuple[LLMRequest, int]:
    purpose = request.purpose
    decision = evaluate(ctx.principal, "llm.cloud_call", space_id)
    if not decision.allowed:
        await _deny(ctx, space_id, purpose, decision.code)
        await _meter(
            ctx, space_id=space_id, purpose=purpose, outcome="denied", error_code=decision.code
        )
        raise ProblemError(403, "cloud_call_denied", "Cloud call not allowed")
    space = await ctx.db.get(Space, space_id)
    if space is None or not space.cloud_ai_enabled:
        await _deny(ctx, space_id, purpose, "cloud_consent_required")
        await _meter(
            ctx,
            space_id=space_id,
            purpose=purpose,
            outcome="denied",
            error_code="cloud_consent_required",
        )
        raise ProblemError(
            403,
            "cloud_consent_required",
            "Cloud AI is not enabled for this space",
            "Enable cloud AI for this space in the settings to use the assistant.",
        )
    if await tokens_used_this_month(ctx) >= ctx.user.llm_monthly_token_budget:
        await _meter(
            ctx,
            space_id=space_id,
            purpose=purpose,
            outcome="budget_exceeded",
            error_code="llm_budget_exceeded",
        )
        raise ProblemError(429, "llm_budget_exceeded", "Monthly LLM budget reached")
    return _masked(request)


def _problem(exc: LLMError) -> ProblemError:
    if isinstance(exc, LLMDisabledError):
        return ProblemError(503, "llm_unavailable", "AI assistant is disabled")
    if isinstance(exc, LLMUnavailableError):
        return ProblemError(503, "llm_unavailable", "AI assistant is temporarily unavailable")
    if isinstance(exc, LLMBlockedError):
        return ProblemError(422, "llm_blocked", "The AI provider declined to answer")
    if isinstance(exc, LLMInvalidOutputError):
        return ProblemError(502, "llm_invalid_output", "The AI answer could not be validated")
    return ProblemError(502, "llm_error", "AI provider error")


async def complete(ctx: Ctx, *, space_id: uuid.UUID, request: LLMRequest) -> LLMResponse:
    masked, redactions = await _prepare(ctx, space_id, request)
    started = time.perf_counter()
    try:
        response = await ctx.llm.generate(masked)
    except LLMError as exc:
        problem = _problem(exc)
        await _meter(
            ctx,
            space_id=space_id,
            purpose=request.purpose,
            outcome="error",
            redactions=redactions,
            error_code=problem.code,
            started=started,
        )
        raise problem from exc
    await _meter(
        ctx,
        space_id=space_id,
        purpose=request.purpose,
        outcome="ok",
        response=response,
        redactions=redactions,
        started=started,
    )
    return response


async def complete_structured[T: BaseModel](
    ctx: Ctx, *, space_id: uuid.UUID, request: LLMRequest, schema: type[T]
) -> tuple[T, LLMResponse]:
    masked, redactions = await _prepare(ctx, space_id, request)
    started = time.perf_counter()
    try:
        parsed, response = await ctx.llm.generate_structured(masked, schema)
    except LLMError as exc:
        problem = _problem(exc)
        await _meter(
            ctx,
            space_id=space_id,
            purpose=request.purpose,
            outcome="error",
            redactions=redactions,
            error_code=problem.code,
            started=started,
        )
        raise problem from exc
    await _meter(
        ctx,
        space_id=space_id,
        purpose=request.purpose,
        outcome="ok",
        response=response,
        redactions=redactions,
        started=started,
    )
    return parsed, response
