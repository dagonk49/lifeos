# ruff: noqa: F811  (fixtures of test_calendar, used as test parameters)
"""Home page (task 23): agenda and week in local days, stored suggestions with their
source, layout, space scoping. Everything is computed locally (never the LLM)."""

import uuid
from datetime import UTC, date, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import Engine, event, select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.connectors.fake import FakeConnector
from lifeos.modules.dashboard.models import DashboardPreference
from lifeos.modules.dashboard.service import CARD_IDS, merge_cards
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.notifications.models import Notification
from lifeos.modules.reviews.models import Review

from .conftest import Owner
from .test_calendar import (  # noqa: F401  (fixtures)
    PARIS,
    app,
    connect,
    connector_settings,
    fake_connector,
    map_calendar,
    run_sync,
    timed,
)
from .test_planning import follow_both_spaces


def day_items(week: dict[str, object], day: str) -> list[dict[str, object]]:
    days = week["days"]
    assert isinstance(days, list)
    found = [d for d in days if d["date"] == day]
    assert found, day
    items: list[dict[str, object]] = found[0]["items"]
    return items


async def test_the_week_puts_each_item_on_its_local_day_across_dst(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    fake_llm: FakeProvider,
) -> None:
    await client.patch("/v1/settings", json={"timezone": "Europe/Paris"})
    personal, work = spaces["personal"], spaces["professional"]
    # 2026: summer time ends on Sunday 25 October (03:00 CEST -> 02:00 CET).
    for title, due in (
        ("Avant le changement", "2026-10-25T00:30:00+02:00"),  # 24 Oct 22:30 UTC
        ("Dimanche soir", "2026-10-25T23:30:00+01:00"),  # 25 Oct 22:30 UTC
        ("Lundi tôt", "2026-10-26T00:30:00+01:00"),  # 25 Oct 23:30 UTC
    ):
        created = await client.post(
            "/v1/tasks", json={"space_id": personal, "title": title, "due_at": due}
        )
        assert created.status_code == 201, created.text
    goal = await client.post("/v1/goals", json={"space_id": personal, "title": "Courir 10 km"})
    assert goal.status_code == 201, goal.text
    milestone = await client.post(
        f"/v1/goals/{goal.json()['id']}/milestones",
        json={"title": "Premier 5 km", "due_date": "2026-10-22"},
    )
    assert milestone.status_code == 201, milestone.text
    rent = await client.post(
        "/v1/finance/recurring",
        json={
            "space_id": personal,
            "label": "Loyer",
            "amount": "800.00",
            "cadence": "monthly",
            "start_date": "2026-09-23",
        },
    )
    assert rent.status_code == 201, rent.text

    sources = await follow_both_spaces(client, spaces, fake_connector)
    fake_connector.add_event(
        "primary", timed("dentist", "Dentiste", datetime(2026, 10, 21, 9, tzinfo=PARIS))
    )
    fake_connector.add_event(
        "family",
        timed("secret", "Entretien confidentiel", datetime(2026, 10, 21, 14, tzinfo=PARIS)),
    )
    for source in sources.values():
        assert (
            await run_sync(database, connector_settings, fake_connector, source)
        ).outcome == "synced"

    response = await client.get(
        "/v1/dashboard/week", params={"start": "2026-10-20", "space_id": personal}
    )
    assert response.status_code == 200, response.text
    week = response.json()
    assert (week["start"], week["end"], week["timezone"]) == (
        "2026-10-20",
        "2026-10-26",
        "Europe/Paris",
    )
    assert [d["date"] for d in week["days"]][0::6] == ["2026-10-20", "2026-10-26"]
    assert [i["title"] for i in day_items(week, "2026-10-25")] == [
        "Avant le changement",
        "Dimanche soir",
    ]
    assert [i["title"] for i in day_items(week, "2026-10-26")] == ["Lundi tôt"]
    sunday = day_items(week, "2026-10-25")[1]
    assert sunday["start"] == "2026-10-25T23:30:00+01:00"
    assert (sunday["kind"], sunday["source_type"], sunday["href"]) == ("task", "task", "/tasks")

    [step] = day_items(week, "2026-10-22")
    assert (step["kind"], step["title"], step["all_day"]) == ("milestone", "Premier 5 km", True)
    assert (step["source_label"], step["href"]) == (
        "Courir 10 km",
        f"/goals?id={goal.json()['id']}",
    )
    [payment] = day_items(week, "2026-10-23")
    assert (payment["kind"], payment["title"]) == ("payment", "Loyer")
    assert payment["detail"] == "-800,00 € · prévu"

    # The other space's occurrence only blocks time: neither title nor source.
    tuesday = day_items(week, "2026-10-21")
    assert [(i["kind"], i["title"]) for i in tuesday] == [
        ("event", "Dentiste"),
        ("busy", "Occupé (autre espace)"),
    ]
    assert tuesday[0]["source_label"] and tuesday[0]["synced_at"]
    assert tuesday[1]["source_id"] is None and tuesday[1]["space_id"] is None
    assert "confidentiel" not in response.text and "Entretien" not in response.text
    assert week["calendars"] == 2 and week["synced_at"]

    # Seen from the professional space: the work title, the personal time anonymous.
    other = (
        await client.get("/v1/dashboard/week", params={"start": "2026-10-20", "space_id": work})
    ).json()
    assert [(i["kind"], i["title"]) for i in day_items(other, "2026-10-21")] == [
        ("busy", "Occupé (autre espace)"),
        ("event", "Entretien confidentiel"),
    ]
    assert day_items(other, "2026-10-25") == []
    # All spaces: every title.
    both = (await client.get("/v1/dashboard/week", params={"start": "2026-10-20"})).json()
    assert {i["kind"] for i in day_items(both, "2026-10-21")} == {"event"}

    invalid = await client.get("/v1/dashboard/week", params={"start": "1800-01-01"})
    assert invalid.status_code == 422 and invalid.json()["code"] == "invalid_window"
    assert fake_llm.requests == []


async def test_the_dashboard_has_today_s_agenda_and_the_week(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    await client.patch("/v1/settings", json={"timezone": "Europe/Paris"})
    personal = spaces["personal"]
    soon = utcnow() + timedelta(minutes=5)
    blocker = await client.post("/v1/tasks", json={"space_id": personal, "title": "Devis"})
    task = await client.post(
        "/v1/tasks",
        json={"space_id": personal, "title": "Signer", "due_at": soon.isoformat()},
    )
    linked = await client.post(
        f"/v1/tasks/{task.json()['id']}/dependencies",
        json={"depends_on_id": blocker.json()["id"]},
    )
    assert linked.status_code == 201, linked.text

    data = (await client.get(f"/v1/dashboard?space_id={personal}")).json()
    today = data["today"]
    assert data["layout"]["default_view"] == "day"
    assert data["week"]["days"][0]["date"] == data["week"]["start"]
    assert len(data["week"]["days"]) == 7
    monday = date.fromisoformat(data["week"]["start"])
    assert monday.weekday() == 0 and monday <= date.fromisoformat(today)
    if soon.astimezone(PARIS).date().isoformat() == today:
        [item] = [i for i in data["agenda_today"] if i["kind"] == "task"]
        assert item["title"] == "Signer" and item["blocked"] is True
        assert item["detail"] == "Attend « Devis »"
    # No calendar followed: the figures say they are computed here, from local data.
    agenda = data["sources"]["agenda"]
    assert (agenda["origin"], agenda["count"]) == ("local", 0)
    assert datetime.fromisoformat(agenda["as_of"]) == datetime.fromisoformat(data["generated_at"])
    assert set(data["sources"]) == set(CARD_IDS)
    assert fake_llm.requests == []


async def test_layout_round_trip_validation_and_merge(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, db: AsyncSession
) -> None:
    default = (await client.get("/v1/dashboard/layout")).json()
    assert [c["id"] for c in default["cards"]] == list(CARD_IDS)
    assert all(c["visible"] for c in default["cards"]) and default["updated_at"] is None

    saved = await client.put(
        "/v1/dashboard/layout",
        json={
            "cards": [{"id": "budget", "visible": False}, {"id": "agenda", "visible": True}],
            "default_view": "week",
            "week_start": "today",
        },
    )
    assert saved.status_code == 200, saved.text
    cards = saved.json()["cards"]
    # Given first, then every other card with its defaults.
    assert [c["id"] for c in cards[:2]] == ["budget", "agenda"]
    assert len(cards) == len(CARD_IDS) and cards[0]["visible"] is False
    again = (await client.get("/v1/dashboard/layout")).json()
    assert again["cards"] == cards
    assert (again["default_view"], again["week_start"]) == ("week", "today")
    row = await db.get(DashboardPreference, logged_in.user.id)
    assert row is not None and row.default_view == "week"

    dashboard = (await client.get("/v1/dashboard")).json()
    assert dashboard["layout"]["cards"] == cards
    assert dashboard["week"]["start"] == dashboard["today"]  # week from today

    for cards_in, reason in (
        ([{"id": "horoscope", "visible": True}], "unknown"),
        ([{"id": "memory"}, {"id": "memory"}], "duplicate"),
    ):
        refused = await client.put("/v1/dashboard/layout", json={"cards": cards_in})
        assert refused.status_code == 422
        body = refused.json()
        assert body["code"] == "dashboard_layout_invalid", body
        assert body["reason"] == reason and body["field"].startswith("cards[")
    assert (await client.get("/v1/dashboard/layout")).json()["cards"] == cards

    events = list(
        await db.scalars(select(AuditEvent).where(AuditEvent.action == "dashboard.layout.update"))
    )
    assert len(events) == 1
    assert events[0].details == {
        "cards": len(CARD_IDS),
        "hidden": 1,
        "default_view": "week",
        "week_start": "today",
    }


def test_new_cards_are_merged_with_their_defaults() -> None:
    merged = merge_cards([{"id": "assistant", "visible": False}, {"id": "gone"}, "junk"])  # type: ignore[list-item]
    assert merged[0].model_dump() == {"id": "assistant", "visible": False}
    assert [c.id for c in merged[1:]] == [c for c in CARD_IDS if c != "assistant"]
    assert all(c.visible for c in merged[1:])


async def test_suggestions_come_from_the_latest_review_with_their_source(
    client: AsyncClient,
    spaces: dict[str, str],
    logged_in: Owner,
    db: AsyncSession,
    fake_llm: FakeProvider,
) -> None:
    personal, work = spaces["personal"], spaces["professional"]
    now = utcnow()

    def review(space: str, start: date, age: timedelta, action: str) -> Review:
        return Review(
            user_id=logged_in.user.id,
            space_id=uuid.UUID(space),
            period="weekly",
            period_start=start,
            period_end=start + timedelta(days=6),
            facts={},
            narrative={
                "summary": "…",
                "suggestions": [{"action": action, "why": f"Parce que {action}"}],
            },
            narrative_status="generated",
            generated_by="user",
            generated_at=now - age,
        )

    latest = review(personal, date(2026, 10, 19), timedelta(days=1), "Bloquer deux matinées")
    db.add_all(
        [
            latest,
            review(personal, date(2026, 10, 12), timedelta(days=8), "Ancienne idée"),
            review(work, date(2026, 10, 19), timedelta(days=1), "Idée du travail"),
        ]
    )
    await db.commit()

    # The cloud is down (not even enabled): the page only reads what is stored.
    data = (await client.get(f"/v1/dashboard?space_id={personal}")).json()
    [item] = data["suggestions"]
    assert item == {
        "text": "Bloquer deux matinées",
        "why": "Parce que Bloquer deux matinées",
        "source_type": "review",
        "source_id": str(latest.id),
        "source_label": "Bilan hebdomadaire du 19/10/2026",
        "date": item["date"],
        "href": f"/reviews?review={latest.id}",
        "space_id": personal,
    }
    assert datetime.fromisoformat(item["date"]) == latest.generated_at
    assert data["sources"]["suggestions"]["origin"] == "review"
    everything = (await client.get("/v1/dashboard")).json()
    assert {s["text"] for s in everything["suggestions"]} == {
        "Bloquer deux matinées",
        "Idée du travail",
    }
    assert fake_llm.requests == []


async def test_unread_notifications_follow_the_space_and_the_delivery_time(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, db: AsyncSession
) -> None:
    now = datetime.now(UTC)
    personal, work = uuid.UUID(spaces["personal"]), uuid.UUID(spaces["professional"])
    for key, space, deliver_after in (
        ("personal", personal, now - timedelta(minutes=1)),
        ("work", work, now - timedelta(minutes=1)),
        ("everywhere", None, now - timedelta(minutes=1)),
        ("later", personal, now + timedelta(hours=2)),
    ):
        db.add(
            Notification(
                user_id=logged_in.user.id,
                space_id=space,
                category="important",
                title=key,
                dedup_key=key,
                deliver_after=deliver_after,
                status="pending",
                created_at=now,
            )
        )
    await db.commit()
    count = "unread_notifications"
    assert (await client.get(f"/v1/dashboard?space_id={personal}")).json()[count] == 2
    assert (await client.get(f"/v1/dashboard?space_id={work}")).json()[count] == 2
    assert (await client.get("/v1/dashboard")).json()[count] == 3


async def test_the_number_of_queries_does_not_grow_with_the_data(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    personal = spaces["personal"]
    statements: list[str] = []

    def count(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    async def measure() -> int:
        statements.clear()
        event.listen(Engine, "before_cursor_execute", count)
        try:
            assert (await client.get(f"/v1/dashboard?space_id={personal}")).status_code == 200
        finally:
            event.remove(Engine, "before_cursor_execute", count)
        return len(statements)

    async def add_tasks(n: int) -> None:
        soon = utcnow() + timedelta(hours=1)
        for i in range(n):
            for title, due in ((f"T{i}", soon), (f"Retard {i}", soon - timedelta(days=3))):
                created = await client.post(
                    "/v1/tasks",
                    json={"space_id": personal, "title": title, "due_at": due.isoformat()},
                )
                assert created.status_code == 201

    await add_tasks(2)
    few = await measure()
    await add_tasks(15)
    assert await measure() == few
