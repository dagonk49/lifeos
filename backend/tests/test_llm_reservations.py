"""Atomic reservation of the LLM caps (ADR 0010): concurrency, margins, settlement, release."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.modules.guardian import gateway
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import LLMCall
from lifeos.modules.identity.models import Space, User
from lifeos.modules.llm.base import LLMMessage, LLMRequest, LLMResponse
from lifeos.modules.llm.fake import FakeProvider

from .conftest import Owner, make_ctx

WAIT = 5  # seconds: a deadlock or a missed event fails the test instead of hanging


class GatedProvider(FakeProvider):
    """Holds every call inside the provider until `release` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.entered.set()
        await self.release.wait()
        return await super().generate(request)


class SlowProvider(FakeProvider):
    async def generate(self, request: LLMRequest) -> LLMResponse:
        await asyncio.sleep(0.2)
        return await super().generate(request)


def _chat(tier: str = "fast", **extra: Any) -> LLMRequest:
    return LLMRequest(
        purpose="test",
        messages=[LLMMessage(role="user", text="Bonjour")],
        tier=tier,  # type: ignore[arg-type]
        **extra,
    )


async def _enable_cloud(database: Database, owner: Owner) -> uuid.UUID:
    async with database.sessionmaker() as session, session.begin():
        space = await session.scalar(select(Space).where(Space.user_id == owner.user.id))
        assert space is not None
        space.cloud_ai_enabled = True
        return space.id


@asynccontextmanager
async def _ctx(
    database: Database, owner: Owner, settings: Settings, llm: FakeProvider
) -> AsyncIterator[Ctx]:
    """A request context on its own session, like one HTTP request or one worker job."""
    async with database.sessionmaker() as session:
        user = await session.get(User, owner.user.id)
        assert user is not None
        yield await make_ctx(database, session, settings, user, llm)


async def _calls(db: AsyncSession) -> list[LLMCall]:
    db.expire_all()
    return list(await db.scalars(select(LLMCall).order_by(LLMCall.created_at)))


def _free(settings: Settings, cap: int, margin: int = 0) -> Settings:
    return settings.model_copy(
        update={"llm_daily_requests_fast": cap, "llm_quota_margin_requests": margin}
    )


async def test_a_call_in_flight_holds_its_slot_without_holding_the_lock(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    capped = _free(settings, cap=2)
    gated, fast = GatedProvider(), FakeProvider()
    async with (
        _ctx(database, owner, capped, gated) as ctx_a,
        _ctx(database, owner, capped, fast) as ctx_b,
    ):
        first = asyncio.create_task(gateway.complete(ctx_a, space_id=space, request=_chat()))
        await asyncio.wait_for(gated.entered.wait(), WAIT)
        # The first call waits inside the provider: the quota lock is already released,
        # so a second call goes through at once...
        await asyncio.wait_for(gateway.complete(ctx_b, space_id=space, request=_chat()), WAIT)
        usage = await gateway.usage_summary(ctx_b)
        assert usage["in_flight"] == {"fast": 1, "deep": 0}
        assert usage["requests_today"] == {"fast": 2, "deep": 0}
        # ...but the slot of the call in flight is taken: the third one is refused.
        with pytest.raises(ProblemError) as exc:
            await asyncio.wait_for(gateway.complete(ctx_b, space_id=space, request=_chat()), WAIT)
        assert exc.value.code == "llm_daily_quota_reached"
        assert exc.value.extra == {"tier": "fast", "limit": 2}
        gated.release.set()
        await asyncio.wait_for(first, WAIT)
    assert len(gated.requests) == 1 and len(fast.requests) == 1
    calls = await _calls(db)
    assert [c.outcome for c in calls] == ["ok", "ok", "budget_exceeded"]
    assert all(c.settled_at is not None for c in calls[:2])
    assert calls[0].input_tokens > 0 and calls[0].model == "fake-1"


async def test_simultaneous_calls_cannot_exceed_the_cap(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    capped = _free(settings, cap=2)
    provider = SlowProvider()

    async def attempt() -> str:
        async with _ctx(database, owner, capped, provider) as ctx:
            try:
                await gateway.complete(ctx, space_id=space, request=_chat())
            except ProblemError as exc:
                return exc.code
            return "ok"

    results = await asyncio.wait_for(asyncio.gather(*(attempt() for _ in range(4))), WAIT)
    assert sorted(results) == ["llm_daily_quota_reached"] * 2 + ["ok"] * 2
    assert len(provider.requests) == 2
    outcomes = sorted(c.outcome for c in await _calls(db))
    assert outcomes == ["budget_exceeded"] * 2 + ["ok"] * 2


async def test_simultaneous_paid_calls_reserve_their_cost(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    paid = settings.model_copy(
        update={
            "llm_billing": "paid",
            "llm_price_input_fast": 100.0,
            "llm_price_output_fast": 100.0,
            "llm_reserve_output_tokens_fast": 1000,
            "llm_daily_budget_usd": 0.2,  # 0.19 usable: room for one reservation (0.1019)
        }
    )
    gated = GatedProvider()
    async with (
        _ctx(database, owner, paid, gated) as ctx_a,
        _ctx(database, owner, paid, FakeProvider()) as ctx_b,
    ):
        first = asyncio.create_task(gateway.complete(ctx_a, space_id=space, request=_chat()))
        await asyncio.wait_for(gated.entered.wait(), WAIT)
        assert Decimal((await gateway.usage_summary(ctx_b))["cost_today_usd"]) == Decimal(
            "0.101900"
        )
        with pytest.raises(ProblemError) as exc:
            await gateway.complete(ctx_b, space_id=space, request=_chat())
        assert exc.value.code == "llm_budget_exceeded"
        gated.release.set()
        await asyncio.wait_for(first, WAIT)
    calls = await _calls(db)
    assert [c.outcome for c in calls] == ["ok", "budget_exceeded"]
    # Settled: the real cost replaces the reserved estimate in the counters.
    assert calls[0].cost_usd < calls[0].reserved_cost_usd


async def test_the_margin_is_kept_below_each_daily_cap(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    assert gateway.daily_request_limit(_free(settings, cap=1, margin=2), "fast") == 0
    space = await _enable_cloud(database, owner)
    capped = settings.model_copy(
        update={
            "llm_daily_requests_fast": 4,
            "llm_daily_requests_deep": 3,
            "llm_quota_margin_requests": 2,
        }
    )
    fake = FakeProvider()
    async with _ctx(database, owner, capped, fake) as ctx:
        for _ in range(2):
            await gateway.complete(ctx, space_id=space, request=_chat())
        with pytest.raises(ProblemError) as exc:
            await gateway.complete(ctx, space_id=space, request=_chat())
        assert exc.value.extra == {"tier": "fast", "limit": 2}
        await gateway.complete(ctx, space_id=space, request=_chat("deep"))
        with pytest.raises(ProblemError):
            await gateway.complete(ctx, space_id=space, request=_chat("deep"))
        usage = await gateway.usage_summary(ctx)
    assert usage["daily_request_caps"] == {"fast": 2, "deep": 1}
    assert len(fake.requests) == 3


class _Answer(BaseModel):
    text: str = "rien"


async def test_the_token_budget_counts_the_call_about_to_be_made(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    fake = FakeProvider()
    # "Bonjour": 3 estimated tokens + 16 of framing; no max_output_tokens: 2048 reserved.
    tokens, cost = gateway.reservation_size(settings, _chat())
    assert (tokens, cost) == (19 + 2048, Decimal(0))
    assert gateway.reservation_size(settings, _chat(max_output_tokens=100))[0] == 119
    async with _ctx(database, owner, settings, fake) as ctx:
        ctx.user.llm_monthly_token_budget = tokens - 1
        with pytest.raises(ProblemError) as exc:
            await gateway.complete(ctx, space_id=space, request=_chat())
        assert exc.value.code == "llm_budget_exceeded"
        assert fake.requests == []  # refused before anything left the server

        ctx.user.llm_monthly_token_budget = 1_000_000
        await gateway.complete_structured(ctx, space_id=space, request=_chat(), schema=_Answer)
    calls = await _calls(db)
    assert [c.outcome for c in calls] == ["budget_exceeded", "ok"]
    # The response schema is sent with the request: it is part of the reserved input.
    schema_chars = len(json.dumps(_Answer.model_json_schema()))
    assert calls[1].reserved_tokens == gateway.estimate_input_tokens(_chat(), schema_chars) + 2048


async def test_a_cancelled_call_settles_its_reservation(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    gated = GatedProvider()
    async with _ctx(database, owner, settings, gated) as ctx:
        task = asyncio.create_task(gateway.complete(ctx, space_id=space, request=_chat()))
        await asyncio.wait_for(gated.entered.wait(), WAIT)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        usage = await gateway.usage_summary(ctx)
    calls = await _calls(db)
    assert [(c.outcome, c.error_code) for c in calls] == [("error", "llm_error")]
    assert calls[0].settled_at is not None
    # It may have reached the provider: it counts, but no longer holds tokens.
    assert usage["requests_today"]["fast"] == 1 and usage["in_flight"]["fast"] == 0
    assert usage["month_tokens_used"] == 0


async def _stale_reservation(
    database: Database,
    owner: Owner,
    space: uuid.UUID,
    *,
    tokens: int,
    age: timedelta,
    now: datetime | None = None,
) -> uuid.UUID:
    async with database.sessionmaker() as session, session.begin():
        call = LLMCall(
            user_id=owner.user.id,
            space_id=space,
            purpose="test",
            provider="fake",
            tier="fast",
            outcome="reserved",
            reserved_tokens=tokens,
            reserved_cost_usd=Decimal("0.5"),
            created_at=(now or utcnow()) - age,
        )
        session.add(call)
        await session.flush()
        return call.id


async def test_an_abandoned_reservation_is_released(
    database: Database,
    db: AsyncSession,
    owner: Owner,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Noon of the current quota day: "an hour ago" stays in the same day whatever the hour
    # the test runs (right after midnight Pacific time, it would fall on the day before).
    noon = gateway.quota_day_start(utcnow()) + timedelta(hours=12)
    monkeypatch.setattr(gateway, "utcnow", lambda: noon)
    space = await _enable_cloud(database, owner)
    # A process died during a call an hour ago: its reservation fills the token budget.
    stale = await _stale_reservation(
        database, owner, space, tokens=999_000, age=timedelta(hours=1), now=noon
    )
    fake = FakeProvider()
    async with _ctx(database, owner, settings, fake) as ctx:
        await gateway.complete(ctx, space_id=space, request=_chat())
        usage = await gateway.usage_summary(ctx)
    abandoned = await db.get(LLMCall, stale)
    assert abandoned is not None and abandoned.outcome == "abandoned"
    assert abandoned.settled_at is not None and abandoned.reserved_tokens == 999_000
    # Released: its tokens no longer count; its request still does (it may have reached
    # the provider).
    assert usage["month_tokens_used"] < 100
    assert usage["requests_today"]["fast"] == 2 and usage["in_flight"]["fast"] == 0


async def test_a_recent_reservation_is_not_released(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    await _stale_reservation(database, owner, space, tokens=999_000, age=timedelta(minutes=1))
    fake = FakeProvider()
    async with _ctx(database, owner, settings, fake) as ctx:
        with pytest.raises(ProblemError) as exc:
            await gateway.complete(ctx, space_id=space, request=_chat())
    assert exc.value.code == "llm_budget_exceeded" and fake.requests == []


async def test_the_sweep_releases_only_old_reservations(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    old = await _stale_reservation(database, owner, space, tokens=10, age=timedelta(hours=1))
    recent = await _stale_reservation(database, owner, space, tokens=10, age=timedelta(seconds=5))
    async with database.sessionmaker() as session, session.begin():
        released = await gateway.release_stale_reservations(
            session, ttl=settings.llm_reservation_ttl
        )
    assert released == 1
    outcomes = {c.id: c.outcome for c in await _calls(db)}
    assert outcomes == {old: "abandoned", recent: "reserved"}


async def test_a_late_call_still_records_its_real_usage(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    gated = GatedProvider()
    async with _ctx(database, owner, settings, gated) as ctx:
        task = asyncio.create_task(gateway.complete(ctx, space_id=space, request=_chat()))
        await asyncio.wait_for(gated.entered.wait(), WAIT)
        # Released while still running (as if the sweep had judged it dead)...
        async with database.sessionmaker() as session, session.begin():
            assert await gateway.release_stale_reservations(session, ttl=timedelta(0)) == 1
        gated.release.set()
        await asyncio.wait_for(task, WAIT)
    # ...the real usage wins.
    calls = await _calls(db)
    assert [c.outcome for c in calls] == ["ok"]
    assert calls[0].input_tokens > 0


def test_reservations_outlive_a_live_call() -> None:
    assert Settings().llm_reservation_ttl == timedelta(minutes=10)
    short = Settings(llm_reservation_ttl_seconds=30, llm_timeout_seconds=60)
    # Two provider requests (thinking-level retry) plus a minute.
    assert short.llm_reservation_ttl == timedelta(seconds=180)


def test_the_release_job_is_scheduled() -> None:
    from lifeos.workers.scheduler import build_scheduler

    jobs = {job.id for job in build_scheduler().get_jobs()}
    assert "release_llm_reservations" in jobs


async def test_refusals_hold_no_reservation(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    async with database.sessionmaker() as session, session.begin():
        await session.execute(update(User).values(llm_paused=True))
    async with _ctx(database, owner, settings, FakeProvider()) as ctx:
        with pytest.raises(ProblemError) as exc:
            await gateway.complete(ctx, space_id=space, request=_chat())
    assert exc.value.code == "llm_paused"
    calls = await _calls(db)
    assert [(c.outcome, c.reserved_tokens, c.settled_at) for c in calls] == [("denied", 0, None)]


async def test_the_quota_lock_cannot_collide_with_single_key_locks(
    database: Database, db: AsyncSession, owner: Owner, settings: Settings
) -> None:
    space = await _enable_cloud(database, owner)
    # A single-key lock made of the same two halves (reviews hold such locks during their
    # LLM call): the two key spaces are distinct, so the reservation is not blocked.
    async with database.sessionmaker() as other, other.begin():
        await other.execute(
            text(
                "SELECT pg_advisory_xact_lock((CAST(:cls AS bigint) << 32)"
                " | (CAST(hashtext(:user) AS bigint) & 4294967295))"
            ),
            {"cls": gateway.QUOTA_LOCK_CLASS, "user": str(owner.user.id)},
        )
        async with _ctx(database, owner, settings, FakeProvider()) as ctx:
            await asyncio.wait_for(gateway.complete(ctx, space_id=space, request=_chat()), WAIT)
