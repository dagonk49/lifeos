import base64
import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import dramatiq
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, update

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.main import create_app
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.retrieval.embedders import FakeEmbedder
from lifeos.modules.retrieval.indexer import index_pending
from lifeos.modules.retrieval.models import Embedding
from lifeos.modules.voice import processing
from lifeos.modules.voice.service import purge_dictations
from lifeos.modules.voice.transcriber import (
    FakeTranscriber,
    RemoteTranscriber,
    Transcriber,
    Transcription,
    TranscriptionError,
    UntranscribableAudio,
    build_transcriber,
    parse_response,
)
from lifeos.workers.broker import broker

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64 + b"fake opus recording"
OGG = b"OggS\x00\x02" + b"\x00" * 64
SAID = "Rappelle-moi d'acheter du pain demain matin."


@pytest.fixture
def voice_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "documents_dir": str(tmp_path / "documents"),
            "documents_key": SecretStr(base64.b64encode(os.urandom(32)).decode()),
            "documents_max_bytes": 64 * 1024,
            "stt_provider": "fake",
        }
    )


@pytest.fixture
async def app(voice_settings: Settings, fake_llm: FakeProvider) -> AsyncIterator[FastAPI]:
    application = create_app(voice_settings)
    application.state.llm = fake_llm
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def store(voice_settings: Settings) -> DocumentStore:
    return DocumentStore(voice_settings)


@pytest.fixture(autouse=True)
def empty_queues() -> None:
    broker.flush_all()


def _queued(queue: str) -> list[str]:
    names = []
    pending = broker.queues.get(queue)  # type: ignore[attr-defined]
    while pending is not None and not pending.empty():
        names.append(dramatiq.Message.decode(pending.get_nowait()).actor_name)
    return names


def _files(directory: str) -> list[Path]:
    root = Path(directory)
    return sorted(root.glob("*/*.bin")) if root.is_dir() else []


async def dictate(client: AsyncClient, space_id: str, data: bytes = WEBM) -> dict[str, object]:
    response = await client.post(
        "/v1/voice/dictations",
        files={"file": ("enregistrement.webm", data, "audio/webm")},
        data={"space_id": space_id},
    )
    assert response.status_code == 202, response.text
    body: dict[str, object] = response.json()
    return body


async def run(
    database: Database,
    store: DocumentStore,
    transcriber: Transcriber | None,
    settings: Settings,
) -> dict[str, int]:
    return await processing.transcribe_pending(database, store, transcriber, settings=settings)


async def test_dictation_is_transcribed_locally_then_the_audio_is_deleted(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    voice_settings: Settings,
) -> None:
    status = (await client.get("/v1/voice/status")).json()
    assert status == {"transcription": True, "max_seconds": voice_settings.voice_max_seconds}

    dictation = await dictate(client, spaces["personal"])
    assert dictation["status"] == "pending" and dictation["text"] is None
    assert _queued("stt") == ["transcribe_dictations"]
    [stored] = _files(voice_settings.documents_dir)
    assert WEBM not in stored.read_bytes()  # encrypted at rest

    transcriber = FakeTranscriber(SAID)
    assert await run(database, store, transcriber, voice_settings) == {"ready": 1}
    assert transcriber.calls == 1
    assert _files(voice_settings.documents_dir) == []  # the recording is gone

    done = (await client.get(f"/v1/voice/dictations/{dictation['id']}")).json()
    assert done["status"] == "ready" and done["text"] == SAID and done["duration"] == 1.0
    async with database.sessionmaker() as session:
        document = await session.get(Document, uuid.UUID(str(dictation["id"])))
        assert document is not None
        assert document.storage_key is None and document.text_source == "speech"

    # Transient: not a document of the library, not searchable, not embedded.
    assert (await client.get("/v1/documents")).json()["items"] == []
    listed = await client.get("/v1/documents", params={"purpose": "dictation"})
    assert [d["id"] for d in listed.json()["items"]] == [dictation["id"]]
    search = await client.get("/v1/search", params={"q": "pain demain"})
    assert search.json()["hits"] == []
    await index_pending(database, FakeEmbedder(), batch=50)
    async with database.sessionmaker() as session:
        vectors = await session.scalar(
            select(func.count()).select_from(Embedding).where(Embedding.object_type == "document")
        )
    assert vectors == 0

    # Used by the client: deleted.
    assert (await client.delete(f"/v1/voice/dictations/{dictation['id']}")).status_code == 204
    gone = await client.get(f"/v1/voice/dictations/{dictation['id']}")
    assert gone.status_code == 404 and gone.json()["code"] == "dictation_not_found"

    # The audit has identifiers and sizes only.
    async with database.sessionmaker() as session:
        events = list(
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.target_id == str(dictation["id"]))
                .order_by(AuditEvent.id)
            )
        )
    assert [e.action for e in events] == [
        "dictation.create",
        "dictation.transcribe",
        "dictation.delete",
    ]
    assert "pain" not in " ".join(str(e.details) for e in events)


async def test_unavailable_transcription_is_retried_and_bad_audio_fails(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    voice_settings: Settings,
) -> None:
    first = await dictate(client, spaces["personal"])
    second = await dictate(client, spaces["personal"], OGG)

    def down(data: bytes, mime: str) -> Transcription:
        raise TranscriptionError("stt down")

    # Unavailable: back to pending, the recording kept, the next ones not even tried.
    assert await run(database, store, FakeTranscriber(handler=down), voice_settings) == {
        "deferred": 1
    }
    waiting = (await client.get(f"/v1/voice/dictations/{first['id']}")).json()
    assert (waiting["status"], waiting["error"]) == ("pending", "transcription_unavailable")
    assert len(_files(voice_settings.documents_dir)) == 2
    # Not retried before the delay...
    assert await run(database, store, FakeTranscriber(SAID), voice_settings) == {"ready": 1}
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(str(first["id"])))
            .values(updated_at=utcnow() - timedelta(minutes=5))
        )

    def too_long(data: bytes, mime: str) -> Transcription:
        raise UntranscribableAudio("too_long")

    # ...then retried; this time the recording is refused: failed, and deleted anyway.
    assert await run(database, store, FakeTranscriber(handler=too_long), voice_settings) == {
        "failed": 1
    }
    failed = (await client.get(f"/v1/voice/dictations/{first['id']}")).json()
    assert (failed["status"], failed["error"], failed["text"]) == ("failed", "too_long", None)
    ready = (await client.get(f"/v1/voice/dictations/{second['id']}")).json()
    assert ready["status"] == "ready"
    assert _files(voice_settings.documents_dir) == []


async def test_one_transcription_at_a_time(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    voice_settings: Settings,
) -> None:
    await dictate(client, spaces["personal"])
    transcriber = FakeTranscriber(SAID)
    async with database.engine.connect() as other:
        await other.execute(select(func.pg_advisory_lock(processing.TRANSCRIBE_LOCK)))
        assert await run(database, store, transcriber, voice_settings) == {}
        await other.execute(select(func.pg_advisory_unlock(processing.TRANSCRIBE_LOCK)))
        await other.commit()
    assert transcriber.calls == 0
    assert await run(database, store, transcriber, voice_settings) == {"ready": 1}
    # Without a transcriber (stt disabled in the worker), nothing happens.
    assert await run(database, store, None, voice_settings) == {}


async def test_uploads_are_checked(
    client: AsyncClient, spaces: dict[str, str], voice_settings: Settings
) -> None:
    personal = spaces["personal"]
    for data in (b"%PDF-1.7 not audio", b"just some text"):
        response = await client.post(
            "/v1/voice/dictations",
            files={"file": ("x", data, "audio/webm")},
            data={"space_id": personal},
        )
        assert response.status_code == 415 and response.json()["code"] == "unsupported_type"
    response = await client.post(
        "/v1/voice/dictations",
        files={"file": ("x", WEBM, "audio/webm")},
        data={"space_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404 and response.json()["code"] == "space_not_found"
    big = await client.post(
        "/v1/voice/dictations",
        files={"file": ("x", WEBM + bytes(70 * 1024), "audio/webm")},
        data={"space_id": personal},
    )
    assert big.status_code == 413
    assert _files(voice_settings.documents_dir) == []  # nothing stored


async def test_disabled_transcription_refuses_dictations(
    settings: Settings, fake_llm: FakeProvider, spaces: dict[str, str], client: AsyncClient
) -> None:
    # The default test settings have no stt provider.
    application = create_app(settings)
    application.state.llm = fake_llm
    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url=str(client.base_url),
            cookies=client.cookies,
            headers=dict(client.headers),
        ) as other,
    ):
        assert (await other.get("/v1/voice/status")).json()["transcription"] is False
        response = await other.post(
            "/v1/voice/dictations",
            files={"file": ("x", WEBM, "audio/webm")},
            data={"space_id": spaces["personal"]},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "transcription_unavailable"


async def test_unused_dictations_are_erased_after_a_day(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    voice_settings: Settings,
) -> None:
    old = await dictate(client, spaces["personal"])
    recent = await dictate(client, spaces["personal"])
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(str(old["id"])))
            .values(created_at=utcnow() - timedelta(hours=25))
        )
    async with database.sessionmaker() as session, session.begin():
        assert await purge_dictations(session, store) == 1
    assert (await client.get(f"/v1/voice/dictations/{old['id']}")).status_code == 404
    assert (await client.get(f"/v1/voice/dictations/{recent['id']}")).status_code == 200
    assert len(_files(voice_settings.documents_dir)) == 1


async def test_transcriber_contract(voice_settings: Settings) -> None:
    assert isinstance(build_transcriber(voice_settings), FakeTranscriber)
    remote = voice_settings.model_copy(update={"stt_provider": "remote"})
    assert isinstance(build_transcriber(remote), RemoteTranscriber)
    assert build_transcriber(voice_settings.model_copy(update={"stt_provider": "disabled"})) is None
    assert parse_response({"text": "bonjour", "duration": 2.5}) == Transcription("bonjour", 2.5)
    for bad in (None, [], {"text": 3, "duration": 1}, {"text": "x", "duration": -1}):
        with pytest.raises(TranscriptionError):
            parse_response(bad)


def test_voice_jobs_are_scheduled() -> None:
    from lifeos.workers.scheduler import build_scheduler

    jobs = {job.id for job in build_scheduler().get_jobs()}
    assert {"transcribe_dictations", "purge_old_dictations"} <= jobs


def test_lock_is_not_the_documents_one() -> None:
    from lifeos.modules.documents import processing as documents

    assert processing.TRANSCRIBE_LOCK != documents.PROCESS_LOCK
