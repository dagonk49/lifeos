"""Cost control: tiers, thinking levels, free quotas, paid budgets, emergency stop."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.db.session import Database
from lifeos.modules.guardian import gateway
from lifeos.modules.guardian.models import LLMCall
from lifeos.modules.identity.models import Space, User
from lifeos.modules.journal.schemas import JournalAnalysis
from lifeos.modules.llm.base import (
    LLMBillingError,
    LLMBlockedError,
    LLMInvalidOutputError,
    LLMMessage,
    LLMQuotaError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    LLMUsage,
)
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.llm.gemini import GeminiProvider
from lifeos.modules.llm.schema import gemini_schema
from lifeos.modules.reviews.schemas import ReviewNarrative

from .conftest import Owner, enable_cloud, make_ctx


def _walk(node: Any) -> list[dict[str, Any]]:
    found = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found += _walk(value)
    elif isinstance(node, list):
        for value in node:
            found += _walk(value)
    return found


@pytest.mark.parametrize("model", [ReviewNarrative, JournalAnalysis])
def test_schema_is_reduced_to_the_gemini_subset(model: Any) -> None:
    schema = gemini_schema(model.model_json_schema())
    allowed = {
        "type",
        "format",
        "title",
        "description",
        "enum",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "anyOf",
        "properties",
        "additionalProperties",
        "required",
        "propertyOrdering",
    }
    for node in _walk(schema):
        if "type" in node or "anyOf" in node:
            assert set(node) <= allowed, set(node) - allowed
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text and '"default"' not in text
    assert schema["propertyOrdering"] == list(model.model_fields)


def test_nested_models_are_inlined() -> None:
    schema = gemini_schema(ReviewNarrative.model_json_schema())
    suggestion = schema["properties"]["suggestions"]["items"]
    assert suggestion["type"] == "object"
    assert set(suggestion["properties"]) == {"action", "why"}


# --- Gemini adapter -------------------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "llm_provider": "gemini",
        "gemini_model": "gemini-fast-test",
        "gemini_model_deep": "gemini-deep-test",
        "gemini_api_key": SecretStr("k"),
    }
    return Settings(**{**base, **overrides})


class _Models:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _ok(text: str = "ok") -> Any:
    return SimpleNamespace(
        text=text,
        model_version=None,
        prompt_feedback=None,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
        usage_metadata=SimpleNamespace(
            prompt_token_count=1000,
            candidates_token_count=100,
            thoughts_token_count=50,
            cached_content_token_count=None,
        ),
    )


def _provider(settings: Settings, *results: Any) -> tuple[GeminiProvider, _Models]:
    provider = GeminiProvider(settings)
    models = _Models(*results)
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=models))  # type: ignore[assignment]
    return provider, models


def _req(tier: str = "fast", background: bool = False) -> LLMRequest:
    return LLMRequest(
        purpose="t",
        messages=[LLMMessage(role="user", text="q")],
        tier=tier,  # type: ignore[arg-type]
        background=background,
    )


async def test_tiers_select_model_and_thinking_level() -> None:
    provider, models = _provider(_settings(), _ok(), _ok())
    fast = await provider.generate(_req("fast"))
    await provider.generate(_req("deep"))
    assert models.calls[0]["model"] == "gemini-fast-test"
    assert models.calls[0]["config"].thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    assert models.calls[1]["model"] == "gemini-deep-test"
    assert models.calls[1]["config"].thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert fast.model == "gemini-fast-test"
    assert fast.usage.output_tokens == 150  # thinking tokens are billed as output


async def test_flex_only_for_background_jobs_on_paid_billing() -> None:
    free, free_models = _provider(_settings(), _ok())
    await free.generate(_req("deep", background=True))
    assert free_models.calls[0]["config"].service_tier is None
    paid, paid_models = _provider(_settings(llm_billing="paid"), _ok(), _ok())
    await paid.generate(_req("deep", background=True))
    await paid.generate(_req("deep"))
    assert paid_models.calls[0]["config"].service_tier == types.ServiceTier.FLEX
    assert paid_models.calls[1]["config"].service_tier is None


async def test_unsupported_minimal_thinking_falls_back_to_low() -> None:
    rejected = genai_errors.ClientError(
        400, {"error": {"message": "Thinking level MINIMAL is not supported for this model"}}
    )
    provider, models = _provider(_settings(), rejected, _ok(), _ok())
    await provider.generate(_req("fast"))
    await provider.generate(_req("fast"))
    levels = [c["config"].thinking_config.thinking_level for c in models.calls]
    # The accepted level is remembered: one extra request per process, not per call.
    assert levels == [types.ThinkingLevel.MINIMAL, types.ThinkingLevel.LOW, types.ThinkingLevel.LOW]


async def test_billing_and_quota_errors_are_explicit() -> None:
    provider, _ = _provider(
        _settings(), genai_errors.ClientError(402, {"error": {"message": "prepaid"}})
    )
    with pytest.raises(LLMBillingError):
        await provider.generate(_req())
    provider, _ = _provider(
        _settings(), genai_errors.ClientError(429, {"error": {"message": "quota"}})
    )
    with pytest.raises(LLMQuotaError):
        await provider.generate(_req())


# --- Gateway budgets ----------------------------------------------------------------------


async def _ctx(
    database: Database,
    db: AsyncSession,
    owner: Owner,
    llm: FakeProvider,
    settings: Settings,
) -> tuple[Any, uuid.UUID]:
    user = await db.get(User, owner.user.id)
    assert user is not None
    ctx = await make_ctx(database, db, settings, user, llm)
    space_id = next(iter(ctx.principal.space_ids))
    space = await db.get(Space, space_id)
    assert space is not None
    space.cloud_ai_enabled = True
    await db.commit()
    return ctx, space_id


def _chat(tier: str = "fast") -> LLMRequest:
    return LLMRequest(
        purpose="test",
        messages=[LLMMessage(role="user", text="Bonjour")],
        tier=tier,  # type: ignore[arg-type]
    )


async def test_free_billing_enforces_local_daily_caps_per_tier(
    database: Database,
    db: AsyncSession,
    owner: Owner,
    fake_llm: FakeProvider,
    settings: Settings,
) -> None:
    capped = settings.model_copy(
        update={"llm_daily_requests_fast": 2, "llm_daily_requests_deep": 1}
    )
    ctx, space = await _ctx(database, db, owner, fake_llm, capped)
    await gateway.complete(ctx, space_id=space, request=_chat())
    await gateway.complete(ctx, space_id=space, request=_chat())
    with pytest.raises(ProblemError) as exc:
        await gateway.complete(ctx, space_id=space, request=_chat())
    assert exc.value.code == "llm_daily_quota_reached"
    assert exc.value.extra == {"tier": "fast", "limit": 2}
    # The deep tier has its own quota; local refusals do not consume Google's quota.
    await gateway.complete(ctx, space_id=space, request=_chat("deep"))
    assert len(fake_llm.requests) == 3
    calls = list(await db.scalars(select(LLMCall).order_by(LLMCall.created_at)))
    assert [(c.tier, c.outcome) for c in calls] == [
        ("fast", "ok"),
        ("fast", "ok"),
        ("fast", "budget_exceeded"),
        ("deep", "ok"),
    ]
    assert all(c.cost_usd == 0 for c in calls)  # free billing costs nothing


async def test_paid_billing_estimates_cost_and_stops_at_the_budget(
    database: Database,
    db: AsyncSession,
    owner: Owner,
    fake_llm: FakeProvider,
    settings: Settings,
) -> None:
    paid = settings.model_copy(
        update={
            "llm_billing": "paid",
            "llm_price_input_fast": 1_000.0,  # absurd prices to reach the cap quickly
            "llm_price_output_fast": 1_000.0,
            "llm_daily_budget_usd": 0.001,
        }
    )
    ctx, space = await _ctx(database, db, owner, fake_llm, paid)
    await gateway.complete(ctx, space_id=space, request=_chat())
    call = await db.scalar(select(LLMCall))
    assert call is not None and call.cost_usd > Decimal("0.001")
    with pytest.raises(ProblemError) as exc:
        await gateway.complete(ctx, space_id=space, request=_chat())
    assert exc.value.code == "llm_budget_exceeded"
    summary = await gateway.usage_summary(ctx)
    assert summary["billing"] == "paid"
    assert Decimal(summary["cost_today_usd"]) == call.cost_usd


def test_cost_estimate() -> None:
    paid = Settings(llm_billing="paid")
    # 1M input + 1M output on the fast tier at the default prices.
    assert gateway.estimate_cost(paid, "fast", 1_000_000, 1_000_000) == Decimal("2.800000")
    assert gateway.estimate_cost(Settings(), "fast", 1_000_000, 1_000_000) == Decimal(0)


async def test_emergency_stop(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    await enable_cloud(client, spaces["personal"])
    paused = await client.patch("/v1/settings", json={"llm_paused": True})
    assert paused.json()["llm_paused"] is True
    conversation = (
        await client.post("/v1/chat/conversations", json={"space_id": spaces["personal"]})
    ).json()["id"]
    sent = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages", json={"content": "Bonjour"}
        )
    ).json()
    assert sent["error"]["code"] == "llm_paused"
    assert fake_llm.requests == []
    usage = (await client.get("/v1/guardian/llm-usage")).json()
    assert usage["paused"] is True and usage["billing"] == "free"
    assert usage["daily_request_caps"] == {"fast": 400, "deep": 15}

    await client.patch("/v1/settings", json={"llm_paused": False})
    resumed = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages", json={"content": "Bonjour"}
        )
    ).json()
    assert resumed["error"] is None
    usage = (await client.get("/v1/guardian/llm-usage")).json()
    assert usage["requests_today"] == {"fast": 1, "deep": 0}


async def test_quota_errors_are_classified() -> None:
    daily = genai_errors.ClientError(
        429,
        {
            "error": {
                "message": "Quota exceeded",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                        ],
                    },
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "51s"},
                ],
            }
        },
    )
    provider, _ = _provider(_settings(), daily)
    with pytest.raises(LLMQuotaError) as exc:
        await provider.generate(_req())
    assert exc.value.daily is True

    per_minute = genai_errors.ClientError(
        429,
        {
            "error": {
                "message": "Please retry in 12s",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}
                        ],
                    },
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12.5s"},
                ],
            }
        },
    )
    provider, _ = _provider(_settings(), per_minute)
    with pytest.raises(LLMQuotaError) as exc:
        await provider.generate(_req())
    assert exc.value.daily is False and exc.value.retry_after == 12


async def test_truncated_answer_and_no_temperature() -> None:
    truncated = _ok("")
    truncated.candidates = [SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))]
    provider, models = _provider(_settings(), truncated)
    request = _req()
    request.temperature = 0.7
    with pytest.raises(LLMInvalidOutputError) as exc:
        await provider.generate(request)
    # Thinking tokens were billed: the error carries the usage for metering.
    assert exc.value.response is not None
    assert exc.value.response.usage.output_tokens == 150
    config = models.calls[0]["config"]
    assert config.temperature is None
    assert config.thinking_config.include_thoughts is False


def test_cached_input_is_cheaper() -> None:
    paid = Settings(llm_billing="paid")
    full = gateway.estimate_cost(paid, "fast", 1_000_000, 0)
    cached = gateway.estimate_cost(paid, "fast", 1_000_000, 0, cached_input_tokens=1_000_000)
    assert full == Decimal("0.300000") and cached == Decimal("0.030000")


async def test_network_failures_are_mapped() -> None:
    provider, _ = _provider(_settings(), httpx.ReadTimeout("slow"))
    with pytest.raises(LLMUnavailableError, match="timeout"):
        await provider.generate(_req())
    provider, _ = _provider(_settings(), httpx.ConnectError("refused"))
    with pytest.raises(LLMUnavailableError, match="unreachable"):
        await provider.generate(_req())


def test_quota_reset_is_the_next_pacific_midnight_even_on_dst_days() -> None:
    # 2026-11-01: end of US daylight saving time (the Pacific day lasts 25 hours).
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    assert gateway.quota_day_start(now) == datetime(2026, 11, 1, 7, 0, tzinfo=UTC)
    assert gateway.next_quota_reset(now) == datetime(2026, 11, 2, 8, 0, tzinfo=UTC)


async def test_failed_calls_are_metered_with_their_usage(
    database: Database,
    db: AsyncSession,
    owner: Owner,
    fake_llm: FakeProvider,
    settings: Settings,
) -> None:
    ctx, space = await _ctx(database, db, owner, fake_llm, settings)
    billed = LLMResponse(
        text="",
        model="m",
        usage=LLMUsage(input_tokens=700, output_tokens=300),
        finish_reason="SAFETY",
    )
    fake_llm.push(LLMBlockedError("SAFETY").with_response(billed))
    with pytest.raises(ProblemError):
        await gateway.complete(ctx, space_id=space, request=_chat())
    assert await gateway.tokens_used_this_month(ctx) == 1000

    class Exploding(FakeProvider):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("unexpected SDK error")

    ctx.llm = Exploding()
    with pytest.raises(RuntimeError):
        await gateway.complete(ctx, space_id=space, request=_chat())
    calls = list(await db.scalars(select(LLMCall).order_by(LLMCall.created_at)))
    assert [(c.outcome, c.error_code, c.input_tokens) for c in calls] == [
        ("error", "llm_blocked", 700),
        ("error", "llm_error", 0),
    ]
    # Both reached (or may have reached) the provider: they count against the daily cap.
    assert await gateway.requests_today(ctx, "fast") == 2
