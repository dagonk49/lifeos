"""Adaptive notifications (F09, tasks 20 and 21): delivery policy, digests, maximum per day,
targeted disabling, re-timing, critical alerts and their reinforced permission."""

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.guardian.policy import evaluate
from lifeos.modules.guardian.principal import Principal
from lifeos.modules.identity.models import User, UserSession
from lifeos.modules.notifications.models import Notification
from lifeos.modules.notifications.policy import (
    CategoryPolicy,
    DeliveryPolicy,
    Window,
    next_delivery,
)
from lifeos.modules.notifications.service import (
    build_digests,
    dispatch_reminders,
    notify,
    retime_pending,
)
from lifeos.modules.privacy.erasure import forget_notifications

from .conftest import Owner

PARIS = "Europe/Paris"
NIGHT = (time(22, 0), time(7, 0))


def _policy(**kwargs: Any) -> DeliveryPolicy:
    return DeliveryPolicy(timezone=PARIS, quiet_start=NIGHT[0], quiet_end=NIGHT[1], **kwargs)


def _paris(*args: int) -> datetime:
    from zoneinfo import ZoneInfo

    return datetime(*args, tzinfo=ZoneInfo(PARIS)).astimezone(UTC)  # type: ignore[misc]


# --- Policy (pure) --------------------------------------------------------------------------


def test_important_is_immediate_outside_quiet_hours_and_inside_windows() -> None:
    policy = _policy()
    noon = _paris(2026, 10, 12, 12, 0)
    assert next_delivery(noon, policy, "important") == noon
    late = _paris(2026, 10, 12, 23, 30)
    assert next_delivery(late, policy, "important") == _paris(2026, 10, 13, 7, 0)
    early = _paris(2026, 10, 13, 6, 0)
    assert next_delivery(early, policy, "important") == _paris(2026, 10, 13, 7, 0)

    # Delivery windows (« créneau opportun »): the next window outside quiet hours.
    windows = (Window(time(9, 0), time(12, 0)), Window(time(14, 0), time(18, 0)))
    slotted = _policy(categories={"important": CategoryPolicy(windows=windows)})
    assert next_delivery(_paris(2026, 10, 12, 8, 0), slotted, "important") == _paris(
        2026, 10, 12, 9, 0
    )
    inside = _paris(2026, 10, 12, 10, 15)
    assert next_delivery(inside, slotted, "important") == inside
    assert next_delivery(_paris(2026, 10, 12, 12, 30), slotted, "important") == _paris(
        2026, 10, 12, 14, 0
    )
    assert next_delivery(_paris(2026, 10, 12, 19, 0), slotted, "important") == _paris(
        2026, 10, 13, 9, 0
    )
    # A window that overlaps quiet hours only keeps its quiet-free part.
    overlap = _policy(categories={"important": CategoryPolicy(windows=(Window(time(6), time(8)),))})
    assert next_delivery(_paris(2026, 10, 12, 5, 0), overlap, "important") == _paris(
        2026, 10, 12, 7, 0
    )
    # Windows entirely inside quiet hours are ignored rather than losing the notification.
    hidden = _policy(
        categories={"important": CategoryPolicy(windows=(Window(time(23), time(23, 30)),))}
    )
    assert next_delivery(noon, hidden, "important") == noon


def test_suggestions_wait_for_the_next_digest_time_outside_quiet_hours() -> None:
    policy = _policy()  # default: one digest at 18:00
    assert next_delivery(_paris(2026, 10, 12, 9, 0), policy, "suggestion") == _paris(
        2026, 10, 12, 18, 0
    )
    assert next_delivery(_paris(2026, 10, 12, 18, 0, 1), policy, "suggestion") == _paris(
        2026, 10, 13, 18, 0
    )
    two = _policy(categories={"suggestion": CategoryPolicy(digest_times=(time(8, 0), time(23, 0)))})
    # 23:00 falls in quiet hours: skipped.
    assert next_delivery(_paris(2026, 10, 12, 9, 0), two, "suggestion") == _paris(
        2026, 10, 13, 8, 0
    )


def test_maximum_per_day_moves_the_surplus_to_the_next_day_with_room() -> None:
    policy = _policy(categories={"important": CategoryPolicy(max_per_day=2)})
    noon = _paris(2026, 10, 12, 12, 0)
    today, tomorrow = date(2026, 10, 12), date(2026, 10, 13)
    assert next_delivery(noon, policy, "important", {today: 1}) == noon
    assert next_delivery(noon, policy, "important", {today: 2}) == _paris(2026, 10, 13, 7, 0)
    assert next_delivery(noon, policy, "important", {today: 2, tomorrow: 2}) == _paris(
        2026, 10, 14, 7, 0
    )
    # Default for suggestions: 3 a day, then the next digest.
    suggestions = _policy()
    assert next_delivery(noon, suggestions, "suggestion", {today: 3}) == _paris(2026, 10, 13, 18, 0)


def test_critical_alerts_need_the_permission_to_bypass_quiet_hours() -> None:
    late = _paris(2026, 10, 12, 23, 30)
    assert next_delivery(late, _policy(), "critical") == _paris(2026, 10, 13, 7, 0)
    assert next_delivery(late, _policy(critical_bypass=True), "critical") == late
    noon = _paris(2026, 10, 12, 12, 0)
    assert next_delivery(noon, _policy(), "critical") == noon
    # Never capped, never restricted to windows, never disabled.
    strict = _policy(
        categories={
            "important": CategoryPolicy(
                enabled=False, max_per_day=1, windows=(Window(time(9), time(10)),)
            )
        }
    )
    assert next_delivery(noon, strict, "critical", {date(2026, 10, 12): 50}) == noon
    assert strict.enabled("critical")
    assert not strict.enabled("important")


def test_daylight_saving_changes() -> None:
    # 2026-10-25: 03:00 CEST becomes 02:00 CET. A 02:30 digest happens once, at its first
    # occurrence, and the quiet hours end at 07:00 CET.
    policy = DeliveryPolicy(
        timezone=PARIS, categories={"suggestion": CategoryPolicy(digest_times=(time(2, 30),))}
    )
    before = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)  # 01:00 CEST
    assert next_delivery(before, policy, "suggestion") == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    # Between the two 02:30: the second one, an hour later.
    between = datetime(2026, 10, 25, 0, 45, tzinfo=UTC)  # 02:45 CEST
    assert next_delivery(between, policy, "suggestion") == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    night = _policy()
    assert next_delivery(datetime(2026, 10, 24, 23, 0, tzinfo=UTC), night, "important") == (
        datetime(2026, 10, 25, 6, 0, tzinfo=UTC)
    )
    # 2027-03-28: 02:00 CET becomes 03:00 CEST. A 02:30 digest (skipped) moves to 03:30.
    spring = datetime(2027, 3, 28, 0, 30, tzinfo=UTC)  # 01:30 CET
    assert next_delivery(spring, policy, "suggestion") == datetime(2027, 3, 28, 1, 30, tzinfo=UTC)
    assert next_delivery(spring, night, "important") == datetime(2027, 3, 28, 5, 0, tzinfo=UTC)


# --- Integration ----------------------------------------------------------------------------


async def _user(db: AsyncSession, owner: Owner) -> User:
    user = await db.get(User, owner.user.id, populate_existing=True)
    assert user is not None
    return user


async def _suggest(db: AsyncSession, user: User, n: int, space_id: str | None = None) -> None:
    for i in range(n):
        assert await notify(
            db,
            user,
            category="suggestion",
            title=f"Idée {i}",
            dedup_key=f"test:suggestion:{i}",
            space_id=uuid.UUID(space_id) if space_id else None,
            source_type="review",
            source_id=uuid.uuid4(),
        )
    await db.commit()


async def test_suggestions_arrive_grouped_in_one_digest(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, db: AsyncSession
) -> None:
    user = await _user(db, logged_in)
    await _suggest(db, user, 3, spaces["personal"])
    items = list(await db.scalars(select(Notification)))
    assert len({n.deliver_after for n in items}) == 1  # the same digest time
    assert all(n.deliver_after > utcnow() for n in items)
    assert (await build_digests(db))["digests"] == 0  # not due yet
    assert (await client.get("/v1/notifications")).json() == []

    # Digest time reached: due suggestions are still not listed one by one.
    await db.execute(update(Notification).values(deliver_after=utcnow() - timedelta(minutes=1)))
    await db.commit()
    assert (await client.get("/v1/notifications")).json() == []
    assert await build_digests(db) == {"digests": 1, "items": 3}
    await db.commit()
    assert (await build_digests(db))["digests"] == 0  # idempotent
    await db.commit()

    listed = (await client.get("/v1/notifications")).json()
    assert len(listed) == 1
    digest = listed[0]
    assert digest["source_type"] == "digest" and digest["items_count"] == 3
    assert digest["space_id"] == spaces["personal"]
    assert "Idée" not in digest["title"] + (digest["body"] or "")  # no item title
    grouped = (await client.get(f"/v1/notifications/{digest['id']}/items")).json()
    assert [n["title"] for n in grouped] == ["Idée 0", "Idée 1", "Idée 2"]
    assert (await client.get("/v1/dashboard")).json()["unread_notifications"] == 1

    # Reading the digest reads its suggestions.
    read = await client.patch(f"/v1/notifications/{digest['id']}", json={"status": "read"})
    assert read.status_code == 200 and read.json()["items_count"] == 3
    statuses = await db.scalars(select(Notification.status).execution_options())
    assert set(statuses) == {"read"}
    # A plain notification has no items.
    not_digest = await client.get(f"/v1/notifications/{grouped[0]['id']}/items")
    assert not_digest.status_code == 404

    audit = await db.scalar(select(AuditEvent).where(AuditEvent.action == "notification.digest"))
    assert audit is not None and audit.details == {"users": 1, "digests": 1, "items": 3}


async def test_erasing_the_last_item_of_a_digest_removes_the_digest(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, db: AsyncSession
) -> None:
    user = await _user(db, logged_in)
    await _suggest(db, user, 1)
    await db.execute(update(Notification).values(deliver_after=utcnow() - timedelta(minutes=1)))
    await build_digests(db)
    await db.commit()
    item = await db.scalar(select(Notification).where(Notification.digest_id.is_not(None)))
    assert item is not None and item.source_id is not None
    assert await forget_notifications(db, user.id, "review", [item.source_id]) == 2
    assert await db.scalar(select(func.count()).select_from(Notification)) == 0


async def test_read_all_leaves_suggestions_waiting_for_their_digest(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, db: AsyncSession
) -> None:
    user = await _user(db, logged_in)
    await _suggest(db, user, 2)
    await db.execute(update(Notification).values(deliver_after=utcnow() - timedelta(minutes=1)))
    await db.commit()
    assert (await client.post("/v1/notifications/read-all")).status_code == 204
    assert await build_digests(db) == {"digests": 1, "items": 2}


async def test_maximum_per_day_is_respected_by_concurrent_producers(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, database: Database
) -> None:
    await client.patch("/v1/settings", json={"timezone": "UTC"})
    put = await client.put(
        "/v1/notifications/preferences",
        json={"categories": [{"category": "important", "max_per_day": 1}]},
    )
    assert put.status_code == 200, put.text

    async def produce(key: str) -> None:
        async with database.sessionmaker() as session, session.begin():
            user = await session.get(User, logged_in.user.id)
            assert user is not None
            assert await notify(session, user, category="important", title=key, dedup_key=key)

    await asyncio.gather(produce("a"), produce("b"), produce("c"))
    async with database.sessionmaker() as session:
        days = sorted((await session.scalars(select(Notification.deliver_after))).all())
    # UTC by default: one today (now), then the next local midnights.
    today = utcnow().date()
    assert [d.date() for d in days] == [today + timedelta(days=i) for i in range(3)]
    assert days[1].time() == time(0, 0) and days[2].time() == time(0, 0)
    # Anti-duplicates still hold.
    async with database.sessionmaker() as session, session.begin():
        user = await session.get(User, logged_in.user.id)
        assert user is not None
        assert not await notify(session, user, category="important", title="a", dedup_key="a")


async def test_disabled_category_creates_nothing_and_does_not_flood_later(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, db: AsyncSession
) -> None:
    put = await client.put(
        "/v1/notifications/preferences",
        json={"categories": [{"category": "important", "enabled": False}]},
    )
    assert put.status_code == 200, put.text
    await client.post(
        "/v1/tasks",
        json={
            "space_id": spaces["personal"],
            "title": "Arroser les plantes",
            "remind_at": (utcnow() - timedelta(minutes=1)).isoformat(),
        },
    )
    assert await dispatch_reminders(db) == 0
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(Notification)) == 0
    audit = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "notification.dispatch_reminders")
    )
    assert audit is not None and audit.details["suppressed"] == 1
    # Enabled again: the old reminder is not replayed (the task was marked reminded).
    await client.put(
        "/v1/notifications/preferences",
        json={"categories": [{"category": "important", "enabled": True}]},
    )
    assert await dispatch_reminders(db) == 0


async def test_changing_preferences_retimes_pending_notifications(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, db: AsyncSession
) -> None:
    now = utcnow()
    start = (now - timedelta(hours=1)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    await client.patch(
        "/v1/settings",
        json={
            "timezone": "UTC",
            "quiet_hours_start": start.strftime("%H:%M"),
            "quiet_hours_end": end.strftime("%H:%M"),
        },
    )
    user = await _user(db, logged_in)
    assert await notify(db, user, category="important", title="Rappel", dedup_key="k")
    await _suggest(db, user, 1)
    held = await db.scalar(select(Notification).where(Notification.dedup_key == "k"))
    assert held is not None and held.deliver_after > now + timedelta(hours=1)

    # Quiet hours removed: delivered now.
    response = await client.patch(
        "/v1/settings", json={"quiet_hours_start": None, "quiet_hours_end": None}
    )
    assert response.status_code == 200
    await db.refresh(held)
    assert held.deliver_after <= utcnow()
    assert (await client.get("/v1/notifications")).json()[0]["title"] == "Rappel"
    # Same key after the re-timing: still one notification.
    user = await _user(db, logged_in)
    assert not await notify(db, user, category="important", title="Rappel", dedup_key="k")
    await db.commit()  # releases the per-user lock

    # Suggestions disabled: the pending one is dismissed, never delivered.
    put = await client.put(
        "/v1/notifications/preferences",
        json={"categories": [{"category": "suggestion", "enabled": False}]},
    )
    assert put.status_code == 200
    suggestion = await db.scalar(
        select(Notification)
        .where(Notification.category == "suggestion")
        .execution_options(populate_existing=True)
    )
    assert suggestion is not None and suggestion.status == "dismissed"
    audit = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "notification.preferences.update")
    )
    assert audit is not None
    assert audit.details == {"fields": "suggestion.enabled", "retimed": 0, "dismissed": 1}


async def test_preferences_round_trip_and_validation(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    defaults = (await client.get("/v1/notifications/preferences")).json()
    assert defaults == {
        "categories": [
            {
                "category": "important",
                "enabled": True,
                "max_per_day": None,
                "windows": [],
                "digest_times": [],
            },
            {
                "category": "suggestion",
                "enabled": True,
                "max_per_day": 3,
                "windows": [],
                "digest_times": ["18:00"],
            },
        ],
        "critical_bypass_quiet_hours": False,
    }
    invalid: list[dict[str, Any]] = [
        {"category": "important", "windows": [{"start": "10:00", "end": "09:00"}]},
        {
            "category": "important",
            "windows": [{"start": "09:00", "end": "12:00"}, {"start": "11:00", "end": "13:00"}],
        },
        {"category": "important", "windows": [{"start": "25:00", "end": "26:00"}]},
        {"category": "important", "digest_times": ["08:00"]},
        {"category": "important", "max_per_day": 0},
        {"category": "suggestion", "digest_times": []},
        {"category": "suggestion", "windows": [{"start": "09:00", "end": "10:00"}]},
    ]
    for pref in invalid:
        response = await client.put("/v1/notifications/preferences", json={"categories": [pref]})
        assert response.status_code == 422, pref
        assert response.json()["code"] == "notification_preferences_invalid", pref
    twice = await client.put(
        "/v1/notifications/preferences",
        json={"categories": [{"category": "important"}, {"category": "important"}]},
    )
    assert twice.json()["code"] == "notification_preferences_invalid"

    saved = await client.put(
        "/v1/notifications/preferences",
        json={
            "categories": [
                {
                    "category": "important",
                    "max_per_day": 10,
                    "windows": [
                        {"start": "14:00", "end": "18:00"},
                        {"start": "08:00", "end": "12:00"},
                    ],
                },
                {"category": "suggestion", "digest_times": ["19:30", "08:00"], "max_per_day": 5},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    body = (await client.get("/v1/notifications/preferences")).json()
    important, suggestion = body["categories"]
    assert important["windows"] == [
        {"start": "08:00", "end": "12:00"},
        {"start": "14:00", "end": "18:00"},
    ]
    assert important["max_per_day"] == 10
    assert suggestion["digest_times"] == ["08:00", "19:30"]
    assert suggestion["max_per_day"] == 5


# --- Critical alerts (task 21) -------------------------------------------------------------


def test_only_the_owner_with_a_recent_passkey_may_grant_the_bypass() -> None:
    user_id = uuid.uuid4()
    owner = Principal(kind="user", user_id=user_id, actor_id="me", space_ids=frozenset())
    assert evaluate(owner, "notification.critical_bypass", None).code == (
        "reauthentication_required"
    )
    recent = Principal(
        kind="user", user_id=user_id, actor_id="me", space_ids=frozenset(), recent_auth=True
    )
    assert evaluate(recent, "notification.critical_bypass", None).allowed
    agent = Principal(
        kind="agent", user_id=user_id, actor_id="core", agent="core", space_ids=frozenset()
    )
    assert evaluate(agent, "notification.critical_bypass", None).code == "forbidden_for_agents"


async def test_critical_bypass_permission_is_reinforced_audited_and_revocable(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, db: AsyncSession
) -> None:
    now = utcnow()
    start = (now - timedelta(hours=1)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    await client.patch(
        "/v1/settings",
        json={
            "timezone": "UTC",
            "quiet_hours_start": start.strftime("%H:%M"),
            "quiet_hours_end": end.strftime("%H:%M"),
        },
    )
    assert (await client.get("/v1/settings")).json()["critical_bypass_quiet_hours"] is False

    # Without the permission, a critical alert waits for the end of quiet hours.
    user = await _user(db, logged_in)
    assert await notify(db, user, category="critical", title="Alerte", dedup_key="c1")
    await db.commit()
    alert = await db.scalar(select(Notification).where(Notification.dedup_key == "c1"))
    assert alert is not None and alert.deliver_after > now + timedelta(hours=1)
    assert (await client.get("/v1/notifications")).json() == []

    # Granting it needs a recent passkey: refused (and audited) with a stale session.
    await db.execute(update(UserSession).values(authenticated_at=utcnow() - timedelta(hours=1)))
    await db.commit()
    refused = await client.patch("/v1/settings", json={"critical_bypass_quiet_hours": True})
    assert refused.status_code == 403
    assert refused.json()["code"] == "reauthentication_required"
    denied = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "notification.critical_bypass", AuditEvent.outcome == "denied"
        )
    )
    assert denied is not None
    assert (await client.get("/v1/settings")).json()["critical_bypass_quiet_hours"] is False

    await db.execute(update(UserSession).values(authenticated_at=utcnow()))
    await db.commit()
    granted = await client.patch("/v1/settings", json={"critical_bypass_quiet_hours": True})
    assert granted.status_code == 200 and granted.json()["critical_bypass_quiet_hours"] is True
    # The held alert is re-timed: delivered now. New ones go straight through.
    assert [n["title"] for n in (await client.get("/v1/notifications")).json()] == ["Alerte"]
    user = await _user(db, logged_in)
    assert await notify(db, user, category="critical", title="Alerte 2", dedup_key="c2")
    await db.commit()
    second = await db.scalar(select(Notification).where(Notification.dedup_key == "c2"))
    assert second is not None and second.deliver_after <= utcnow()
    # Important notifications still wait.
    assert await notify(db, user, category="important", title="Rappel", dedup_key="i1")
    await db.commit()
    important = await db.scalar(select(Notification).where(Notification.dedup_key == "i1"))
    assert important is not None and important.deliver_after > utcnow()

    # Withdrawing it is immediate, even with a stale session.
    await db.execute(update(UserSession).values(authenticated_at=utcnow() - timedelta(hours=1)))
    await db.commit()
    revoked = await client.patch("/v1/settings", json={"critical_bypass_quiet_hours": False})
    assert revoked.status_code == 200 and revoked.json()["critical_bypass_quiet_hours"] is False
    events = list(
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.action == "notification.critical_bypass",
                AuditEvent.outcome == "success",
            )
            .order_by(AuditEvent.id)
        )
    )
    assert [e.details for e in events] == [{"enabled": True}, {"enabled": False}]
    user = await _user(db, logged_in)
    assert await notify(db, user, category="critical", title="Alerte 3", dedup_key="c3")
    await db.commit()
    third = await db.scalar(select(Notification).where(Notification.dedup_key == "c3"))
    assert third is not None and third.deliver_after > utcnow()


async def test_retime_pending_leaves_digests_and_delivered_items_alone(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, db: AsyncSession
) -> None:
    user = await _user(db, logged_in)
    await _suggest(db, user, 2)
    await db.execute(update(Notification).values(deliver_after=utcnow() - timedelta(minutes=1)))
    await build_digests(db)
    await db.commit()
    assert await retime_pending(db, user) == {"pending": 0, "retimed": 0, "dismissed": 0}
