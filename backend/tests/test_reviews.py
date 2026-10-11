import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.db.session import Database
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.guardian.models import AgentPermission
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.notifications.models import Notification
from lifeos.modules.reviews.models import Review
from lifeos.modules.reviews.service import period_bounds, reference_day, run_scheduled

from .conftest import enable_cloud

NARRATIVE = {
    "summary": "Semaine régulière, deux tâches menées à bien.",
    "progress": ["Dossier de crédit terminé"],
    "obstacles": ["Une tâche en retard"],
    "suggestions": [{"action": "Bloquer 30 min mardi", "why": "La tâche en retard"}],
    "interpretations": ["Il semble que les soirées soient plus productives"],
    "questions": ["Qu'est-ce qui a le mieux marché ?"],
}


def test_period_bounds() -> None:
    sunday = date(2026, 10, 11)
    assert period_bounds("daily", sunday) == (sunday, sunday)
    assert period_bounds("weekly", sunday) == (date(2026, 10, 5), date(2026, 10, 11))
    assert period_bounds("monthly", sunday) == (date(2026, 10, 1), date(2026, 10, 31))
    assert period_bounds("monthly", date(2026, 2, 14)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert period_bounds("weekly", date(2026, 12, 31)) == (date(2026, 12, 28), date(2027, 1, 3))


def test_scheduled_runs_close_the_right_period() -> None:
    paris = ZoneInfo("Europe/Paris")
    # On time: daily 21:30, weekly Sunday 19:00 -> the day / week that is ending.
    assert reference_day("daily", datetime(2026, 10, 11, 21, 30, tzinfo=paris)) == date(
        2026, 10, 11
    )
    assert reference_day("weekly", datetime(2026, 10, 11, 19, 0, tzinfo=paris)) == date(
        2026, 10, 11
    )
    # Caught up after midnight (scheduler restarted): still the previous day / week.
    assert reference_day("daily", datetime(2026, 10, 12, 0, 30, tzinfo=paris)) == date(2026, 10, 11)
    assert reference_day("weekly", datetime(2026, 10, 12, 1, 0, tzinfo=paris)) == date(2026, 10, 11)
    # Monthly runs on the 1st: they close the month that just ended.
    november_first = datetime(2026, 11, 1, 8, 0, tzinfo=paris)
    assert period_bounds("monthly", reference_day("monthly", november_first)) == (
        date(2026, 10, 1),
        date(2026, 10, 31),
    )


async def test_goal_achieved_during_the_period_is_part_of_its_review(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    space = spaces["personal"]
    goal = (
        await client.post("/v1/goals", json={"space_id": space, "title": "Finir le livre"})
    ).json()
    await client.patch(
        f"/v1/goals/{goal['id']}",
        json={"status": "achieved"},
        headers={"If-Match": str(goal["version"])},
    )
    review = (
        await client.post(
            "/v1/reviews", json={"space_id": space, "period": "weekly", "with_narrative": False}
        )
    ).json()
    assert [(g["title"], g["status"]) for g in review["facts"]["goals"]] == [
        ("Finir le livre", "achieved")
    ]


async def _seed_week(client: AsyncClient, space: str) -> None:
    now = datetime.now(UTC)
    done = (
        await client.post("/v1/tasks", json={"space_id": space, "title": "Envoyer le dossier"})
    ).json()
    await client.patch(f"/v1/tasks/{done['id']}", json={"status": "done"})
    await client.post(
        "/v1/tasks",
        json={
            "space_id": space,
            "title": "Rappeler la banque",
            "due_at": (now - timedelta(hours=2)).isoformat(),
        },
    )
    await client.post(
        "/v1/journal",
        json={
            "space_id": space,
            "entry_date": date.today().isoformat(),
            "content": "Bonne journée, j'ai écrit à paul@example.org.",
            "mood": 4,
        },
    )
    await client.post(
        "/v1/memory/facts",
        json={"space_id": space, "kind": "decision", "statement": "Je garde la voiture"},
    )
    goal = (
        await client.post(
            "/v1/goals",
            json={
                "space_id": space,
                "title": "Lire 12 livres",
                "kind": "measurable",
                "start_value": "0",
                "target_value": "12",
            },
        )
    ).json()
    await client.post(f"/v1/goals/{goal['id']}/checkins", json={"value": "3"})


async def test_review_separates_local_facts_from_the_assistant_reading(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    space = spaces["personal"]
    await _seed_week(client, space)

    # Without cloud consent: facts only, the reason is explicit.
    local = await client.post("/v1/reviews", json={"space_id": space, "period": "weekly"})
    assert local.status_code == 201, local.text
    review = local.json()
    assert review["narrative"] is None
    assert review["narrative_status"] == "unavailable"
    assert review["narrative_error"] == "cloud_consent_required"
    facts = review["facts"]
    assert facts["tasks"]["completed_titles"] == ["Envoyer le dossier"]
    assert facts["tasks"]["overdue_titles"] == ["Rappeler la banque"]
    assert facts["journal"] == {
        "entries": 1,
        "days_with_entry": 1,
        "mood_average": 4.0,
        "mood_entries": 1,
    }
    assert facts["memory"]["decisions"] == ["Je garde la voiture"]
    assert facts["goals"][0]["metric_progress"] == 0.25
    assert facts["goals"][0]["checkins_in_period"] == 1
    assert facts["omitted"] == []
    assert fake_llm.requests == []

    # With consent: the narrative is stored apart from the facts; regenerating replaces.
    await enable_cloud(client, space)
    fake_llm.push(json.dumps(NARRATIVE))
    regenerated = (
        await client.post("/v1/reviews", json={"space_id": space, "period": "weekly"})
    ).json()
    assert regenerated["id"] == review["id"]
    assert regenerated["narrative_status"] == "generated"
    assert regenerated["narrative"]["interpretations"] == NARRATIVE["interpretations"]
    assert regenerated["facts"]["tasks"] == facts["tasks"]
    sent = fake_llm.requests[-1]
    assert "FAITS (JSON)" in sent.messages[0].text
    assert "paul@example.org" not in sent.messages[0].text  # masked journal excerpt
    assert sent.purpose == "review.weekly"

    listed = (await client.get("/v1/reviews?period=weekly")).json()
    assert [r["id"] for r in listed] == [review["id"]]
    assert (await client.delete(f"/v1/reviews/{review['id']}")).status_code == 204


async def test_future_period_is_refused(client: AsyncClient, spaces: dict[str, str]) -> None:
    future = (date.today() + timedelta(days=60)).isoformat()
    response = await client.post(
        "/v1/reviews", json={"space_id": spaces["personal"], "period": "monthly", "day": future}
    )
    assert response.status_code == 422


async def test_automatic_reviews_run_as_coaching_agent(
    client: AsyncClient,
    spaces: dict[str, str],
    fake_llm: FakeProvider,
    database: Database,
    db: AsyncSession,
    settings: Settings,
) -> None:
    space = spaces["personal"]
    await _seed_week(client, space)
    await enable_cloud(client, space)
    status = (await client.get(f"/v1/reviews/automation?space_id={space}")).json()
    assert status["enabled"] is False
    assert status["missing_permissions"] == ["tasks", "journal", "goals", "facts"]

    enabled = (
        await client.put("/v1/reviews/automation", json={"space_id": space, "enabled": True})
    ).json()
    assert enabled["enabled"] is True and enabled["missing_permissions"] == []

    fake_llm.push(json.dumps(NARRATIVE))
    counts = await run_scheduled(
        db, database=database, settings=settings, llm=fake_llm, period="weekly"
    )
    await db.commit()
    assert counts == {"generated": 1, "skipped": 0}
    review = await db.scalar(select(Review))
    assert review is not None
    assert review.generated_by == "agent:coaching"
    assert review.narrative_status == "generated"
    # The coaching agent was not granted the conversations: they are left out.
    assert review.facts["omitted"] == ["messages"]
    notification = await db.scalar(select(Notification))
    assert notification is not None and notification.category == "suggestion"

    # Without its permissions, the agent does nothing (and the refusal is audited).
    await db.execute(update(AgentPermission).values(revoked_at=datetime.now(UTC)))
    await db.commit()
    counts = await run_scheduled(
        db, database=database, settings=settings, llm=fake_llm, period="weekly"
    )
    await db.commit()
    assert counts == {"generated": 0, "skipped": 1}
    denied = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "review.generate", AuditEvent.outcome == "denied"
        )
    )
    assert denied is not None and denied.actor_id == "coaching"

    disabled = (
        await client.put("/v1/reviews/automation", json={"space_id": space, "enabled": False})
    ).json()
    assert disabled["enabled"] is False


async def test_daily_automatic_summary_stays_local(
    client: AsyncClient,
    spaces: dict[str, str],
    fake_llm: FakeProvider,
    database: Database,
    db: AsyncSession,
    settings: Settings,
) -> None:
    space = spaces["personal"]
    await enable_cloud(client, space)
    await client.put("/v1/reviews/automation", json={"space_id": space, "enabled": True})
    counts = await run_scheduled(
        db, database=database, settings=settings, llm=fake_llm, period="daily"
    )
    await db.commit()
    assert counts["generated"] == 1
    review = await db.scalar(select(Review))
    assert review is not None and review.narrative_status == "none"
    assert fake_llm.requests == []
