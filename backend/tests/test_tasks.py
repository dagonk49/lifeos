"""Task dependencies, blocked state and tags (F03, task 22)."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.modules.agents.context import _tasks
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.guardian.principal import agent_principal
from lifeos.modules.identity.models import Space, User
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.notifications.models import Notification
from lifeos.modules.notifications.service import dispatch_reminders
from lifeos.modules.tasks import service
from lifeos.modules.tasks.models import Task, TaskDependency

from .conftest import Owner, make_ctx


async def _task(client: AsyncClient, space: str, title: str, **extra: Any) -> dict[str, Any]:
    response = await client.post("/v1/tasks", json={"space_id": space, "title": title, **extra})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _link(client: AsyncClient, task: dict[str, Any], blocker: dict[str, Any]) -> Any:
    return await client.post(
        f"/v1/tasks/{task['id']}/dependencies", json={"depends_on_id": blocker["id"]}
    )


async def _set_status(client: AsyncClient, task_id: str, status: str) -> Any:
    return await client.patch(f"/v1/tasks/{task_id}", json={"status": status})


async def _unblocked(db: AsyncSession) -> list[Notification]:
    db.expire_all()
    rows = await db.scalars(select(Notification).where(Notification.title.like("Débloquée%")))
    return list(rows)


async def test_a_blocked_task_explains_why_and_is_released_once(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    personal = spaces["personal"]
    quote = await _task(client, personal, "Demander le devis")
    sign = await _task(client, personal, "Signer le devis", depends_on=[quote["id"]])
    assert sign["depends_on"] == [quote["id"]]
    assert sign["blocked"] is True

    shown = (await client.get(f"/v1/tasks/{sign['id']}")).json()
    assert shown["blocked_by"] == [
        {"id": quote["id"], "title": "Demander le devis", "status": "todo"}
    ]
    assert (await client.get(f"/v1/tasks/{quote['id']}")).json()["blocked"] is False

    # Neither started nor finished while it waits; the refusal names what it waits for.
    for status in ("in_progress", "done"):
        refused = await _set_status(client, sign["id"], status)
        assert refused.status_code == 409
        assert refused.json()["code"] == "task_blocked"
        assert [b["id"] for b in refused.json()["blocked_by"]] == [quote["id"]]
    # Cancelling it stays possible (and is not a release of anything).
    blocked_list = (await client.get("/v1/tasks?blocked=true")).json()["items"]
    assert [t["id"] for t in blocked_list] == [sign["id"]]
    free_list = (await client.get("/v1/tasks?blocked=false")).json()["items"]
    assert [t["id"] for t in free_list] == [quote["id"]]

    # Finishing the dependency frees the next task, with one notification.
    assert (await _set_status(client, quote["id"], "done")).status_code == 200
    freed = (await client.get(f"/v1/tasks/{sign['id']}")).json()
    assert freed["blocked"] is False and freed["blocked_by"] == []
    assert freed["depends_on"] == [quote["id"]]
    notes = await _unblocked(db)
    assert [n.title for n in notes] == ["Débloquée : Signer le devis"]
    assert notes[0].source_type == "task" and str(notes[0].source_id) == sign["id"]
    assert notes[0].category == "important"

    # Reopening blocks it again; finishing again does not repeat the notification.
    assert (await _set_status(client, quote["id"], "todo")).status_code == 200
    assert (await client.get(f"/v1/tasks/{sign['id']}")).json()["blocked"] is True
    assert (await _set_status(client, quote["id"], "done")).status_code == 200
    assert len(await _unblocked(db)) == 1
    started = await _set_status(client, sign["id"], "in_progress")
    assert started.status_code == 200, started.text
    assert (await _set_status(client, sign["id"], "done")).status_code == 200

    audit = await db.scalars(select(AuditEvent.details).where(AuditEvent.action == "task.update"))
    assert {"fields": "status", "released": 1} in list(audit)


async def test_a_task_waiting_for_two_is_released_by_the_last_one(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    personal = spaces["personal"]
    first = await _task(client, personal, "Réserver la salle")
    second = await _task(client, personal, "Envoyer les invitations")
    party = await _task(client, personal, "Fête", depends_on=[first["id"], second["id"]])
    assert len(party["blocked_by"]) == 2

    # A cancelled dependency no longer blocks.
    assert (await _set_status(client, first["id"], "cancelled")).status_code == 200
    assert (await client.get(f"/v1/tasks/{party['id']}")).json()["blocked_by"] == [
        {"id": second["id"], "title": "Envoyer les invitations", "status": "todo"}
    ]
    assert await _unblocked(db) == []
    assert (await _set_status(client, second["id"], "in_progress")).status_code == 200
    assert (await client.get(f"/v1/tasks/{party['id']}")).json()["blocked"] is True
    assert (await _set_status(client, second["id"], "done")).status_code == 200
    assert [n.title for n in await _unblocked(db)] == ["Débloquée : Fête"]


async def test_no_cycle_is_ever_accepted(client: AsyncClient, spaces: dict[str, str]) -> None:
    personal = spaces["personal"]
    a = await _task(client, personal, "A")
    b = await _task(client, personal, "B")
    c = await _task(client, personal, "C")

    itself = await _link(client, a, a)
    assert itself.status_code == 422 and itself.json()["code"] == "dependency_cycle"

    assert (await _link(client, a, b)).status_code == 201  # A waits for B
    assert (await _link(client, b, c)).status_code == 201  # B waits for C
    closing = await _link(client, c, a)  # C would wait for A: C -> A -> B -> C
    assert closing.status_code == 422
    assert closing.json()["code"] == "dependency_cycle"
    assert closing.json()["path"] == [c["id"], a["id"], b["id"], c["id"]]
    direct = await _link(client, b, a)
    assert direct.status_code == 422 and direct.json()["path"] == [b["id"], a["id"], b["id"]]

    shown = (await client.get(f"/v1/tasks/{c['id']}")).json()
    assert shown["depends_on"] == [] and shown["version"] == 1


async def test_links_stay_in_one_space_and_one_owner(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    mine = await _task(client, spaces["personal"], "Perso")
    work = await _task(client, spaces["professional"], "Pro")
    mismatch = await _link(client, mine, work)
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "dependency_space_mismatch"
    created = await client.post(
        "/v1/tasks",
        json={"space_id": spaces["personal"], "title": "X", "depends_on": [work["id"]]},
    )
    assert created.json()["code"] == "dependency_space_mismatch"

    stranger = User(email="other@example.org", display_name="Other", role="owner")
    db.add(stranger)
    await db.flush()
    foreign_space = Space(user_id=stranger.id, kind="personal", name="Perso")
    db.add(foreign_space)
    await db.flush()
    foreign = Task(user_id=stranger.id, space_id=foreign_space.id, title="Secret")
    db.add(foreign)
    await db.commit()
    hidden = await _link(client, mine, {"id": str(foreign.id)})
    assert hidden.status_code == 404 and hidden.json()["code"] == "task_not_found"
    reverse = await _link(client, {"id": str(foreign.id)}, mine)
    assert reverse.status_code == 404
    assert (await db.scalar(select(TaskDependency))) is None


async def test_links_change_the_version_and_can_be_removed(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    personal = spaces["personal"]
    first = await _task(client, personal, "Acheter la peinture")
    paint = await _task(client, personal, "Peindre")
    key = {"Idempotency-Key": "link-1"}
    linked = await client.post(
        f"/v1/tasks/{paint['id']}/dependencies", json={"depends_on_id": first["id"]}, headers=key
    )
    assert linked.status_code == 201 and linked.json()["version"] == 2
    replay = await client.post(
        f"/v1/tasks/{paint['id']}/dependencies", json={"depends_on_id": first["id"]}, headers=key
    )
    assert replay.json() == linked.json()
    again = await _link(client, paint, first)  # already there: nothing changes
    assert again.status_code == 201 and again.json()["version"] == 2

    stale = await client.patch(
        f"/v1/tasks/{paint['id']}", json={"title": "Peindre le salon"}, headers={"If-Match": "1"}
    )
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"

    url = f"/v1/tasks/{paint['id']}/dependencies/{first['id']}"
    assert (await client.delete(url)).status_code == 204
    gone = await client.delete(url)
    assert gone.status_code == 404 and gone.json()["code"] == "dependency_not_found"
    shown = (await client.get(f"/v1/tasks/{paint['id']}")).json()
    assert shown["depends_on"] == [] and shown["version"] == 3
    actions = list(
        await db.scalars(
            select(AuditEvent.action)
            .where(AuditEvent.action.like("task.dependency.%"))
            .order_by(AuditEvent.id)
        )
    )
    assert actions == ["task.dependency.add", "task.dependency.remove"]


async def test_deleting_the_dependency_frees_the_task(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    personal = spaces["personal"]
    first = await _task(client, personal, "Obsolète")
    then = await _task(client, personal, "Ensuite", depends_on=[first["id"]])
    assert (await client.delete(f"/v1/tasks/{first['id']}")).status_code == 204
    shown = (await client.get(f"/v1/tasks/{then['id']}")).json()
    assert shown["blocked"] is False and shown["depends_on"] == []
    assert (await db.scalar(select(TaskDependency))) is None
    assert (await _set_status(client, then["id"], "done")).status_code == 200


async def test_concurrent_opposite_links_cannot_both_succeed(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    personal = spaces["personal"]
    a = uuid.UUID((await _task(client, personal, "A"))["id"])
    b = uuid.UUID((await _task(client, personal, "B"))["id"])

    async def link(task_id: uuid.UUID, blocker_id: uuid.UUID) -> str:
        async with database.sessionmaker() as session:
            ctx = await make_ctx(database, session, settings, logged_in.user, fake_llm)
            try:
                await service.add_dependency(ctx, task_id, blocker_id)
                # Give the other request time to reach the lock before this one commits.
                await asyncio.sleep(0.2)
                await session.commit()
            except ProblemError as exc:
                await session.rollback()
                return exc.code
            return "ok"

    results = await asyncio.gather(link(a, b), link(b, a))
    assert sorted(results) == ["dependency_cycle", "ok"]
    async with database.sessionmaker() as session:
        assert len(list(await session.scalars(select(TaskDependency)))) == 1


async def test_tags_are_normalised_and_filtered(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    personal = spaces["personal"]
    task = await _task(
        client, personal, "Réparer le robinet", tags=[" Maison ", "#maison", "Urgent"]
    )
    assert task["tags"] == ["maison", "urgent"]
    await _task(client, personal, "Lire", tags=["loisirs"])
    found = (await client.get("/v1/tasks?tag=MAISON")).json()["items"]
    assert [t["id"] for t in found] == [task["id"]]

    patched = await client.patch(f"/v1/tasks/{task['id']}", json={"tags": ["Plomberie"]})
    assert patched.json()["tags"] == ["plomberie"]
    assert (await client.get("/v1/tasks?tag=maison")).json()["items"] == []

    for tags in (["x" * 41], [""], [f"t{i}" for i in range(11)], ["a,b"]):
        refused = await client.post(
            "/v1/tasks", json={"space_id": personal, "title": "T", "tags": tags}
        )
        assert refused.status_code == 422, tags
        assert refused.json()["code"] == "validation_error"
    assert (await client.get("/v1/tasks?tag=%20")).status_code == 422


async def test_export_holds_the_links(client: AsyncClient, spaces: dict[str, str]) -> None:
    personal = spaces["personal"]
    first = await _task(client, personal, "Un")
    second = await _task(client, personal, "Deux", depends_on=[first["id"]], tags=["x"])
    export = (await client.get("/v1/privacy/export")).json()
    assert [(d["task_id"], d["depends_on_id"]) for d in export["task_dependencies"]] == [
        (second["id"], first["id"])
    ]
    assert {t["title"]: t["tags"] for t in export["tasks"]} == {"Un": [], "Deux": ["x"]}


async def test_agents_link_tasks_only_with_a_write_permission(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    db: AsyncSession,
    settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    personal = spaces["personal"]
    first = uuid.UUID((await _task(client, personal, "Un"))["id"])
    second = uuid.UUID((await _task(client, personal, "Deux"))["id"])

    async def grant(access: str) -> None:
        response = await client.post(
            "/v1/guardian/permissions",
            json={
                "agent": "planning",
                "space_id": personal,
                "resource": "tasks",
                "access": access,
                "purpose": "Planifier la semaine",
            },
        )
        assert response.status_code == 201, response.text

    await grant("read")
    reader = await agent_principal(db, logged_in.user.id, "planning")
    ctx = await make_ctx(database, db, settings, logged_in.user, fake_llm, principal=reader)
    with pytest.raises(ProblemError) as refused:
        await service.add_dependency(ctx, second, first)
    assert refused.value.status == 403
    await db.rollback()

    await grant("write")
    writer = await agent_principal(db, logged_in.user.id, "planning")
    ctx = await make_ctx(database, db, settings, logged_in.user, fake_llm, principal=writer)
    task = await service.add_dependency(ctx, second, first)
    assert (await service.task_out(ctx, task)).blocked is True
    await db.commit()


async def test_planning_context_and_reminders_name_the_wait(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    db: AsyncSession,
    settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    personal = spaces["personal"]
    first = await _task(client, personal, "Recevoir le colis")
    await _task(
        client,
        personal,
        "Monter l'étagère",
        depends_on=[first["id"]],
        remind_at=(utcnow() - timedelta(minutes=1)).isoformat(),
    )
    ctx = await make_ctx(database, db, settings, logged_in.user, fake_llm)
    lines = await _tasks(ctx, uuid.UUID(personal), ZoneInfo("Europe/Paris"))
    assert "- Monter l'étagère (bloquée par « Recevoir le colis »)" in lines[0]

    assert await dispatch_reminders(db) == 1
    await db.commit()
    reminder = await db.scalar(select(Notification).where(Notification.title.like("Rappel%")))
    assert reminder is not None
    # Counted, never named: the blocker's title would outlive its erasure.
    assert reminder.body == "Encore bloquée par 1 tâche(s) non terminée(s)."


async def test_erasing_a_dependency_scrubs_the_replies_that_quote_it(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    personal = spaces["personal"]
    first = await _task(client, personal, "Consulter le notaire")
    body = {"space_id": personal, "title": "Signer l'acte", "depends_on": [first["id"]]}
    key = {"Idempotency-Key": "create-with-dependency"}
    created = await client.post("/v1/tasks", json=body, headers=key)
    assert created.json()["blocked_by"][0]["title"] == "Consulter le notaire"

    assert (await client.delete(f"/v1/tasks/{first['id']}")).status_code == 204
    # The stored reply held the erased task's title: it is no longer replayed.
    replay = await client.post("/v1/tasks", json=body, headers=key)
    assert replay.status_code == 410
    assert "Consulter" not in replay.text
