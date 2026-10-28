"""Chat, degraded mode, 90-day purge, notifications, dashboard, settings."""

from datetime import UTC, datetime, time, timedelta

from httpx import AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.modules.chat.models import Conversation, Message
from lifeos.modules.chat.service import purge_expired_messages
from lifeos.modules.llm.base import LLMUnavailableError
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.notifications.models import Notification
from lifeos.modules.notifications.policy import quiet_hours_delivery as next_delivery
from lifeos.modules.notifications.service import dispatch_reminders
from lifeos.modules.tasks.models import Task

from .conftest import enable_cloud


async def _conversation(client: AsyncClient, space_id: str) -> str:
    response = await client.post("/v1/chat/conversations", json={"space_id": space_id})
    assert response.status_code == 201
    conversation_id: str = response.json()["id"]
    return conversation_id


async def test_chat_degraded_mode_keeps_the_message(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    conversation = await _conversation(client, spaces["personal"])
    sent = await client.post(
        f"/v1/chat/conversations/{conversation}/messages", json={"content": "Bonjour"}
    )
    assert sent.status_code == 201
    body = sent.json()
    assert body["assistant_message"] is None
    assert body["error"]["code"] == "cloud_consent_required"
    assert fake_llm.requests == []
    detail = (await client.get(f"/v1/chat/conversations/{conversation}")).json()
    assert [m["content"] for m in detail["messages"]] == ["Bonjour"]
    assert detail["title"] == "Bonjour"


async def test_cloud_outage_does_not_block_local_features(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    await enable_cloud(client, spaces["personal"])
    await client.post("/v1/tasks", json={"space_id": spaces["personal"], "title": "Courses"})
    conversation = await _conversation(client, spaces["personal"])
    fake_llm.push(LLMUnavailableError("down"))
    sent = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages", json={"content": "Aide-moi"}
        )
    ).json()
    assert sent["error"]["code"] == "llm_unavailable"
    assert (await client.get("/v1/tasks")).status_code == 200
    dashboard = await client.get("/v1/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["tasks_without_due"] == 1


async def test_chat_context_is_minimal_and_scoped_to_the_space(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    await enable_cloud(client, spaces["personal"])
    await client.post(
        "/v1/memory/facts",
        json={"space_id": spaces["personal"], "statement": "Ma voiture est une Clio grise"},
    )
    await client.post(
        "/v1/memory/facts",
        json={
            "space_id": spaces["professional"],
            "statement": "La voiture de fonction est une Tesla",
        },
    )
    await client.post(
        "/v1/memory/facts",
        json={"space_id": spaces["personal"], "statement": "J'aime le jardinage"},
    )
    conversation = await _conversation(client, spaces["personal"])
    sent = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages",
            json={
                "content": "Quand faire la révision de ma voiture ? Mon tel 06 11 22 33 44",
                "context": {"screen": "chat"},
            },
        )
    ).json()
    assert sent["assistant_message"]["content"].startswith("[fake]")
    assert sent["context_items"] == 1
    request = fake_llm.requests[-1]
    assert request.system is not None
    assert "Clio grise" in request.system
    assert "Tesla" not in request.system  # other space
    assert "jardinage" not in request.system  # not relevant to the question
    assert "écran « chat »" in request.system
    assert "06 11 22 33 44" not in request.messages[-1].text


async def test_retention_purge_and_remember(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    conversation = await _conversation(client, spaces["personal"])
    for text in ("vieux message", "message conservé", "message récent"):
        await client.post(f"/v1/chat/conversations/{conversation}/messages", json={"content": text})
    messages = (await client.get(f"/v1/chat/conversations/{conversation}")).json()["messages"]
    by_text = {m["content"]: m for m in messages}
    retained = await client.post(
        f"/v1/chat/messages/{by_text['message conservé']['id']}/retain", json={"retained": True}
    )
    assert retained.json()["retained"] is True and retained.json()["purge_after"] is None
    remembered = await client.post(
        f"/v1/chat/messages/{by_text['message conservé']['id']}/remember"
    )
    assert remembered.status_code == 201
    assert remembered.json()["source_type"] == "message"

    await db.execute(
        update(Message)
        .where(Message.content.in_(["vieux message", "message conservé"]))
        .values(created_at=datetime.now(UTC) - timedelta(days=91))
    )
    await db.execute(
        update(Message)
        .where(Message.content == "vieux message")
        .values(purge_after=datetime.now(UTC) - timedelta(days=1))
    )
    await db.commit()
    counts = await purge_expired_messages(db)
    await db.commit()
    assert counts == {"messages": 1, "conversations": 0}
    remaining = list(await db.scalars(select(Message.content).order_by(Message.content)))
    assert remaining == ["message conservé", "message récent"]
    # The memory created from a message survives the purge of raw conversations.
    assert len((await client.get("/v1/memory/memories")).json()["items"]) == 1

    # Empty conversations older than 90 days disappear too.
    await db.execute(delete(Message))
    await db.execute(
        update(Conversation).values(last_message_at=datetime.now(UTC) - timedelta(days=91))
    )
    await db.commit()
    assert (await purge_expired_messages(db))["conversations"] == 1


def test_quiet_hours() -> None:
    tz = "Europe/Paris"
    night = (time(22, 0), time(7, 0))
    # 23:30 Paris (CEST, UTC+2) -> held until 07:00 the next day.
    late = datetime(2026, 10, 10, 21, 30, tzinfo=UTC)
    held = next_delivery(late, tz, *night, "important")
    assert held == datetime(2026, 10, 11, 5, 0, tzinfo=UTC)
    # 06:00 Paris -> held until 07:00 the same day.
    early = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)
    assert next_delivery(early, tz, *night, "suggestion") == datetime(
        2026, 10, 11, 5, 0, tzinfo=UTC
    )
    # Outside quiet hours, or no quiet hours: immediate.
    noon = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)
    assert next_delivery(noon, tz, *night, "important") == noon
    assert next_delivery(late, tz, None, None, "important") == late
    # Critical alerts wait too, unless the owner allowed them through (task 21).
    assert next_delivery(late, tz, *night, "critical") == held
    assert next_delivery(late, tz, *night, "critical", critical_bypass=True) == late
    assert next_delivery(noon, tz, *night, "critical") == noon
    assert next_delivery(late, tz, None, None, "critical") == late
    # Same-day window (lunch break).
    lunch = datetime(2026, 10, 11, 11, 30, tzinfo=UTC)  # 13:30 Paris
    assert next_delivery(lunch, tz, time(13, 0), time(14, 0), "important") == datetime(
        2026, 10, 11, 12, 0, tzinfo=UTC
    )


async def test_reminders_respect_quiet_hours_and_are_not_duplicated(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    now = datetime.now(UTC)
    # Quiet hours covering "now" whatever the time of the test run.
    paris_now = now.astimezone().time()
    start = (datetime.combine(now.date(), paris_now) - timedelta(hours=1)).time()
    end = (datetime.combine(now.date(), paris_now) + timedelta(hours=2)).time()
    settings = await client.patch(
        "/v1/settings",
        json={
            "timezone": "UTC",
            "quiet_hours_start": start.strftime("%H:%M"),
            "quiet_hours_end": end.strftime("%H:%M"),
        },
    )
    assert settings.status_code == 200, settings.text
    task = (
        await client.post(
            "/v1/tasks",
            json={
                "space_id": spaces["personal"],
                "title": "Prendre les médicaments",
                "remind_at": (now - timedelta(minutes=1)).isoformat(),
            },
        )
    ).json()
    assert await dispatch_reminders(db) == 1
    await db.commit()
    assert await dispatch_reminders(db) == 0  # already reminded
    await db.commit()

    notification = await db.scalar(select(Notification))
    assert notification is not None
    assert notification.deliver_after > now  # held by quiet hours
    assert (await client.get("/v1/notifications")).json() == []  # not delivered yet

    await db.execute(update(Notification).values(deliver_after=now - timedelta(seconds=1)))
    await db.commit()
    delivered = (await client.get("/v1/notifications")).json()
    assert [n["title"] for n in delivered] == ["Rappel : Prendre les médicaments"]
    assert delivered[0]["source_id"] == task["id"]
    read = await client.patch(f"/v1/notifications/{delivered[0]['id']}", json={"status": "read"})
    assert read.json()["status"] == "read"
    assert (await client.get("/v1/notifications")).json() == []

    # Changing the reminder time re-arms it.
    await client.patch(
        f"/v1/tasks/{task['id']}", json={"remind_at": (now - timedelta(seconds=5)).isoformat()}
    )
    assert await dispatch_reminders(db) == 1
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(Notification)) == 2
    reminded = await db.get(Task, task["id"])
    assert reminded is not None and reminded.reminded_at is not None


async def test_dashboard_buckets(client: AsyncClient, spaces: dict[str, str]) -> None:
    await client.patch("/v1/settings", json={"timezone": "Europe/Paris"})
    now = datetime.now(UTC)
    space = spaces["personal"]
    for title, due in (
        ("En retard", now - timedelta(days=2)),
        ("Dans trois jours", now + timedelta(days=3)),
        ("Sans date", None),
    ):
        await client.post(
            "/v1/tasks",
            json={"space_id": space, "title": title, "due_at": due.isoformat() if due else None},
        )
    data = (await client.get(f"/v1/dashboard?space_id={space}")).json()
    assert [t["title"] for t in data["tasks_overdue"]] == ["En retard"]
    assert [t["title"] for t in data["tasks_upcoming"]] == ["Dans trois jours"]
    assert data["tasks_without_due"] == 1
    assert data["assistant"]["provider"] == "fake"
    assert data["assistant"]["cloud_enabled_spaces"] == []
    assert data["generated_at"]


async def test_settings_validation(client: AsyncClient, spaces: dict[str, str]) -> None:
    bad_tz = await client.patch("/v1/settings", json={"timezone": "Mars/Olympus"})
    assert bad_tz.status_code == 422
    half = await client.patch("/v1/settings", json={"quiet_hours_start": "22:00"})
    assert half.status_code == 422
    assert half.json()["code"] == "quiet_hours_incomplete"
    ok = await client.patch(
        "/v1/settings",
        json={
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "llm_monthly_token_budget": 500_000,
        },
    )
    assert ok.json()["quiet_hours_end"] == "07:00:00"
    assert ok.json()["llm_monthly_token_budget"] == 500_000
    listed = (await client.get("/v1/spaces")).json()
    assert {s["kind"] for s in listed} == {"personal", "professional"}
