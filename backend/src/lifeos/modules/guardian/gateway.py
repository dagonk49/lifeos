"""Cloud gateway: the only path from LifeOS data to a cloud LLM (ADR 0003, ADR 0007).

Before any call: emergency stop, Guardian policy, consent of the space, monthly token
budget, and either the local daily request caps (free billing: stay under Google's free
quotas, never pay) or the daily/monthly cost budgets (paid billing). Then local masking of
sensitive data, the call, and metering without storing any content. Provider failures
become explicit degraded-mode errors.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select

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


def month_start(now: datetime, timezone: str) -> datetime:
    local = now.astimezone(ZoneInfo(timezone))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def quota_day_start(now: datetime) -> datetime:
    local = now.astimezone(QUOTA_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


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


async def requests_today(ctx: Ctx, tier: Tier) -> int:
    count = await ctx.db.scalar(
        select(func.count()).where(
            LLMCall.user_id == ctx.user.id,
            LLMCall.tier == tier,
            LLMCall.created_at >= quota_day_start(utcnow()),
            # Calls that reached the provider; local refusals do not consume its quota.
            LLMCall.outcome.in_(("ok", "error")),
        )
    )
    return int(count or 0)


async def cost_since(ctx: Ctx, since: datetime) -> Decimal:
    cost = await ctx.db.scalar(
        select(func.coalesce(func.sum(LLMCall.cost_usd), 0)).where(
            LLMCall.user_id == ctx.user.id, LLMCall.created_at >= since
        )
    )
    return Decimal(cost or 0)


@dataclass(frozen=True)
class _Prepared:
    request: LLMRequest
    redactions: int


async def _meter(
    ctx: Ctx,
    *,
    space_id: uuid.UUID,
    request: LLMRequest,
    outcome: str,
    response: LLMResponse | None = None,
    redactions: int = 0,
    error_code: str | None = None,
    started: float | None = None,
) -> None:
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
        session.add(
            LLMCall(
                user_id=ctx.user.id,
                space_id=space_id,
                purpose=request.purpose,
                provider=provider,
                tier=request.tier,
                model=response.model if response else None,
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                cost_usd=estimate_cost(
                    ctx.settings, request.tier, input_tokens, output_tokens, cached_tokens
                ),
                redactions=redactions,
                outcome=outcome,
                error_code=error_code,
                duration_ms=int((time.perf_counter() - started) * 1000) if started else 0,
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
    settings = ctx.settings
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
    if await tokens_used_this_month(ctx) >= ctx.user.llm_monthly_token_budget:
        raise await _refuse(
            ctx,
            space_id,
            request,
            outcome="budget_exceeded",
            audit_denial=False,
            problem=ProblemError(429, "llm_budget_exceeded", "Monthly LLM budget reached"),
        )
    if settings.llm_billing == "free":
        cap = {
            "fast": settings.llm_daily_requests_fast,
            "deep": settings.llm_daily_requests_deep,
        }[request.tier]
        if await requests_today(ctx, request.tier) >= cap:
            raise await _refuse(
                ctx,
                space_id,
                request,
                outcome="budget_exceeded",
                audit_denial=False,
                problem=ProblemError(
                    429,
                    "llm_daily_quota_reached",
                    "Daily AI quota reached",
                    "The free quota resets every day at 9:00 (Paris time).",
                    extra={"tier": request.tier, "limit": cap},
                ),
            )
    else:
        now = utcnow()
        if await cost_since(ctx, quota_day_start(now)) >= Decimal(
            str(settings.llm_daily_budget_usd)
        ) or await cost_since(ctx, month_start(now, ctx.user.timezone)) >= Decimal(
            str(settings.llm_monthly_budget_usd)
        ):
            raise await _refuse(
                ctx,
                space_id,
                request,
                outcome="budget_exceeded",
                audit_denial=False,
                problem=ProblemError(429, "llm_budget_exceeded", "AI cost budget reached"),
            )
    return _masked(request)


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
) -> R:
    prepared = await _prepare(ctx, space_id, request)
    started = time.perf_counter()
    try:
        result: R = await call(prepared.request)
    except LLMError as exc:
        problem = _problem(exc)
        await _meter(
            ctx,
            space_id=space_id,
            request=request,
            outcome="error",
            redactions=prepared.redactions,
            error_code=problem.code,
            started=started,
        )
        raise problem from exc
    await _meter(
        ctx,
        space_id=space_id,
        request=request,
        outcome="ok",
        response=result_response(result),
        redactions=prepared.redactions,
        started=started,
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

    result: tuple[T, LLMResponse] = await _run(ctx, space_id, request, call, lambda r: r[1])
    return result


async def usage_summary(ctx: Ctx) -> dict[str, Any]:
    """What the owner needs to see: quotas, costs, budgets, emergency stop."""
    settings = ctx.settings
    now = utcnow()
    models: dict[str, str | None] = {"fast": None, "deep": None}
    provider_models = getattr(ctx.llm, "models", None)
    if isinstance(provider_models, dict):
        models = {k: str(v) for k, v in provider_models.items()}
    reset = quota_day_start(now) + timedelta(days=1)
    return {
        "provider": ctx.llm.name,
        "enabled": ctx.llm.name != "disabled",
        "paused": ctx.user.llm_paused,
        "billing": settings.llm_billing,
        "models": models,
        "requests_today": {
            "fast": await requests_today(ctx, "fast"),
            "deep": await requests_today(ctx, "deep"),
        },
        "daily_request_caps": {
            "fast": settings.llm_daily_requests_fast,
            "deep": settings.llm_daily_requests_deep,
        },
        "quota_resets_at": reset,
        "month_tokens_used": await tokens_used_this_month(ctx),
        "month_token_budget": ctx.user.llm_monthly_token_budget,
        "cost_today_usd": str(await cost_since(ctx, quota_day_start(now))),
        "cost_month_usd": str(await cost_since(ctx, month_start(now, ctx.user.timezone))),
        "daily_budget_usd": settings.llm_daily_budget_usd,
        "monthly_budget_usd": settings.llm_monthly_budget_usd,
    }
