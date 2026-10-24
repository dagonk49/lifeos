"""Cloud gateway: the only path from LifeOS data to a cloud LLM (ADR 0003, ADR 0007).

Before any call: emergency stop, Guardian policy, consent of the space and local masking
of sensitive data. Then the call atomically reserves its share of the monthly token budget
and of either the local daily request caps (free billing: stay under Google's free quotas,
never pay) or the daily/monthly cost budgets (paid billing), with a safety margin (ADR
0010). After the call, the reservation is settled with the real usage, without storing any
content; a reservation whose process died is released by a sweep. Provider failures become
explicit degraded-mode errors.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as time_of_day
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import LLMCall
from lifeos.modules.guardian.policy import evaluate
from lifeos.modules.guardian.privacy import mask
from lifeos.modules.identity.models import Space
from lifeos.modules.llm.base import (
    LLMBillingError,
    LLMBlockedError,
    LLMDisabledError,
    LLMError,
    LLMInvalidOutputError,
    LLMMessage,
    LLMQuotaError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    Tier,
)
from lifeos.observability.metrics import LLM_CALLS, LLM_TOKENS

# Google's daily quotas reset at midnight Pacific time.
QUOTA_TZ = ZoneInfo("America/Los_Angeles")
_MICRO = Decimal("0.000001")
TIERS: tuple[Tier, ...] = ("fast", "deep")
# Calls that reached (or may have reached) the provider: they count against the daily caps.
# A released reservation stays counted: its request may have reached Google (ADR 0010).
REQUEST_OUTCOMES = ("ok", "error", "reserved", "abandoned")
# Input estimate: Gemini averages about 4 characters per token in French; 3 is
# conservative. Each message (and the system instruction) adds a few framing tokens.
_CHARS_PER_TOKEN = 3
_TOKENS_PER_MESSAGE = 8


def month_start(now: datetime, timezone: str) -> datetime:
    local = now.astimezone(ZoneInfo(timezone))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def quota_day_start(now: datetime) -> datetime:
    local = now.astimezone(QUOTA_TZ)
    return datetime.combine(local.date(), time_of_day.min, tzinfo=QUOTA_TZ).astimezone(UTC)


def next_quota_reset(now: datetime) -> datetime:
    """Next midnight in Pacific time (23 or 25 hours away on DST change days)."""
    tomorrow = now.astimezone(QUOTA_TZ).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time_of_day.min, tzinfo=QUOTA_TZ).astimezone(UTC)


def estimate_cost(
    settings: Settings,
    tier: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> Decimal:
    """USD cost estimated from the configured price table (0 on free billing).

    Cached input tokens (implicit caching) are billed at about 10 % of the input price.
    """
    if settings.llm_billing != "paid":
        return Decimal(0)
    prices = {
        "fast": (settings.llm_price_input_fast, settings.llm_price_output_fast),
        "deep": (settings.llm_price_input_deep, settings.llm_price_output_deep),
    }[tier]
    price_in, price_out = Decimal(str(prices[0])), Decimal(str(prices[1]))
    cached = min(cached_input_tokens, input_tokens)
    cost = (
        price_in * (input_tokens - cached)
        + price_in * Decimal("0.1") * cached
        + price_out * output_tokens
    ) / Decimal(1_000_000)
    return cost.quantize(_MICRO)


def daily_request_limit(settings: Settings, tier: Tier) -> int:
    """Requests allowed per day on a tier: the configured cap minus the safety margin."""
    cap = {"fast": settings.llm_daily_requests_fast, "deep": settings.llm_daily_requests_deep}
    return max(0, cap[tier] - settings.llm_quota_margin_requests)


def budget_limit(settings: Settings, budget_usd: float) -> Decimal:
    """A USD budget minus the share kept in reserve (paid billing)."""
    ratio = Decimal(1) - Decimal(str(settings.llm_budget_margin_ratio))
    return (Decimal(str(budget_usd)) * ratio).quantize(_MICRO)


def estimate_input_tokens(request: LLMRequest, extra_chars: int = 0) -> int:
    """Conservative input size of a (masked) request; `extra_chars`: e.g. a JSON schema."""
    chars = len(request.system or "") + sum(len(m.text) for m in request.messages)
    framing = _TOKENS_PER_MESSAGE * (len(request.messages) + 1)
    return -(-(chars + extra_chars) // _CHARS_PER_TOKEN) + framing


def reservation_size(
    settings: Settings, request: LLMRequest, extra_chars: int = 0
) -> tuple[int, Decimal]:
    """Tokens and USD cost reserved before a call.

    Output: the request's own max_output_tokens (thinking included, a true bound), else
    the configured per-tier estimate.
    """
    input_tokens = estimate_input_tokens(request, extra_chars)
    output_tokens = request.max_output_tokens
    if output_tokens is None:
        output_tokens = {
            "fast": settings.llm_reserve_output_tokens_fast,
            "deep": settings.llm_reserve_output_tokens_deep,
        }[request.tier]
    cost = estimate_cost(settings, request.tier, input_tokens, output_tokens)
    return input_tokens + output_tokens, cost


# Counters: one SQL for the reservation check and for the usage summary. A pending
# reservation counts with its estimate; a settled call with its real usage.


async def _tokens_since(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> int:
    used = await session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (LLMCall.outcome == "reserved", LLMCall.reserved_tokens),
                        else_=LLMCall.input_tokens + LLMCall.output_tokens,
                    )
                ),
                0,
            )
        ).where(
            LLMCall.user_id == user_id,
            LLMCall.created_at >= since,
            # Failed calls may have consumed tokens too (blocked, truncated, invalid).
            LLMCall.outcome.in_(REQUEST_OUTCOMES),
        )
    )
    return int(used or 0)


async def _requests_since(
    session: AsyncSession, user_id: uuid.UUID, tier: Tier, since: datetime
) -> int:
    count = await session.scalar(
        select(func.count()).where(
            LLMCall.user_id == user_id,
            LLMCall.tier == tier,
            LLMCall.created_at >= since,
            # Local refusals do not consume the provider's quota.
            LLMCall.outcome.in_(REQUEST_OUTCOMES),
        )
    )
    return int(count or 0)


async def _cost_since(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> Decimal:
    cost = await session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (LLMCall.outcome == "reserved", LLMCall.reserved_cost_usd),
                        else_=LLMCall.cost_usd,
                    )
                ),
                0,
            )
        ).where(LLMCall.user_id == user_id, LLMCall.created_at >= since)
    )
    return Decimal(cost or 0)


async def _in_flight(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> dict[str, int]:
    rows = await session.execute(
        select(LLMCall.tier, func.count())
        .where(
            LLMCall.user_id == user_id,
            LLMCall.outcome == "reserved",
            LLMCall.created_at >= since,
        )
        .group_by(LLMCall.tier)
    )
    counts = {tier: int(count) for tier, count in rows}
    return {tier: counts.get(tier, 0) for tier in TIERS}


async def tokens_used_this_month(ctx: Ctx) -> int:
    return await _tokens_since(ctx.db, ctx.user.id, month_start(utcnow(), ctx.user.timezone))


async def requests_today(ctx: Ctx, tier: Tier) -> int:
    return await _requests_since(ctx.db, ctx.user.id, tier, quota_day_start(utcnow()))


async def cost_since(ctx: Ctx, since: datetime) -> Decimal:
    return await _cost_since(ctx.db, ctx.user.id, since)


async def release_stale_reservations(
    session: AsyncSession,
    *,
    ttl: timedelta,
    now: datetime | None = None,
    user_id: uuid.UUID | None = None,
) -> int:
    """Releases reservations older than `ttl` (their process died during the call).

    Their tokens and cost no longer count; the request still does (ADR 0010). A call that
    settles afterwards still records its real usage.
    """
    moment = now or utcnow()
    stmt = (
        update(LLMCall)
        .where(LLMCall.outcome == "reserved", LLMCall.created_at < moment - ttl)
        .values(outcome="abandoned", settled_at=moment)
    )
    if user_id is not None:
        stmt = stmt.where(LLMCall.user_id == user_id)
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


@dataclass(frozen=True)
class _Prepared:
    request: LLMRequest
    redactions: int


async def _meter(
    ctx: Ctx, *, space_id: uuid.UUID, request: LLMRequest, outcome: str, error_code: str
) -> None:
    """A local refusal: nothing reached the provider."""
    LLM_CALLS.labels(ctx.llm.name, request.purpose, outcome).inc()
    # Detached: the refusal is recorded even if the request fails afterwards.
    async with ctx.database.sessionmaker() as session, session.begin():
        session.add(
            LLMCall(
                user_id=ctx.user.id,
                space_id=space_id,
                purpose=request.purpose,
                provider=ctx.llm.name,
                tier=request.tier,
                outcome=outcome,
                error_code=error_code,
                created_at=utcnow(),
            )
        )


async def _refuse(
    ctx: Ctx,
    space_id: uuid.UUID,
    request: LLMRequest,
    *,
    outcome: str,
    problem: ProblemError,
    audit_denial: bool = True,
) -> ProblemError:
    if audit_denial:
        await audit.record_detached(
            ctx.database,
            action="llm.cloud_call",
            outcome="denied",
            actor_type=ctx.principal.kind,
            actor_id=ctx.principal.actor_id,
            user_id=ctx.user.id,
            space_id=space_id,
            details={"purpose": request.purpose, "reason": problem.code},
        )
    await _meter(ctx, space_id=space_id, request=request, outcome=outcome, error_code=problem.code)
    return problem


def _masked(request: LLMRequest) -> _Prepared:
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
    return _Prepared(request.model_copy(update={"system": system, "messages": messages}), total)


async def _prepare(ctx: Ctx, space_id: uuid.UUID, request: LLMRequest) -> _Prepared:
    """Checks that need no counter: emergency stop, policy, consent; then local masking."""
    if ctx.user.llm_paused:
        raise await _refuse(
            ctx,
            space_id,
            request,
            outcome="denied",
            problem=ProblemError(503, "llm_paused", "The AI assistant is paused"),
        )
    decision = evaluate(ctx.principal, "llm.cloud_call", space_id)
    if not decision.allowed:
        raise await _refuse(
            ctx,
            space_id,
            request,
            outcome="denied",
            problem=ProblemError(403, "cloud_call_denied", "Cloud call not allowed"),
        )
    space = await ctx.db.get(Space, space_id)
    if space is None or not space.cloud_ai_enabled:
        raise await _refuse(
            ctx,
            space_id,
            request,
            outcome="denied",
            problem=ProblemError(
                403,
                "cloud_consent_required",
                "Cloud AI is not enabled for this space",
                "Enable cloud AI for this space in the settings to use the assistant.",
            ),
        )
    return _masked(request)


# Two-key advisory locks (class, object) never collide with the single-key ones used
# elsewhere (e.g. reviews hold one during their LLM call): no self-deadlock by hash luck.
QUOTA_LOCK_CLASS = 0x4C4C4D51  # "LLMQ"


def _quota_lock(user_id: uuid.UUID) -> Any:
    # Counters are per user (single owner: Google's quotas are per project).
    return func.pg_advisory_xact_lock(QUOTA_LOCK_CLASS, func.hashtext(str(user_id)))


async def _quota_refusal(
    session: AsyncSession, ctx: Ctx, tier: Tier, tokens: int, cost: Decimal, now: datetime
) -> ProblemError | None:
    """Whether a call of this size still fits, counting pending reservations."""
    settings = ctx.settings
    user_id = ctx.user.id
    month = month_start(now, ctx.user.timezone)
    if await _tokens_since(session, user_id, month) + tokens > ctx.user.llm_monthly_token_budget:
        return ProblemError(429, "llm_budget_exceeded", "Monthly LLM budget reached")
    if settings.llm_billing == "free":
        limit = daily_request_limit(settings, tier)
        if await _requests_since(session, user_id, tier, quota_day_start(now)) + 1 > limit:
            return ProblemError(
                429,
                "llm_daily_quota_reached",
                "Daily AI quota reached",
                "The free quota resets every day at 9:00 (Paris time).",
                extra={"tier": tier, "limit": limit},
            )
        return None
    day_budget = budget_limit(settings, settings.llm_daily_budget_usd)
    month_budget = budget_limit(settings, settings.llm_monthly_budget_usd)
    if (
        await _cost_since(session, user_id, quota_day_start(now)) + cost > day_budget
        or await _cost_since(session, user_id, month) + cost > month_budget
    ):
        return ProblemError(429, "llm_budget_exceeded", "AI cost budget reached")
    return None


async def _reserve(
    ctx: Ctx, space_id: uuid.UUID, request: LLMRequest, prepared: _Prepared, extra_chars: int
) -> uuid.UUID:
    """Atomically checks the caps and reserves this call's share (ADR 0010).

    Own short transaction, serialized per user by an advisory lock: two simultaneous
    calls cannot both take the last slot. The lock is released at commit, before the
    provider call; the request's own transaction is never involved.
    """
    tokens, cost = reservation_size(ctx.settings, prepared.request, extra_chars)
    reservation_id = uuid.uuid4()
    async with ctx.database.sessionmaker() as session, session.begin():
        await session.execute(select(_quota_lock(ctx.user.id)))
        now = utcnow()
        await release_stale_reservations(
            session, ttl=ctx.settings.llm_reservation_ttl, now=now, user_id=ctx.user.id
        )
        refusal = await _quota_refusal(session, ctx, request.tier, tokens, cost, now)
        if refusal is None:
            session.add(
                LLMCall(
                    id=reservation_id,
                    user_id=ctx.user.id,
                    space_id=space_id,
                    purpose=request.purpose,
                    provider=ctx.llm.name,
                    tier=request.tier,
                    outcome="reserved",
                    reserved_tokens=tokens,
                    reserved_cost_usd=cost,
                    redactions=prepared.redactions,
                    created_at=now,
                )
            )
    if refusal is not None:
        raise await _refuse(
            ctx, space_id, request, outcome="budget_exceeded", audit_denial=False, problem=refusal
        )
    return reservation_id


async def _settle(
    ctx: Ctx,
    reservation_id: uuid.UUID,
    request: LLMRequest,
    *,
    outcome: str,
    started: float,
    response: LLMResponse | None = None,
    error_code: str | None = None,
) -> None:
    """Replaces the reservation with the real usage (even if it was released meanwhile)."""
    provider = ctx.llm.name
    LLM_CALLS.labels(provider, request.purpose, outcome).inc()
    input_tokens = response.usage.input_tokens if response else 0
    cached_tokens = response.usage.cached_input_tokens if response else 0
    output_tokens = response.usage.output_tokens if response else 0
    if response is not None:
        LLM_TOKENS.labels(provider, "input").inc(input_tokens)
        LLM_TOKENS.labels(provider, "output").inc(output_tokens)
    # Detached: the call happened (and may have cost tokens) even if the request fails.
    async with ctx.database.sessionmaker() as session, session.begin():
        await session.execute(
            update(LLMCall)
            .where(
                LLMCall.id == reservation_id,
                LLMCall.outcome.in_(("reserved", "abandoned")),
            )
            .values(
                outcome=outcome,
                model=response.model if response else None,
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                cost_usd=estimate_cost(
                    ctx.settings, request.tier, input_tokens, output_tokens, cached_tokens
                ),
                error_code=error_code,
                duration_ms=int((time.perf_counter() - started) * 1000),
                settled_at=utcnow(),
            )
        )


def _problem(exc: LLMError) -> ProblemError:
    if isinstance(exc, LLMDisabledError):
        return ProblemError(503, "llm_unavailable", "AI assistant is disabled")
    if isinstance(exc, LLMBillingError):
        return ProblemError(503, "llm_billing_exhausted", "AI prepaid balance or spend cap reached")
    if isinstance(exc, LLMQuotaError):
        extra: dict[str, Any] = {"daily": exc.daily}
        headers = None
        if exc.retry_after is not None and not exc.daily:
            extra["retry_after"] = exc.retry_after
            headers = {"Retry-After": str(exc.retry_after)}
        return ProblemError(
            503,
            "llm_quota_exceeded",
            "The AI provider's quota is exhausted",
            "Daily quota: it resets around 9:00 (Paris time)." if exc.daily else None,
            headers=headers,
            extra=extra,
        )
    if isinstance(exc, LLMUnavailableError):
        return ProblemError(503, "llm_unavailable", "AI assistant is temporarily unavailable")
    if isinstance(exc, LLMBlockedError):
        return ProblemError(422, "llm_blocked", "The AI provider declined to answer")
    if isinstance(exc, LLMInvalidOutputError):
        return ProblemError(502, "llm_invalid_output", "The AI answer could not be validated")
    return ProblemError(502, "llm_error", "AI provider error")


async def _run[R](
    ctx: Ctx,
    space_id: uuid.UUID,
    request: LLMRequest,
    call: Any,
    result_response: Any,
    *,
    extra_input_chars: int = 0,
) -> R:
    prepared = await _prepare(ctx, space_id, request)
    reservation = await _reserve(ctx, space_id, request, prepared, extra_input_chars)
    started = time.perf_counter()
    # Settlements are shielded: a cancelled request still settles its reservation.
    try:
        result: R = await call(prepared.request)
    except LLMError as exc:
        problem = _problem(exc)
        await asyncio.shield(
            _settle(
                ctx,
                reservation,
                request,
                outcome="error",
                started=started,
                response=exc.response,  # billed tokens of an unusable answer
                error_code=problem.code,
            )
        )
        raise problem from exc
    except BaseException:
        # Unexpected failure or cancellation: the request may have reached the provider,
        # so it counts against the daily caps.
        await asyncio.shield(
            _settle(
                ctx, reservation, request, outcome="error", started=started, error_code="llm_error"
            )
        )
        raise
    await asyncio.shield(
        _settle(
            ctx,
            reservation,
            request,
            outcome="ok",
            started=started,
            response=result_response(result),
        )
    )
    return result


async def complete(ctx: Ctx, *, space_id: uuid.UUID, request: LLMRequest) -> LLMResponse:
    response: LLMResponse = await _run(ctx, space_id, request, ctx.llm.generate, lambda r: r)
    return response


async def complete_structured[T: BaseModel](
    ctx: Ctx, *, space_id: uuid.UUID, request: LLMRequest, schema: type[T]
) -> tuple[T, LLMResponse]:
    async def call(prepared: LLMRequest) -> tuple[T, LLMResponse]:
        return await ctx.llm.generate_structured(prepared, schema)

    # The response schema is sent with the request: it counts as input.
    schema_chars = len(json.dumps(schema.model_json_schema()))
    result: tuple[T, LLMResponse] = await _run(
        ctx, space_id, request, call, lambda r: r[1], extra_input_chars=schema_chars
    )
    return result


async def usage_summary(ctx: Ctx) -> dict[str, Any]:
    """What the owner needs to see: quotas, costs, budgets, emergency stop.

    Limits are the enforced ones (configured caps and budgets minus the safety margins);
    usage includes the estimates reserved by calls still running.
    """
    settings = ctx.settings
    now = utcnow()
    user_id = ctx.user.id
    day = quota_day_start(now)
    month = month_start(now, ctx.user.timezone)
    models: dict[str, str | None] = {"fast": None, "deep": None}
    provider_models = getattr(ctx.llm, "models", None)
    if isinstance(provider_models, dict):
        models = {k: str(v) for k, v in provider_models.items()}
    return {
        "provider": ctx.llm.name,
        "enabled": ctx.llm.name != "disabled",
        "paused": ctx.user.llm_paused,
        "billing": settings.llm_billing,
        "models": models,
        "requests_today": {
            tier: await _requests_since(ctx.db, user_id, tier, day) for tier in TIERS
        },
        "in_flight": await _in_flight(ctx.db, user_id, now - settings.llm_reservation_ttl),
        "daily_request_caps": {tier: daily_request_limit(settings, tier) for tier in TIERS},
        "daily_request_margin": settings.llm_quota_margin_requests,
        "quota_resets_at": next_quota_reset(now),
        "month_tokens_used": await _tokens_since(ctx.db, user_id, month),
        "month_token_budget": ctx.user.llm_monthly_token_budget,
        "cost_today_usd": str(await _cost_since(ctx.db, user_id, day)),
        "cost_month_usd": str(await _cost_since(ctx.db, user_id, month)),
        "daily_budget_usd": float(budget_limit(settings, settings.llm_daily_budget_usd)),
        "monthly_budget_usd": float(budget_limit(settings, settings.llm_monthly_budget_usd)),
        "budget_margin_ratio": settings.llm_budget_margin_ratio,
    }
