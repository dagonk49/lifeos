"""The full erasure cycle (docs/politique-de-donnees.md §2): a deleted object never comes
back, in search, in the assistant's context, in the export or in a stored copy, and the
erasures made after a backup are replayed after a restore."""

import asyncio
import base64
import csv
import io
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.cli import main as cli_main
from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.main import create_app
from lifeos.modules.agents.context import build_sections
from lifeos.modules.agents.registry import AGENT_SPECS
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.chat import service as chat
from lifeos.modules.documents import processing
from lifeos.modules.documents.extraction import FakeExtractor
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.goals import service as goals
from lifeos.modules.guardian.actions import purge_decided
from lifeos.modules.guardian.models import Approval
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.memory import service as memory
from lifeos.modules.notifications.models import Notification
from lifeos.modules.notifications.service import dispatch_reminders, purge_old
from lifeos.modules.privacy import erasure
from lifeos.modules.retrieval.context import relevant_context
from lifeos.modules.retrieval.embedders import FakeEmbedder
from lifeos.modules.retrieval.indexer import SOURCES, index_pending
from lifeos.modules.retrieval.models import Embedding
from lifeos.modules.tasks import service as tasks
from lifeos.workers.broker import broker

from .conftest import Owner, enable_cloud, make_ctx

WORD = "zibeline"  # rare enough to be found only where it was written
NARRATIVE = {
    "summary": "Semaine régulière : dossier envoyé, voiture gardée.",
    "progress": ["Dossier envoyé"],
    "obstacles": [],
    "suggestions": [],
    "interpretations": [],
    "questions": [],
}


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def _count(database: Database, sql: str, **params: Any) -> int:
    async with database.sessionmaker() as session:
        return int(await session.scalar(text(sql), params) or 0)


async def _vectors(database: Database, ids: list[str]) -> int:
    async with database.sessionmaker() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Embedding)
            .where(Embedding.object_id.in_([uuid.UUID(i) for i in ids]))
        )
    return int(count or 0)


async def _ctx(database: Database, settings: Settings, owner: Owner, fake_llm: FakeProvider) -> Any:
    session = database.sessionmaker()
    return await make_ctx(database, session, settings, owner.user, fake_llm)


# --- Search and AI context ----------------------------------------------------------------


async def test_a_deleted_memory_never_comes_back_in_search_or_ai_context(
    app: FastAPI,
    client: AsyncClient,
    spaces: dict[str, str],
    logged_in: Owner,
    database: Database,
    settings: Settings,
    fake_llm: FakeProvider,
) -> None:
    space = spaces["personal"]
    await enable_cloud(client, space)
    embedder = FakeEmbedder()
    app.state.embedder = embedder
    # Created with Idempotency-Key, as the web client does: their replies are stored.
    saved = (
        await client.post(
            "/v1/memory/memories",
            json={
                "space_id": space,
                "title": "Manteau",
                "content": f"Mon manteau en {WORD} est rangé au grenier.",
                "importance": 5,
                "protected": True,
            },
            headers=_key(),
        )
    ).json()
    fact = (
        await client.post(
            "/v1/memory/facts",
            json={"space_id": space, "statement": f"Le manteau en {WORD} vient de ma grand-mère"},
            headers=_key(),
        )
    ).json()
    entry = (
        await client.post(
            "/v1/journal",
            json={
                "space_id": space,
                "entry_date": date.today().isoformat(),
                "content": f"Rangé le manteau en {WORD} au grenier.",
            },
            headers=_key(),
        )
    ).json()
    ids = [saved["id"], fact["id"], entry["id"]]
    await index_pending(database, embedder, batch=50)
    assert await _vectors(database, ids) == 3

    async def ask() -> str:
        """A new conversation, a question without the word: what reaches the AI."""
        conversation = (
            await client.post("/v1/chat/conversations", json={"space_id": space})
        ).json()
        sent = await client.post(
            f"/v1/chat/conversations/{conversation['id']}/messages",
            json={"content": "Où est rangé mon manteau ?"},
        )
        assert sent.status_code == 201, sent.text
        request = fake_llm.requests[-1]
        return (request.system or "") + "".join(m.text for m in request.messages)

    async def agent_context() -> str:
        ctx = await _ctx(database, settings, logged_in, fake_llm)
        ctx.embedder = embedder
        try:
            sections = []
            for name in ("memory", "coaching"):
                sections += await build_sections(
                    ctx, AGENT_SPECS[name], uuid.UUID(space), "manteau grenier"
                )
            items = await relevant_context(ctx, space_id=uuid.UUID(space), query="manteau grenier")
            return "\n".join(sections + [item.text for item in items])
        finally:
            await ctx.db.close()

    # Before: everywhere.
    hits = (await client.get("/v1/search", params={"q": WORD})).json()["hits"]
    assert {h["id"] for h in hits} == set(ids)
    assert WORD in await ask()
    assert WORD in await agent_context()

    assert (
        await client.delete(f"/v1/memory/memories/{saved['id']}?confirm=true")
    ).status_code == 204
    assert (await client.delete(f"/v1/memory/facts/{fact['id']}")).status_code == 204
    assert (await client.delete(f"/v1/journal/{entry['id']}")).status_code == 204

    # After: nowhere. Full text, meaning, vectors, chat, agents, export, stored replies.
    for query in (WORD, "manteau grenier"):
        result = (await client.get("/v1/search", params={"q": query})).json()
        assert result["semantic"] is True
        assert not {h["id"] for h in result["hits"]} & set(ids)
    assert await _vectors(database, ids) == 0
    assert WORD not in await ask()
    assert WORD not in await agent_context()
    export = await client.get("/v1/privacy/export")
    assert WORD not in export.text
    assert (
        await _count(
            database,
            "SELECT count(*) FROM idempotency_keys WHERE response_body::text ILIKE :w",
            w=f"%{WORD}%",
        )
        == 0
    )
    # The audit trail never held it.
    assert (
        await _count(
            database,
            "SELECT count(*) FROM audit_events WHERE details::text ILIKE :w",
            w=f"%{WORD}%",
        )
        == 0
    )


async def test_deleting_a_fact_erases_its_whole_correction_history(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    space = spaces["personal"]

    async def chain(*statements: str) -> list[str]:
        first = (
            await client.post(
                "/v1/memory/facts",
                json={"space_id": space, "kind": "preference", "statement": statements[0]},
            )
        ).json()
        ids = [first["id"]]
        for statement in statements[1:]:
            corrected = await client.post(
                f"/v1/memory/facts/{ids[-1]}/correct", json={"statement": statement}
            )
            assert corrected.status_code == 201
            ids.append(corrected.json()["id"])
        return ids

    drinks = await chain("Je préfère le thé", "Je préfère le café", "Je préfère le chocolat")
    sports = await chain("Je cours le lundi", "Je cours le mardi")
    # From the latest version: every earlier one goes with it.
    assert (await client.delete(f"/v1/memory/facts/{drinks[-1]}")).status_code == 204
    # From an earlier version (listed with include_inactive): the later ones too.
    assert (await client.delete(f"/v1/memory/facts/{sports[0]}")).status_code == 204

    listed = (await client.get("/v1/memory/facts?include_inactive=true")).json()["items"]
    assert listed == []
    assert (await client.get("/v1/privacy/export")).json()["facts"] == []
    events = list(
        await db.scalars(
            select(AuditEvent).where(AuditEvent.action == "fact.delete").order_by(AuditEvent.id)
        )
    )
    # One event per version, identifiers only: the replay after a restore erases each one.
    assert sorted(e.target_id for e in events) == sorted(drinks + sports)
    assert {e.details["versions"] for e in events} == {2, 3}


async def test_a_retried_creation_of_an_erased_object_is_not_created_again(
    client: AsyncClient, spaces: dict[str, str], database: Database
) -> None:
    space = spaces["personal"]
    headers = _key()
    body = {"space_id": space, "title": f"Acheter du {WORD}"}
    created = await client.post("/v1/tasks", json=body, headers=headers)
    assert created.status_code == 201
    replayed = await client.post("/v1/tasks", json=body, headers=headers)
    assert replayed.json() == created.json()

    assert (await client.delete(f"/v1/tasks/{created.json()['id']}")).status_code == 204
    retried = await client.post("/v1/tasks", json=body, headers=headers)
    assert retried.status_code == 410
    assert retried.headers["content-type"].startswith("application/problem+json")
    assert retried.json()["code"] == "erased"
    assert WORD not in retried.text
    assert (await client.get("/v1/tasks")).json()["items"] == []
    # The key stays until it expires: retrying again changes nothing.
    assert (await client.post("/v1/tasks", json=body, headers=headers)).status_code == 410
    assert await _count(database, "SELECT count(*) FROM idempotency_keys") == 1

    # A chat message: its stored reply names the conversation and both messages.
    conversation = (await client.post("/v1/chat/conversations", json={"space_id": space})).json()
    path = f"/v1/chat/conversations/{conversation['id']}/messages"
    message_key = _key()
    sent = await client.post(path, json={"content": f"Le {WORD}"}, headers=message_key)
    assert sent.status_code == 201
    assert (await client.delete(f"/v1/chat/conversations/{conversation['id']}")).status_code == 204
    again = await client.post(path, json={"content": f"Le {WORD}"}, headers=message_key)
    assert again.status_code == 410 and WORD not in again.text


async def test_deleting_a_conversation_erases_its_messages_and_their_vectors(
    app: FastAPI, client: AsyncClient, spaces: dict[str, str], database: Database
) -> None:
    embedder = FakeEmbedder()
    app.state.embedder = embedder
    await enable_cloud(client, spaces["personal"])
    conversation = (
        await client.post("/v1/chat/conversations", json={"space_id": spaces["personal"]})
    ).json()
    await client.post(
        f"/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": f"Je cherche un manteau en {WORD} pour l'hiver."},
    )
    detail = (await client.get(f"/v1/chat/conversations/{conversation['id']}")).json()
    message_ids = [m["id"] for m in detail["messages"]]
    assert len(message_ids) == 2
    await index_pending(database, embedder, batch=50)
    assert await _vectors(database, message_ids) == 2

    assert (await client.delete(f"/v1/chat/conversations/{conversation['id']}")).status_code == 204
    # Messages by cascade, and their vectors by the trigger of each cascaded row.
    assert await _count(database, "SELECT count(*) FROM messages") == 0
    assert await _vectors(database, message_ids) == 0
    assert (await client.get("/v1/search", params={"q": WORD})).json()["hits"] == []


# --- What was saved from a source -------------------------------------------------------------


async def _analysed_entry(
    client: AsyncClient, space: str, fake_llm: FakeProvider, day: date
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A journal entry whose analysis the owner approved: one task, one fact."""
    entry = (
        await client.post(
            "/v1/journal",
            json={
                "space_id": space,
                "entry_date": day.isoformat(),
                "content": f"Vu le notaire pour le {WORD}. Je dois signer l'acte.",
            },
        )
    ).json()
    fake_llm.push(
        json.dumps(
            {
                "tasks": [{"title": f"Signer l'acte du {WORD}"}],
                "facts": [{"kind": "event", "statement": f"Rendez-vous chez le notaire ({WORD})"}],
            }
        )
    )
    approval = (await client.post(f"/v1/journal/{entry['id']}/analyze")).json()
    approved = await client.post(f"/v1/approvals/{approval['id']}/approve", json={})
    assert approved.json()["status"] == "executed", approved.text
    return entry, approval


async def test_what_was_saved_from_a_journal_entry_stays_unless_asked(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    space = spaces["personal"]
    await enable_cloud(client, space)
    entry, approval = await _analysed_entry(client, space, fake_llm, date.today())
    [fact] = (await client.get("/v1/memory/facts")).json()["items"]
    corrected = (
        await client.post(
            f"/v1/memory/facts/{fact['id']}/correct",
            json={"statement": f"Rendez-vous chez la notaire ({WORD})"},
        )
    ).json()

    # Shown before deleting: the latest version of the fact, and the task.
    derived = (await client.get(f"/v1/journal/{entry['id']}/derived")).json()
    assert [(i["type"], i["text"]) for i in derived["items"]] == [
        ("fact", f"Rendez-vous chez la notaire ({WORD})"),
        ("task", f"Signer l'acte du {WORD}"),
    ]
    assert derived["items"][0]["id"] == corrected["id"]

    # By default the entry goes, what was saved from it stays; the analysis that quotes
    # the entry (proposed texts) goes with it.
    assert (await client.delete(f"/v1/journal/{entry['id']}")).status_code == 204
    assert len((await client.get("/v1/tasks")).json()["items"]) == 1
    assert len((await client.get("/v1/memory/facts")).json()["items"]) == 1
    assert (await client.get(f"/v1/approvals/{approval['id']}")).status_code == 404

    # On request, with everything saved from it (the fact with its whole history).
    other, other_approval = await _analysed_entry(
        client, space, fake_llm, date.today() - timedelta(days=1)
    )
    assert (await client.delete(f"/v1/journal/{other['id']}?with_derived=true")).status_code == 204
    # Only what was saved from the first entry remains (its fact, with its history).
    facts = (await client.get("/v1/memory/facts?include_inactive=true")).json()["items"]
    assert {f["id"] for f in facts} == {corrected["id"], fact["id"]}
    remaining = (await client.get("/v1/tasks")).json()["items"]
    assert [t["source_id"] for t in remaining] == [entry["id"]]
    assert (await client.get(f"/v1/approvals/{other_approval['id']}")).status_code == 404
    audit = (await client.get("/v1/guardian/audit?action=journal.delete")).json()["items"]
    assert {
        (a["details"].get("derived_facts"), a["details"].get("derived_tasks")) for a in audit
    } == {
        (None, None),
        (1, 1),
    }

    # Unknown or foreign entries: 404; signed out: 401.
    assert (await client.get(f"/v1/journal/{uuid.uuid4()}/derived")).status_code == 404
    client.cookies.clear()
    assert (await client.get(f"/v1/journal/{entry['id']}/derived")).status_code == 401


async def test_a_conversation_lists_and_erases_on_request_what_was_saved_from_it(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider, database: Database
) -> None:
    space = spaces["personal"]
    await enable_cloud(client, space)
    conversation = (await client.post("/v1/chat/conversations", json={"space_id": space})).json()
    sent = (
        await client.post(
            f"/v1/chat/conversations/{conversation['id']}/messages",
            json={"content": f"J'ai décidé de vendre le manteau en {WORD}."},
        )
    ).json()
    remembered = await client.post(f"/v1/chat/messages/{sent['user_message']['id']}/remember")
    assert remembered.status_code == 201
    fake_llm.push(
        json.dumps(
            {
                "tasks": [{"title": f"Mettre en vente le {WORD}"}],
                "facts": [{"kind": "decision", "statement": f"Vendre le manteau en {WORD}"}],
            }
        )
    )
    consolidated = (
        await client.post(f"/v1/chat/conversations/{conversation['id']}/consolidate")
    ).json()
    approval_id = consolidated["approval_id"]
    await client.post(f"/v1/approvals/{approval_id}/approve", json={})

    derived = (await client.get(f"/v1/chat/conversations/{conversation['id']}/derived")).json()
    assert sorted(i["type"] for i in derived["items"]) == ["fact", "memory", "task"]
    assert (
        await client.delete(f"/v1/chat/conversations/{conversation['id']}?with_derived=true")
    ).status_code == 204
    assert (await client.get("/v1/tasks")).json()["items"] == []
    assert (await client.get("/v1/memory/facts")).json()["items"] == []
    assert (await client.get("/v1/memory/memories")).json()["items"] == []
    assert (await client.get(f"/v1/approvals/{approval_id}")).status_code == 404
    assert WORD not in (await client.get("/v1/privacy/export")).text
    assert (await client.get(f"/v1/chat/conversations/{uuid.uuid4()}/derived")).status_code == 404


# --- Notifications, reviews, approvals ------------------------------------------------------


async def test_deleting_a_task_removes_its_reminders(
    client: AsyncClient, spaces: dict[str, str], database: Database
) -> None:
    task = (
        await client.post(
            "/v1/tasks",
            json={
                "space_id": spaces["personal"],
                "title": f"Récupérer le {WORD}",
                "remind_at": (utcnow() - timedelta(minutes=1)).isoformat(),
            },
        )
    ).json()
    async with database.sessionmaker() as session, session.begin():
        assert await dispatch_reminders(session) == 1
    [notification] = (await client.get("/v1/notifications")).json()
    assert notification["title"] == f"Rappel : Récupérer le {WORD}"

    assert (await client.delete(f"/v1/tasks/{task['id']}")).status_code == 204
    assert await _count(database, "SELECT count(*) FROM notifications") == 0
    audit = (await client.get("/v1/guardian/audit?action=task.delete")).json()["items"]
    assert audit[0]["details"] == {"notifications": 1}


async def test_past_reviews_forget_erased_items_and_withdraw_their_reading(
    client: AsyncClient,
    spaces: dict[str, str],
    fake_llm: FakeProvider,
    logged_in: Owner,
    database: Database,
    settings: Settings,
) -> None:
    space = spaces["personal"]
    await enable_cloud(client, space)
    done = (
        await client.post("/v1/tasks", json={"space_id": space, "title": "Envoyer le dossier"})
    ).json()
    await client.patch(f"/v1/tasks/{done['id']}", json={"status": "done"})
    kept = (
        await client.post("/v1/tasks", json={"space_id": space, "title": "Payer le loyer"})
    ).json()
    await client.patch(f"/v1/tasks/{kept['id']}", json={"status": "done"})
    decision = (
        await client.post(
            "/v1/memory/facts",
            json={"space_id": space, "kind": "decision", "statement": "Je garde la voiture"},
        )
    ).json()
    entry = (
        await client.post(
            "/v1/journal",
            json={"space_id": space, "entry_date": date.today().isoformat(), "content": "Calme."},
        )
    ).json()

    async def weekly() -> dict[str, Any]:
        fake_llm.push(json.dumps(NARRATIVE))
        review = await client.post("/v1/reviews", json={"space_id": space, "period": "weekly"})
        assert review.json()["narrative_status"] == "generated"
        return dict(review.json())

    async def stored() -> dict[str, Any]:
        [review] = (await client.get("/v1/reviews?period=weekly")).json()
        return dict(review)

    review = await weekly()
    assert sorted(review["facts"]["tasks"]["completed_titles"]) == [
        "Envoyer le dossier",
        "Payer le loyer",
    ]

    # A journal entry of the period: the reading built from the journal is withdrawn.
    assert (await client.delete(f"/v1/journal/{entry['id']}")).status_code == 204
    after = await stored()
    assert after["narrative"] is None
    assert (after["narrative_status"], after["narrative_error"]) == ("none", "source_erased")
    assert after["facts"]["tasks"] == review["facts"]["tasks"]

    # A task and a fact listed by the review: they leave its facts, and the reading goes.
    await weekly()
    assert (await client.delete(f"/v1/tasks/{done['id']}")).status_code == 204
    assert (await client.delete(f"/v1/memory/facts/{decision['id']}")).status_code == 204
    after = await stored()
    assert after["facts"]["tasks"]["completed_titles"] == ["Payer le loyer"]
    assert after["facts"]["memory"]["decisions"] == []
    assert after["narrative"] is None

    # The coaching agent's context no longer carries the old reading.
    ctx = await _ctx(database, settings, logged_in, fake_llm)
    try:
        sections = await build_sections(ctx, AGENT_SPECS["coaching"], uuid.UUID(space), "bilan")
    finally:
        await ctx.db.close()
    assert not any("Synthèse" in s or "voiture" in s for s in sections)


async def test_decided_approvals_and_old_notifications_are_purged_after_90_days(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider, database: Database
) -> None:
    space = spaces["personal"]
    await enable_cloud(client, space)
    entry, approval = await _analysed_entry(client, space, fake_llm, date.today())
    fake_llm.push(json.dumps({"tasks": [{"title": "Autre"}], "facts": []}))
    pending = (await client.post(f"/v1/journal/{entry['id']}/analyze")).json()
    old = utcnow() - timedelta(days=91)
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(Approval).where(Approval.id == uuid.UUID(approval["id"])).values(decided_at=old)
        )
        await session.execute(
            update(Approval)
            .where(Approval.id == uuid.UUID(pending["id"]))
            .values(created_at=old, expires_at=old + timedelta(days=7))
        )
        for status, read_at, deliver_after in (
            ("read", old, old),  # read 91 days ago: purged
            ("pending", None, old),  # never read, delivered 91 days ago: purged
            ("read", utcnow() - timedelta(days=10), old),  # read recently: kept
        ):
            session.add(
                Notification(
                    user_id=(await session.scalar(select(Approval.user_id))),
                    space_id=uuid.UUID(space),
                    category="suggestion",
                    title="Votre bilan est prêt",
                    dedup_key=str(uuid.uuid4()),
                    deliver_after=deliver_after,
                    status=status,
                    read_at=read_at,
                    created_at=old,
                )
            )
    async with database.sessionmaker() as session, session.begin():
        # A pending approval is never purged, even an old one (it expires first).
        assert await purge_decided(session) == 1
        assert await purge_old(session) == 2
    assert (await client.get(f"/v1/approvals/{approval['id']}")).status_code == 404
    assert (await client.get(f"/v1/approvals/{pending['id']}")).status_code == 200
    assert await _count(database, "SELECT count(*) FROM notifications") == 1
    purges = "SELECT count(*) FROM audit_events WHERE action IN ('approval.purge', :other)"
    assert await _count(database, purges, other="notification.purge") == 2


async def test_milestone_deletion_is_audited_with_its_own_id(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    goal = (
        await client.post("/v1/goals", json={"space_id": spaces["personal"], "title": "Courir"})
    ).json()
    milestone = (
        await client.post(f"/v1/goals/{goal['id']}/milestones", json={"title": "5 km"})
    ).json()
    assert (
        await client.delete(f"/v1/goals/{goal['id']}/milestones/{milestone['id']}")
    ).status_code == 204
    event = await db.scalar(select(AuditEvent).where(AuditEvent.action == "goal.milestone.delete"))
    assert event is not None
    assert (event.target_type, event.target_id) == ("goal_milestone", milestone["id"])
    assert event.details == {"goal": goal["id"]}


# --- Index -------------------------------------------------------------------------------


async def test_every_indexed_source_has_an_erasure_trigger(db: AsyncSession) -> None:
    """A new indexed source without its trigger would leave vectors behind."""
    rows = await db.execute(
        text(
            "SELECT c.relname, pg_get_triggerdef(t.oid) FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal"
        )
    )
    definitions: dict[str, list[str]] = {}
    for table, definition in rows:
        definitions.setdefault(table, []).append(definition)
    for source in SOURCES:
        table = source.model.__tablename__
        assert any(
            f"{table}_delete_embeddings AFTER DELETE" in d
            and f"lifeos_delete_embeddings('{source.object_type}')" in d
            for d in definitions.get(table, [])
        ), table


# --- Documents ----------------------------------------------------------------------------


@pytest.fixture
def doc_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "documents_dir": str(tmp_path / "documents"),
            "documents_key": SecretStr(base64.b64encode(os.urandom(32)).decode()),
        }
    )


@pytest.fixture
async def doc_app(doc_settings: Settings, fake_llm: FakeProvider) -> AsyncIterator[FastAPI]:
    application = create_app(doc_settings)
    application.state.llm = fake_llm
    async with application.router.lifespan_context(application):
        yield application


async def test_document_erasure_is_complete(
    doc_app: FastAPI, doc_settings: Settings, owner: Owner, database: Database
) -> None:
    from httpx import ASGITransport

    from .conftest import ORIGIN, enroll

    broker.flush_all()
    store = DocumentStore(doc_settings)
    embedder = FakeEmbedder()
    doc_app.state.embedder = embedder
    async with AsyncClient(
        transport=ASGITransport(app=doc_app),
        base_url="http://testserver",
        headers={"Origin": ORIGIN, "User-Agent": "pytest"},
    ) as client:
        await enroll(client, owner)
        me = (await client.get("/v1/auth/me")).json()
        space = next(s["id"] for s in me["spaces"] if s["kind"] == "personal")
        data = f"Quittance du chalet {WORD}, loyer mensuel.".encode()
        headers = _key()
        upload = await client.post(
            "/v1/documents",
            files={"file": ("quittance.txt", data, "application/octet-stream")},
            data={"space_id": space, "purpose": "receipt"},
            headers=headers,
        )
        assert upload.status_code == 201, upload.text
        document = upload.json()
        await processing.process_document(
            database, store, FakeExtractor(), uuid.UUID(document["id"]), settings=doc_settings
        )
        transaction = (
            await client.post(
                "/v1/finance/transactions",
                json={
                    "space_id": space,
                    "tx_date": date.today().isoformat(),
                    "amount": "950.00",
                    "direction": "expense",
                    "label": "Loyer",
                    "source_type": "receipt",
                    "document_id": document["id"],
                },
            )
        ).json()
        await index_pending(database, embedder, batch=50)
        assert await _vectors(database, [document["id"]]) == 1
        async with database.sessionmaker() as session:
            key = await session.scalar(
                text("SELECT storage_key FROM documents WHERE id = :id"), {"id": document["id"]}
            )
        assert store._path(key).exists()

        assert (await client.delete(f"/v1/documents/{document['id']}")).status_code == 204
        # Row and extracted text, vectors, encrypted original, link of the transaction.
        assert await _count(database, "SELECT count(*) FROM documents") == 0
        assert await _vectors(database, [document["id"]]) == 0
        assert not store._path(key).exists()
        kept = (await client.get(f"/v1/finance/transactions/{transaction['id']}")).json()
        assert kept["document_id"] is None
        assert (await client.get("/v1/search", params={"q": WORD})).json()["hits"] == []
        assert WORD not in (await client.get("/v1/privacy/export")).text
        retried = await client.post(
            "/v1/documents",
            files={"file": ("quittance.txt", data, "application/octet-stream")},
            data={"space_id": space, "purpose": "receipt"},
            headers=headers,
        )
        assert retried.status_code == 410
        assert await _count(database, "SELECT count(*) FROM documents") == 0


# --- Replay after a restore -----------------------------------------------------------------


def _export(rows: list[tuple[Any, ...]]) -> str:
    """The list restore.sh exports from the replaced database (COPY ... CSV HEADER)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(erasure.EXPORT_COLUMNS)
    writer.writerows(rows)
    return out.getvalue()


async def test_erasures_made_after_a_backup_are_replayed_after_a_restore(
    app: FastAPI,
    client: AsyncClient,
    spaces: dict[str, str],
    logged_in: Owner,
    database: Database,
    settings: Settings,
    fake_llm: FakeProvider,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    space = spaces["personal"]
    embedder = FakeEmbedder()
    app.state.embedder = embedder
    task = (await client.post("/v1/tasks", json={"space_id": space, "title": f"Le {WORD}"})).json()
    kept = (await client.post("/v1/tasks", json={"space_id": space, "title": "Gardée"})).json()
    first = (
        await client.post("/v1/memory/facts", json={"space_id": space, "statement": "Avant"})
    ).json()
    latest = (
        await client.post(f"/v1/memory/facts/{first['id']}/correct", json={"statement": WORD})
    ).json()
    retracted = (
        await client.post("/v1/memory/facts", json={"space_id": space, "statement": "Faux"})
    ).json()
    saved = (
        await client.post(
            "/v1/memory/memories",
            json={"space_id": space, "title": "Souvenir", "content": f"Le {WORD}"},
        )
    ).json()
    conversation = (await client.post("/v1/chat/conversations", json={"space_id": space})).json()
    await client.post(
        f"/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": f"Parlons du {WORD} et de ses origines."},
    )
    goal = (await client.post("/v1/goals", json={"space_id": space, "title": "Objectif"})).json()
    milestone = (
        await client.post(f"/v1/goals/{goal['id']}/milestones", json={"title": "Étape"})
    ).json()
    await index_pending(database, embedder, batch=50)
    assert await _vectors(database, [task["id"], latest["id"], saved["id"]]) == 3

    # The owner's erasures after the backup, as audited; rolled back here, the database
    # stands for the restored backup that still holds everything.
    async with database.sessionmaker() as session:
        ctx = await make_ctx(database, session, settings, logged_in.user, fake_llm)
        await tasks.delete_task(ctx, uuid.UUID(task["id"]))
        await memory.delete_fact(ctx, uuid.UUID(latest["id"]))
        await memory.retract_fact(ctx, uuid.UUID(retracted["id"]))
        await memory.delete_memory(ctx, uuid.UUID(saved["id"]), confirm=False)
        await chat.delete_conversation(ctx, uuid.UUID(conversation["id"]))
        await goals.delete_milestone(ctx, uuid.UUID(goal["id"]), uuid.UUID(milestone["id"]))
        rows = list(
            (
                await session.execute(
                    text(
                        "SELECT occurred_at, user_id, action, target_type, target_id "
                        "FROM audit_events WHERE outcome = 'success' AND target_id IS NOT NULL "
                        "ORDER BY id"
                    )
                )
            ).all()
        )
        await session.rollback()
    # Not the owner's (forged or corrupted line), and lines the replay ignores.
    rows.append((utcnow(), uuid.uuid4(), "task.delete", "task", kept["id"]))
    rows.append((utcnow(), logged_in.user.id, "task.update", "task", kept["id"]))
    rows.append((utcnow(), logged_in.user.id, "task.delete", "task", "not-an-id"))
    listing = tmp_path / "erasures.csv"
    listing.write_text(_export(rows), encoding="utf-8")

    async def cli(*args: str) -> int:
        return await asyncio.to_thread(cli_main, list(args))

    # Dry run: what would go, nothing changes.
    assert await cli("replay-erasures", str(listing)) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "fact.delete 2" in out and "task.delete 1" in out
    assert "not the owner's: 1" in out
    assert await _count(database, "SELECT count(*) FROM tasks") == 2

    assert await cli("replay-erasures", str(listing), "--yes") == 0
    out = capsys.readouterr().out
    assert "Erased:" in out
    remaining = (await client.get("/v1/tasks")).json()["items"]
    assert [t["id"] for t in remaining] == [kept["id"]]
    assert (await client.get("/v1/memory/facts?include_inactive=true")).json()["items"][0][
        "status"
    ] == "retracted"
    assert len((await client.get("/v1/memory/facts?include_inactive=true")).json()["items"]) == 1
    assert (await client.get(f"/v1/memory/memories/{saved['id']}")).status_code == 404
    assert (await client.get(f"/v1/chat/conversations/{conversation['id']}")).status_code == 404
    assert await _count(database, "SELECT count(*) FROM goal_milestones") == 0
    assert await _vectors(database, [task["id"], latest["id"], first["id"], saved["id"]]) == 0
    assert WORD not in (await client.get("/v1/privacy/export")).text
    # Audited again: a later restore of an older backup replays them from this database.
    async with database.sessionmaker() as session:
        replayed = list(
            await session.scalars(
                select(AuditEvent.action).where(AuditEvent.actor_id == erasure.REPLAY_ACTOR)
            )
        )
    assert sorted(replayed) == sorted(
        [
            "task.delete",
            "fact.delete",
            "fact.delete",
            "fact.retract",
            "memory.delete",
            "conversation.delete",
            "goal.milestone.delete",
            "privacy.replay_erasures",
        ]
    )

    # Idempotent: a second run finds everything already absent.
    assert await cli("replay-erasures", str(listing), "--yes") == 0
    assert "Erased: none." in capsys.readouterr().out
    assert await cli("replay-erasures", str(tmp_path / "missing.csv")) == 1


async def test_the_replay_lists_the_originals_of_erased_documents_for_removal(
    db: AsyncSession, doc_settings: Settings, owner: Owner
) -> None:
    present = uuid.uuid4()
    key = DocumentStore.key_for(owner.user.id, present)
    listing = _export(
        [(utcnow(), owner.user.id, "document.delete", "document", present)]
    ).splitlines()
    result = await erasure.replay(db, erasure.parse_erasures(listing), apply=True)
    # The row was gone already (uploaded after the backup, or taken after the files):
    # its file, restored from the archive, is listed for removal after the commit.
    assert (result.absent, result.files) == (1, [key])


async def test_erasure_helpers_ignore_what_they_do_not_know(db: AsyncSession) -> None:
    # Nothing to scrub, nothing to forget: no query.
    assert await erasure.scrub_idempotency(db, uuid.uuid4(), []) == 0
    assert await erasure.forget_notifications(db, uuid.uuid4(), "task", []) == 0
    assert await erasure.scrub_reviews(db, uuid.uuid4(), uuid.uuid4()) == 0
    assert erasure.parse_erasures(["occurred_at,user_id,action"]) == []
    # List items that are, or are named by, an erased text go; other values stay.
    assert erasure._strip(
        {"a": ["x", {"title": "x", "n": 1}, {"title": "y"}], "b": "x"}, {"x"}
    ) == {"a": [{"title": "y"}], "b": "x"}
