import dataclasses
import json
import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.ml.app import create_ml_app
from lifeos.modules.identity.models import User
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.retrieval.context import relevant_context
from lifeos.modules.retrieval.embedders import (
    EMBEDDING_DIMS,
    MODEL_SPECS,
    EmbeddingError,
    FakeEmbedder,
    Kind,
    RemoteEmbedder,
    fit_dims,
)
from lifeos.modules.retrieval.hybrid import Ref, fuse, vector_refs
from lifeos.modules.retrieval.indexer import (
    CHUNK_CHARS,
    MAX_CHUNKS,
    chunk_text,
    coverage,
    drop_other_models,
    index_pending,
)
from lifeos.modules.retrieval.models import Embedding

from .conftest import Owner, make_ctx


class FailingEmbedder:
    name = "failing"
    model = "fake-bow-768"

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        raise EmbeddingError("down")


async def _index(database: Database, embedder: object) -> dict[str, int]:
    async with database.sessionmaker() as session, session.begin():
        return await index_pending(session, embedder, batch=100)  # type: ignore[arg-type]


async def _vectors(database: Database, object_id: str | uuid.UUID) -> list[Embedding]:
    async with database.sessionmaker() as session:
        rows = await session.scalars(
            select(Embedding)
            .where(Embedding.object_id == uuid.UUID(str(object_id)))
            .order_by(Embedding.chunk)
        )
        return list(rows)


def _today(user_tz: str = "Europe/Paris") -> date:
    return utcnow().astimezone(ZoneInfo(user_tz)).date()


# --- Pure functions ---------------------------------------------------------------------


def test_chunk_text_packs_sentences_and_bounds_the_output() -> None:
    assert chunk_text("  Une seule phrase.  ") == ["Une seule phrase."]
    sentence = "Phrase de remplissage numéro {} pour tester le découpage."
    long_text = " ".join(sentence.format(i) for i in range(60))
    chunks = chunk_text(long_text)
    assert 1 < len(chunks) <= MAX_CHUNKS
    assert all(len(c) <= CHUNK_CHARS for c in chunks)
    assert chunks[0].startswith("Phrase de remplissage numéro 0 ")
    # A single sentence longer than a chunk is cut, never dropped.
    giant = "x" * (CHUNK_CHARS * 2 + 10)
    assert [len(c) for c in chunk_text(giant)] == [CHUNK_CHARS, CHUNK_CHARS, 10]
    # Huge texts are capped (the beginning is what gets embedded).
    assert len(chunk_text(" ".join(sentence.format(i) for i in range(1000)))) == MAX_CHUNKS


def test_rrf_fusion_rewards_agreement() -> None:
    a, b, c, d = (Ref("task", uuid.uuid4()) for _ in range(4))
    text_rank = [a, b, c]
    vector_rank = [c, d, a]
    fused = fuse(text_rank, vector_rank)
    assert fused[:2] == [a, c]  # found by both methods
    assert set(fused) == {a, b, c, d}
    assert fuse([], []) == []


def test_fit_dims_truncates_matryoshka_models_only() -> None:
    gemma = MODEL_SPECS["google/embeddinggemma-300m"]
    mpnet = MODEL_SPECS["sentence-transformers/paraphrase-multilingual-mpnet-base-v2"]
    qwen = MODEL_SPECS["Qwen/Qwen3-Embedding-0.6B-Q"]
    out = fit_dims([3.0, 4.0] + [0.0] * (EMBEDDING_DIMS - 2), gemma)
    assert out[:2] == [0.6, 0.8]  # normalised
    assert len(fit_dims([1.0] * 1024, qwen)) == EMBEDDING_DIMS
    with pytest.raises(EmbeddingError):
        fit_dims([1.0] * 1024, mpnet)
    with pytest.raises(EmbeddingError):
        fit_dims([1.0] * 384, gemma)


async def test_fake_embedder_brings_related_texts_closer() -> None:
    embedder = FakeEmbedder()
    query, related, unrelated = await embedder.embed(
        ["vélo au parc", "Balade à vélo dans le parc", "Déclaration d'impôts"], "passage"
    )

    def cosine(u: list[float], v: list[float]) -> float:
        return sum(x * y for x, y in zip(u, v, strict=True))

    assert cosine(query, related) > 0.5
    assert cosine(query, unrelated) < 0.2


# --- Indexing -----------------------------------------------------------------------------


async def test_indexer_embeds_once_reembeds_changes_and_follows_deletions(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, database: Database
) -> None:
    personal = spaces["personal"]
    task = (
        await client.post("/v1/tasks", json={"space_id": personal, "title": "Réparer le vélo"})
    ).json()
    long_entry = " ".join(f"Phrase numéro {i} du journal de la journée." for i in range(80))
    entry = (
        await client.post(
            "/v1/journal",
            json={"space_id": personal, "entry_date": str(_today()), "content": long_entry},
        )
    ).json()
    fact = (
        await client.post(
            "/v1/memory/facts", json={"space_id": personal, "statement": "J'habite à Lyon"}
        )
    ).json()
    embedder = FakeEmbedder()

    counts = await _index(database, embedder)
    assert counts == {"fact": 1, "journal": 1, "task": 1}
    journal_vectors = await _vectors(database, entry["id"])
    assert len(journal_vectors) > 1  # long entries are chunked
    assert [v.chunk for v in journal_vectors] == list(range(len(journal_vectors)))
    assert {v.user_id for v in journal_vectors} == {logged_in.user.id}

    # Idempotent: nothing is stale, the model is not called again.
    calls = embedder.calls
    assert await _index(database, embedder) == {}
    assert embedder.calls == calls

    # A changed object is re-embedded, and only it.
    await client.patch(f"/v1/tasks/{task['id']}", json={"notes": "Changer la chaîne"})
    assert await _index(database, embedder) == {"task": 1}
    [task_vector] = await _vectors(database, task["id"])
    async with database.sessionmaker() as session:
        expected = await session.scalar(select(func.md5("Réparer le vélo. Changer la chaîne")))
    assert task_vector.content_hash == expected

    # Moved to another space (no API for it yet): vectors follow, as they are filtered
    # by space.
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            text("UPDATE tasks SET space_id = :space WHERE id = :id"),
            {"space": spaces["professional"], "id": task["id"]},
        )
    assert await _index(database, embedder) == {"task": 1}
    [moved] = await _vectors(database, task["id"])
    assert str(moved.space_id) == spaces["professional"]

    # A retracted fact leaves the index at the next run.
    await client.post(f"/v1/memory/facts/{fact['id']}/retract")
    assert await _index(database, embedder) == {"pruned": 1}
    assert await _vectors(database, fact["id"]) == []

    # Erasure reaches the vectors, whatever deleted the object (database triggers).
    task_two = (
        await client.post("/v1/tasks", json={"space_id": personal, "title": "Autre tâche"})
    ).json()
    assert await _index(database, embedder) == {"task": 1}
    assert (await client.delete(f"/v1/tasks/{task_two['id']}")).status_code == 204
    assert await _vectors(database, task_two["id"]) == []
    async with database.sessionmaker() as session, session.begin():
        await session.execute(text("DELETE FROM journal_entries"))
    assert await _vectors(database, entry["id"]) == []

    async with database.sessionmaker() as session:
        progress = await coverage(session, logged_in.user.id, embedder.model)
    assert progress == {"model": embedder.model, "indexed": 1, "total": 1}


async def test_short_messages_are_not_indexed_and_other_models_are_dropped(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, database: Database
) -> None:
    from lifeos.modules.chat.models import Conversation, Message

    personal = uuid.UUID(spaces["personal"])
    async with database.sessionmaker() as session, session.begin():
        conversation = Conversation(user_id=logged_in.user.id, space_id=personal, title="Test")
        session.add(conversation)
        await session.flush()
        for content in ("ok", "Je préfère les réunions le matin, avant dix heures."):
            session.add(
                Message(
                    user_id=logged_in.user.id,
                    space_id=personal,
                    conversation_id=conversation.id,
                    role="user",
                    content=content,
                    created_at=utcnow(),
                )
            )
    assert await _index(database, FakeEmbedder()) == {"message": 1}

    class OtherModel(FakeEmbedder):
        model = "another-model"

    assert await _index(database, OtherModel()) == {"message": 1}
    async with database.sessionmaker() as session, session.begin():
        assert await drop_other_models(session, "another-model") == 1
        models = set(await session.scalars(select(Embedding.model)))
    assert models == {"another-model"}


async def test_concurrent_indexing_runs_do_not_collide(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, database: Database
) -> None:
    await client.post("/v1/tasks", json={"space_id": spaces["personal"], "title": "Une tâche"})
    async with database.sessionmaker() as first, first.begin():
        assert await index_pending(first, FakeEmbedder()) == {"task": 1}
        # The first run still holds its transaction: a second run backs off.
        assert await _index(database, FakeEmbedder()) == {}
    assert await _index(database, FakeEmbedder()) == {}


# --- Hybrid search -----------------------------------------------------------------------


@pytest.fixture
def embedder(app: FastAPI) -> FakeEmbedder:
    fake = FakeEmbedder()
    app.state.embedder = fake
    return fake


async def _corpus(client: AsyncClient, spaces: dict[str, str]) -> dict[str, str]:
    personal, professional = spaces["personal"], spaces["professional"]
    ids = {}
    ids["both"] = (
        await client.post(
            "/v1/tasks", json={"space_id": personal, "title": "Réparer le vélo pour aller au parc"}
        )
    ).json()["id"]
    ids["semantic"] = (
        await client.post(
            "/v1/journal",
            json={"space_id": personal, "entry_date": str(_today()), "content": "Balade au parc"},
        )
    ).json()["id"]
    ids["other_space"] = (
        await client.post(
            "/v1/memory/memories",
            json={"space_id": professional, "title": "Parc", "content": "Parc automobile"},
        )
    ).json()["id"]
    retracted = (
        await client.post(
            "/v1/memory/facts", json={"space_id": personal, "statement": "Vélo garé au parc"}
        )
    ).json()["id"]
    await client.post(f"/v1/memory/facts/{retracted}/retract")
    ids["retracted"] = retracted
    await client.post("/v1/tasks", json={"space_id": personal, "title": "Payer les impôts"})
    return ids


async def test_hybrid_search_fuses_text_and_meaning_with_the_same_filters(
    client: AsyncClient, spaces: dict[str, str], database: Database, embedder: FakeEmbedder
) -> None:
    ids = await _corpus(client, spaces)
    await _index(database, embedder)

    result = (await client.get("/v1/search", params={"q": "vélo parc"})).json()
    assert result["semantic"] is True
    matches = {hit["id"]: hit["match"] for hit in result["hits"]}
    assert matches[ids["both"]] == "both"
    assert matches[ids["semantic"]] == "semantic"
    assert matches[ids["other_space"]] == "semantic"
    assert ids["retracted"] not in matches  # inactive facts never surface
    assert result["hits"][0]["id"] == ids["both"]
    assert len(matches) == 3

    # Space, type and period filters apply to semantic hits too.
    scoped = (
        await client.get("/v1/search", params={"q": "vélo parc", "space_id": spaces["personal"]})
    ).json()
    assert {h["id"] for h in scoped["hits"]} == {ids["both"], ids["semantic"]}
    journal_only = (
        await client.get("/v1/search", params={"q": "vélo parc", "types": "journal"})
    ).json()
    assert [h["id"] for h in journal_only["hits"]] == [ids["semantic"]]
    later = str(_today() + timedelta(days=2))
    assert (await client.get("/v1/search", params={"q": "vélo parc", "from": later})).json()[
        "hits"
    ] == []
    limited = (await client.get("/v1/search", params={"q": "vélo parc", "limit": 1})).json()
    assert [h["id"] for h in limited["hits"]] == [ids["both"]]
    status = (await client.get("/v1/search/status")).json()
    assert status == {"semantic": True, "model": "fake-bow-768", "indexed": 4, "total": 4}


async def test_search_degrades_to_full_text_without_a_working_embedder(
    app: FastAPI, client: AsyncClient, spaces: dict[str, str], database: Database
) -> None:
    ids = await _corpus(client, spaces)
    await _index(database, FakeEmbedder())

    app.state.embedder = None
    status = (await client.get("/v1/search/status")).json()
    assert status == {"semantic": False, "model": None, "indexed": 0, "total": 0}
    plain = (await client.get("/v1/search", params={"q": "vélo parc"})).json()
    assert plain["semantic"] is False
    assert [(h["id"], h["match"]) for h in plain["hits"]] == [(ids["both"], "text")]

    app.state.embedder = FailingEmbedder()
    degraded = (await client.get("/v1/search", params={"q": "vélo parc"})).json()
    assert [(h["id"], h["match"]) for h in degraded["hits"]] == [(ids["both"], "text")]


async def test_vector_refs_are_scoped_to_owner_and_model(
    client: AsyncClient, spaces: dict[str, str], logged_in: Owner, database: Database
) -> None:
    await _corpus(client, spaces)
    embedder = FakeEmbedder()
    await _index(database, embedder)
    all_spaces = [uuid.UUID(s) for s in spaces.values()]
    async with database.sessionmaker() as session:
        mine = await vector_refs(
            session,
            embedder,
            "vélo parc",
            user_id=logged_in.user.id,
            space_ids=all_spaces,
            object_types=["task", "journal", "memory", "fact"],
        )
        assert len(mine) == 3  # the retracted fact is not indexed
        other_user = await vector_refs(
            session,
            embedder,
            "vélo parc",
            user_id=uuid.uuid4(),
            space_ids=all_spaces,
            object_types=["task"],
        )

        class OtherModel(FakeEmbedder):
            model = "another-model"

        other_model = await vector_refs(
            session,
            OtherModel(),
            "vélo parc",
            user_id=logged_in.user.id,
            space_ids=all_spaces,
            object_types=["task"],
        )
    assert other_user == [] and other_model == []


# --- Assistant context ---------------------------------------------------------------------


async def test_context_honours_french_time_expressions(
    client: AsyncClient,
    spaces: dict[str, str],
    logged_in: Owner,
    database: Database,
    settings: Settings,
    fake_llm: FakeProvider,
) -> None:
    personal = spaces["personal"]
    today = _today()
    last_week = today - timedelta(days=7)  # always inside the previous ISO week
    long_ago = today - timedelta(days=40)
    for day, content in (
        (last_week, "Rendez-vous chez le dentiste, tout va bien."),
        (long_ago, "Rendez-vous chez le garagiste pour la vidange."),
    ):
        await client.post(
            "/v1/journal", json={"space_id": personal, "entry_date": str(day), "content": content}
        )
    await client.post(
        "/v1/memory/memories",
        json={
            "space_id": personal,
            "title": "Allergie",
            "content": "Allergie à la pénicilline",
            "importance": 5,
            "protected": True,
        },
    )
    embedder = FakeEmbedder()
    await _index(database, embedder)

    async with database.sessionmaker() as session:
        user = await session.get(User, logged_in.user.id)
        assert user is not None
        ctx = dataclasses.replace(
            await make_ctx(database, session, settings, user, fake_llm), embedder=embedder
        )
        recent = await relevant_context(
            ctx, space_id=uuid.UUID(personal), query="Qu'ai-je fait la semaine dernière ?"
        )
        anytime = await relevant_context(
            ctx, space_id=uuid.UUID(personal), query="rendez-vous garagiste"
        )
    recent_texts = [item.text for item in recent if item.kind == "journal"]
    assert recent_texts == ["Rendez-vous chez le dentiste, tout va bien."]
    # Protected, important memories are always part of the context.
    assert any(item.text.startswith("Allergie — ") for item in recent)
    assert "Rendez-vous chez le garagiste pour la vidange." in [i.text for i in anytime]


# --- ml service and its client --------------------------------------------------------------


async def test_ml_service_embeds_and_reports_failures(settings: Settings) -> None:
    app = create_ml_app(settings, embedder=FakeEmbedder())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ml") as ml:
        assert (await ml.get("/healthz")).json() == {"status": "ok", "model": "fake-bow-768"}
        body = (await ml.post("/embed", json={"texts": ["a", "b"], "kind": "query"})).json()
        assert body["model"] == "fake-bow-768"
        assert [len(v) for v in body["vectors"]] == [EMBEDDING_DIMS, EMBEDDING_DIMS]
        too_many = await ml.post("/embed", json={"texts": ["x"] * 129, "kind": "passage"})
        assert too_many.status_code == 422
        bad_kind = await ml.post("/embed", json={"texts": ["x"], "kind": "other"})
        assert bad_kind.status_code == 422

    broken = create_ml_app(settings, embedder=FailingEmbedder())
    async with AsyncClient(transport=ASGITransport(app=broken), base_url="http://ml") as ml:
        failed = await ml.post("/embed", json={"texts": ["a"], "kind": "query"})
        assert failed.status_code == 503
        assert failed.json() == {"error": "embedding_unavailable"}


async def test_remote_embedder_batches_and_validates(settings: Settings) -> None:
    remote_settings = settings.model_copy(update={"embeddings_model": "fake-bow-768"})
    app = create_ml_app(settings, embedder=FakeEmbedder())
    sizes: list[int] = []

    async def record(request: httpx.Request) -> None:
        sizes.append(len(json.loads(request.content)["texts"]))

    client = AsyncClient(transport=ASGITransport(app=app), event_hooks={"request": [record]})
    remote = RemoteEmbedder(remote_settings, client=client)
    vectors = await remote.embed([f"texte {i}" for i in range(70)], "passage")
    assert len(vectors) == 70 and sizes == [8] * 8 + [6]
    assert vectors[5] == (await FakeEmbedder().embed(["texte 5"], "passage"))[0]

    other = RemoteEmbedder(settings.model_copy(update={"embeddings_model": "other"}), client)
    with pytest.raises(EmbeddingError, match="another model"):
        await other.embed(["a"], "query")

    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "fake-bow-768", "vectors": [[0.1, 0.2]]})

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    for handler in (malformed, unreachable):
        broken = RemoteEmbedder(
            remote_settings, client=AsyncClient(transport=httpx.MockTransport(handler))
        )
        with pytest.raises(EmbeddingError):
            await broken.embed(["a"], "query")


async def test_local_embedder_fails_fast_after_a_failed_load(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lifeos.modules.retrieval.embedders import LocalEmbedder

    embedder = LocalEmbedder(settings)
    attempts = 0

    def missing_model() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("model not downloaded")

    monkeypatch.setattr(embedder, "_load", missing_model)
    for _ in range(3):
        with pytest.raises(EmbeddingError):
            await embedder.embed(["a"], "query")
    assert attempts == 1  # no new (slow) attempt before the retry delay
