import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text

from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.modules.chat.models import Conversation, Message
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.memory.models import Fact
from lifeos.modules.memory.quality import _negative
from lifeos.modules.retrieval.embedders import FakeEmbedder
from lifeos.modules.retrieval.indexer import index_pending

from .conftest import Owner, enable_cloud


async def _fact(client: AsyncClient, space: str, statement: str, **extra: object) -> str:
    response = await client.post(
        "/v1/memory/facts", json={"space_id": space, "statement": statement, **extra}
    )
    assert response.status_code == 201, response.text
    fact_id: str = response.json()["id"]
    return fact_id


async def _index(database: Database, embedder: FakeEmbedder) -> None:
    async with database.sessionmaker() as session, session.begin():
        await index_pending(session, embedder)


async def _review(client: AsyncClient, space: str) -> dict[str, object]:
    response = await client.get("/v1/memory/review", params={"space_id": space})
    assert response.status_code == 200, response.text
    result: dict[str, object] = response.json()
    return result


def _pairs(queue: dict[str, object]) -> dict[frozenset[str], tuple[str, str]]:
    items = queue["items"]
    assert isinstance(items, list)
    return {
        frozenset(f["id"] for f in item["facts"]): (item["kind"], item["reason"])
        for item in items
        if item["kind"] != "stale"
    }


def test_negation_detection_in_french() -> None:
    assert _negative("Je n'aime pas le café")
    assert _negative("Je ne bois jamais d'alcool")
    assert _negative("Je déteste courir")
    assert not _negative("J'aime le café")
    assert not _negative("Je préfère le matin")


async def test_exact_duplicates_are_found_without_a_local_model(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    space = spaces["personal"]
    first = await _fact(client, space, "J'aime le thé vert.")
    second = await _fact(client, space, "j'aime le the  vert")
    await _fact(client, space, "J'aime le thé vert.", kind="preference")  # another kind
    day_one = datetime(2026, 9, 1, 9, tzinfo=UTC).isoformat()
    day_two = datetime(2026, 9, 2, 9, tzinfo=UTC).isoformat()
    await _fact(client, space, "Séance de sport", kind="event", occurred_at=day_one)
    await _fact(client, space, "Séance de sport", kind="event", occurred_at=day_two)

    queue = await _review(client, space)
    assert queue["semantic"] is False
    assert _pairs(queue) == {
        frozenset({first, second}): ("duplicate", "Même texte enregistré plusieurs fois")
    }
    # Other spaces are not mixed in.
    assert _pairs(await _review(client, spaces["professional"])) == {}


async def test_close_facts_are_flagged_as_duplicates_or_possible_contradictions(
    app: FastAPI, client: AsyncClient, spaces: dict[str, str], database: Database
) -> None:
    embedder = FakeEmbedder()
    app.state.embedder = embedder
    space = spaces["personal"]
    morning = await _fact(client, space, "Je préfère courir le matin", kind="preference")
    reordered = await _fact(client, space, "Le matin, je préfère courir", kind="preference")
    likes = await _fact(client, space, "J'aime le café", kind="preference")
    dislikes = await _fact(client, space, "Je n'aime pas le café", kind="preference")
    early = await _fact(client, space, "Je préfère courir le matin tôt", kind="preference")
    same_text_other_kind = await _fact(client, space, "Le café", kind="decision")
    for day in (1, 8):
        await _fact(
            client,
            space,
            "Course au parc",
            kind="event",
            occurred_at=datetime(2026, 9, day, 7, tzinfo=UTC).isoformat(),
        )
    await _index(database, embedder)

    queue = await _review(client, space)
    assert queue["semantic"] is True
    pairs = _pairs(queue)
    assert pairs[frozenset({morning, reordered})][0] == "duplicate"
    assert pairs[frozenset({likes, dislikes})] == (
        "conflict",
        "Formulations opposées : laquelle est vraie aujourd'hui ?",
    )
    assert pairs[frozenset({morning, early})] == (
        "conflict",
        "Éléments proches : mise à jour ou contradiction ?",
    )
    assert not any(same_text_other_kind in pair for pair in pairs)
    # Routine events on different days are not duplicates.
    events = await client.get("/v1/memory/facts", params={"kind": "event"})
    event_ids = {f["id"] for f in events.json()["items"]}
    assert not any(pair & event_ids for pair in pairs)
    items = queue["items"]
    assert isinstance(items, list)
    assert items[0]["kind"] == "conflict"  # conflicts first

    # « Keep both »: the pair is no longer flagged.
    dismissed = await client.post("/v1/memory/review/dismiss", json={"fact_ids": [dislikes, likes]})
    assert dismissed.status_code == 204, dismissed.text
    again = await client.post("/v1/memory/review/dismiss", json={"fact_ids": [likes, dislikes]})
    assert again.status_code == 204  # idempotent
    # Retracting one fact resolves its pairs.
    await client.post(f"/v1/memory/facts/{reordered}/retract")
    pairs = _pairs(await _review(client, space))
    assert frozenset({likes, dislikes}) not in pairs
    assert frozenset({morning, reordered}) not in pairs
    assert frozenset({morning, early}) in pairs


async def test_dismiss_requires_two_facts_of_the_same_space(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    personal = await _fact(client, spaces["personal"], "Un fait")
    professional = await _fact(client, spaces["professional"], "Un autre fait")
    same = await client.post("/v1/memory/review/dismiss", json={"fact_ids": [personal, personal]})
    assert same.status_code == 422
    mixed = await client.post(
        "/v1/memory/review/dismiss", json={"fact_ids": [personal, professional]}
    )
    assert mixed.status_code == 422
    unknown = await client.post(
        "/v1/memory/review/dismiss", json={"fact_ids": [personal, str(uuid.uuid4())]}
    )
    assert unknown.status_code == 404


async def test_ageing_preferences_ask_for_confirmation(
    client: AsyncClient, spaces: dict[str, str], database: Database
) -> None:
    space = spaces["personal"]
    old = await _fact(client, space, "Je préfère les réunions le matin", kind="preference")
    recent = await _fact(client, space, "Je préfère le thé", kind="preference")
    old_fact_not_preference = await _fact(client, space, "J'ai une voiture")
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            text(
                "UPDATE facts SET created_at = now() - interval '200 days', origin = 'extracted'"
                " WHERE id IN (:a, :b)"
            ),
            {"a": old, "b": old_fact_not_preference},
        )

    items = (await _review(client, space))["items"]
    assert isinstance(items, list)
    stale = [item["facts"][0]["id"] for item in items if item["kind"] == "stale"]
    assert stale == [old]
    assert recent not in stale

    confirmed = await client.post(f"/v1/memory/facts/{old}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["origin"] == "user_confirmed"  # an extracted fact, now validated
    assert body["last_confirmed_at"] is not None
    items = (await _review(client, space))["items"]
    assert isinstance(items, list)
    assert [item for item in items if item["kind"] == "stale"] == []

    await client.post(f"/v1/memory/facts/{recent}/retract")
    refused = await client.post(f"/v1/memory/facts/{recent}/confirm")
    assert refused.status_code == 409


# --- Conversation consolidation -------------------------------------------------------------


async def _conversation(database: Database, owner: Owner, space: str) -> uuid.UUID:
    start = utcnow() - timedelta(hours=1)
    async with database.sessionmaker() as session, session.begin():
        conversation = Conversation(
            user_id=owner.user.id, space_id=uuid.UUID(space), title="Discussion"
        )
        session.add(conversation)
        await session.flush()
        for minute, (role, content) in enumerate(
            [
                ("user", "J'ai décidé d'arrêter le café à partir de lundi."),
                ("assistant", "Bonne idée ! Tu pourrais aussi courir le matin."),
                ("user", "Je dois appeler le plombier avant vendredi."),
            ]
        ):
            session.add(
                Message(
                    user_id=owner.user.id,
                    space_id=uuid.UUID(space),
                    conversation_id=conversation.id,
                    role=role,
                    content=content,
                    created_at=start + timedelta(minutes=minute),
                )
            )
    return conversation.id


async def test_conversation_consolidation_proposes_then_saves_after_approval(
    client: AsyncClient,
    spaces: dict[str, str],
    logged_in: Owner,
    database: Database,
    fake_llm: FakeProvider,
) -> None:
    space = spaces["personal"]
    await enable_cloud(client, space)
    conversation = await _conversation(database, logged_in, space)
    fake_llm.push(
        json.dumps(
            {
                "tasks": [{"title": "Appeler le plombier"}],
                "facts": [{"kind": "decision", "statement": "Arrêter le café"}],
            }
        )
    )

    result = await client.post(f"/v1/chat/conversations/{conversation}/consolidate")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["messages"] == 2 and body["proposed"] == 2
    request = fake_llm.requests[-1]
    assert request.purpose == "chat.consolidate"
    sent = request.messages[0].text
    # Only the owner's own words are analysed, never the assistant's answers.
    assert "plombier" in sent and "courir le matin" not in sent
    async with database.sessionmaker() as session:
        assert await session.scalar(select(Fact)) is None  # nothing before approval

    approval = (await client.get(f"/v1/approvals/{body['approval_id']}")).json()
    assert approval["action"] == "chat.apply_proposals"
    assert approval["preview"]["summary"] == "1 tâche(s), 1 fait(s) à enregistrer"
    approved = await client.post(
        f"/v1/approvals/{body['approval_id']}/approve", json={"params": approval["params"]}
    )
    assert approved.status_code == 200, approved.text
    async with database.sessionmaker() as session:
        fact = await session.scalar(select(Fact))
        assert fact is not None
        assert (fact.statement, fact.source_type, fact.source_id) == (
            "Arrêter le café",
            "conversation",
            conversation,
        )
    tasks = (await client.get("/v1/tasks")).json()["items"]
    assert [t["title"] for t in tasks] == ["Appeler le plombier"]
    details = (await client.get(f"/v1/chat/conversations/{conversation}")).json()
    assert details["consolidated_until"] is not None

    # Already consolidated: no new cloud call.
    calls = len(fake_llm.requests)
    again = await client.post(f"/v1/chat/conversations/{conversation}/consolidate")
    assert again.status_code == 409
    assert again.json()["code"] == "nothing_to_consolidate"
    assert len(fake_llm.requests) == calls


async def test_consolidation_without_durable_content_needs_no_approval(
    client: AsyncClient,
    spaces: dict[str, str],
    logged_in: Owner,
    database: Database,
    fake_llm: FakeProvider,
) -> None:
    space = spaces["personal"]
    await enable_cloud(client, space)
    conversation = await _conversation(database, logged_in, space)
    fake_llm.push(json.dumps({"tasks": [], "facts": []}))
    body = (await client.post(f"/v1/chat/conversations/{conversation}/consolidate")).json()
    assert body == {"approval_id": None, "messages": 2, "proposed": 0}
    again = await client.post(f"/v1/chat/conversations/{conversation}/consolidate")
    assert again.status_code == 409
