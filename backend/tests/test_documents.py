import base64
import hashlib
import os
import time
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.main import create_app
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.documents import processing, service
from lifeos.modules.documents.extraction import (
    Extraction,
    ExtractionError,
    FakeExtractor,
    RemoteExtractor,
    UnreadableDocument,
    build_extractor,
)
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.router import content_disposition
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.guardian.models import RESOURCES
from lifeos.modules.guardian.policy import ACTIONS
from lifeos.modules.guardian.principal import Principal
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.retrieval.embedders import FakeEmbedder
from lifeos.modules.retrieval.indexer import index_pending
from lifeos.modules.retrieval.models import Embedding
from lifeos.workers.broker import broker

from .conftest import Owner, make_ctx

MAX_BYTES = 256 * 1024
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR fake png body"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF fake jpeg body"
PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF"
NOTE = "Notes de réunion : livraison du vélo cargo prévue jeudi.".encode()
RIB = """RELEVÉ D'IDENTITÉ BANCAIRE
Titulaire : Camille Martin
IBAN : FR76 3000 6000 0112 3456 7890 189
BIC : AGRIFRPP"""
RECEIPT = """CARREFOUR MARKET
12 rue de la Paix
LAIT DEMI ECREME 1L        1,15
PAIN DE CAMPAGNE           2,30
TOTAL TTC                  3,45 EUR
CB EMV  **** **** **** 1234
10/10/2026 18:42  CAISSE 3"""


@pytest.fixture
def doc_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "documents_dir": str(tmp_path / "documents"),
            "documents_key": SecretStr(base64.b64encode(os.urandom(32)).decode()),
            "documents_max_bytes": MAX_BYTES,
        }
    )


@pytest.fixture
async def app(doc_settings: Settings, fake_llm: FakeProvider) -> AsyncIterator[FastAPI]:
    """The application with document storage configured (key and directory)."""
    application = create_app(doc_settings)
    application.state.llm = fake_llm
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def store(doc_settings: Settings) -> DocumentStore:
    return DocumentStore(doc_settings)


@pytest.fixture
def extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture(autouse=True)
def empty_queues() -> None:
    broker.flush_all()


async def upload(
    client: AsyncClient,
    space_id: str,
    data: bytes,
    *,
    filename: str = "note.txt",
    headers: dict[str, str] | None = None,
    **fields: Any,
) -> httpx.Response:
    form = {"space_id": space_id}
    for key, value in fields.items():
        form[key] = str(value).lower() if isinstance(value, bool) else str(value)
    return await client.post(
        "/v1/documents",
        files={"file": (filename, data, "application/octet-stream")},
        data=form,
        headers=headers or {},
    )


async def created(client: AsyncClient, space_id: str, data: bytes, **fields: Any) -> dict[str, Any]:
    response = await upload(client, space_id, data, **fields)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def process(
    database: Database,
    store: DocumentStore,
    extractor: FakeExtractor | None,
    document_id: str,
    settings: Settings,
) -> str:
    return await processing.process_document(
        database, store, extractor, uuid.UUID(document_id), settings=settings
    )


async def _audit(database: Database, document_id: str) -> list[AuditEvent]:
    async with database.sessionmaker() as session:
        rows = await session.scalars(
            select(AuditEvent).where(AuditEvent.target_id == document_id).order_by(AuditEvent.id)
        )
        return list(rows)


def _stored_files(directory: str | Path) -> list[Path]:
    """Encrypted originals on disk (sync helper: file system access)."""
    root = Path(directory)
    return sorted(root.glob("*/*.bin")) if root.is_dir() else []


def _queued(queue: str = "documents") -> list[str]:
    import dramatiq

    messages = []
    pending = broker.queues.get(queue)  # type: ignore[attr-defined]
    while pending is not None and not pending.empty():
        message = dramatiq.Message.decode(pending.get_nowait())  # type: ignore[var-annotated]
        messages.append(f"{message.actor_name}:{message.args[0] if message.args else ''}")
    return messages


# --- Upload, read, download, update, delete -------------------------------------------------


async def test_document_lifecycle_through_the_api(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    extractor: FakeExtractor,
    doc_settings: Settings,
) -> None:
    personal = spaces["personal"]
    response = await upload(client, personal, NOTE, filename="../../Compte rendu.txt")
    assert response.status_code == 201, response.text
    document = response.json()
    assert document["title"] == "Compte rendu"
    assert (document["kind"], document["mime"], document["status"]) == (
        "text",
        "text/plain",
        "pending",
    )
    assert document["size_bytes"] == len(NOTE) and document["original_available"] is True
    assert document["keep_original"] is False and document["extracted_text"] is None
    retention = timedelta(days=doc_settings.documents_original_retention_days)
    until = document["original_until"]
    assert abs((utcnow() + retention).timestamp() - _ts(until)) < 60
    # Processing is enqueued for the worker after the commit (by actor name).
    assert _queued() == [f"process_document:{document['id']}"]

    # Encrypted at rest, under the owner's directory.
    [stored] = _stored_files(doc_settings.documents_dir)
    assert stored.name == f"{document['id']}.bin" and NOTE not in stored.read_bytes()

    assert await process(database, store, extractor, document["id"], doc_settings) == "ready"
    # Idempotent: already processed.
    assert await process(database, store, extractor, document["id"], doc_settings) == "skipped"
    detail = (await client.get(f"/v1/documents/{document['id']}")).json()
    assert detail["status"] == "ready" and detail["extracted_text"] == NOTE.decode()
    assert detail["text_source"] == "text" and detail["sensitivity"] == []
    assert detail["analysis"] == {"sensitivity_reasons": {}}
    assert extractor.calls == 0  # plain text is decoded in-process

    listing = (await client.get("/v1/documents", params={"space_id": personal})).json()
    assert [d["id"] for d in listing["items"]] == [document["id"]]
    assert "extracted_text" not in listing["items"][0]
    assert (await client.get("/v1/documents", params={"status": "pending"})).json()["items"] == []

    download = await client.get(f"/v1/documents/{document['id']}/original")
    assert download.status_code == 200 and download.content == NOTE
    assert download.headers["content-type"].startswith("text/plain")
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["content-disposition"] == (
        "attachment; filename=\"Compte rendu.txt\"; filename*=UTF-8''Compte%20rendu.txt"
    )

    renamed = await client.patch(
        f"/v1/documents/{document['id']}",
        json={"title": "Réunion vélo", "keep_original": True},
        headers={"If-Match": str(detail["version"])},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Réunion vélo"
    assert renamed.json()["keep_original"] is True and renamed.json()["original_until"] is None
    stale = await client.patch(
        f"/v1/documents/{document['id']}",
        json={"title": "x"},
        headers={"If-Match": str(detail["version"])},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    back = await client.patch(f"/v1/documents/{document['id']}", json={"keep_original": False})
    assert back.json()["keep_original"] is False and back.json()["original_until"] is not None

    assert (await client.delete(f"/v1/documents/{document['id']}")).status_code == 204
    assert not stored.exists()
    gone = await client.get(f"/v1/documents/{document['id']}")
    assert gone.status_code == 404 and gone.json()["code"] == "document_not_found"

    # The audit trail has identifiers only: no title, no file name, no content.
    events = await _audit(database, document["id"])
    assert [e.action for e in events] == [
        "document.create",
        "document.process",
        "document.download",
        "document.update",
        "document.update",
        "document.delete",
    ]
    flat = " ".join(str(e.details) for e in events)
    assert "Compte" not in flat and "vélo" not in flat and "réunion" not in flat.lower()


def _ts(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value).timestamp()


@pytest.mark.parametrize(
    "data",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"<!DOCTYPE html><html><body>hi</body></html>",
        b"MZ\x90\x00\x03\x00\x00\x00 executable",
        b"PK\x03\x04 a zip archive",
        b"OggS\x00\x02 dictation is not uploaded here",
    ],
)
async def test_dangerous_unknown_and_audio_types_are_refused(
    client: AsyncClient, spaces: dict[str, str], doc_settings: Settings, data: bytes
) -> None:
    response = await upload(client, spaces["personal"], data, filename="photo.jpg")
    assert response.status_code == 415 and response.json()["code"] == "unsupported_type"
    assert _stored_files(doc_settings.documents_dir) == []


async def test_upload_limits_and_form_validation(
    app: FastAPI, client: AsyncClient, spaces: dict[str, str], doc_settings: Settings
) -> None:
    personal = spaces["personal"]
    too_big = await upload(client, personal, b"a" * (MAX_BYTES + 1))
    assert too_big.status_code == 413 and too_big.json()["code"] == "document_too_large"
    # Announced too large: refused before reading the body.
    announced = await client.post(
        "/v1/documents",
        content=b"x",
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(MAX_BYTES * 2),
        },
    )
    assert announced.status_code == 413
    assert (await upload(client, personal, b"a" * MAX_BYTES)).status_code == 201

    empty = await upload(client, personal, b"")
    assert empty.status_code == 422 and empty.json()["code"] == "document_empty"
    dictation = await upload(client, personal, NOTE, purpose="dictation")
    assert dictation.status_code == 422
    assert dictation.json()["errors"][0]["loc"] == ["body", "purpose"]
    no_file = await client.post("/v1/documents", data={"space_id": personal}, files={"x": b""})
    assert no_file.status_code == 422
    json_body = await client.post("/v1/documents", json={"space_id": personal})
    assert json_body.status_code == 415
    unknown_space = await upload(client, str(uuid.uuid4()), NOTE)
    assert unknown_space.status_code == 404 and unknown_space.json()["code"] == "space_not_found"

    # No key configured: uploads are refused, nothing is stored in clear.
    app.state.settings = doc_settings.model_copy(update={"documents_key": None})
    unavailable = await upload(client, personal, NOTE)
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "documents_unavailable"


async def test_idempotent_upload_is_bound_to_the_file(
    client: AsyncClient, spaces: dict[str, str], database: Database
) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = await upload(client, spaces["personal"], NOTE, headers=key)
    replay = await upload(client, spaces["personal"], NOTE, headers=key)
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    other_file = await upload(client, spaces["personal"], b"Autre contenu", headers=key)
    assert other_file.status_code == 409
    assert other_file.json()["code"] == "idempotency_key_reused"
    async with database.sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Document)) == 1


async def test_provenance_must_exist_and_share_the_space(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    personal, professional = spaces["personal"], spaces["professional"]
    task = (
        await client.post("/v1/tasks", json={"space_id": personal, "title": "Payer la facture"})
    ).json()

    other_space = await upload(client, professional, PDF, source_type="task", source_id=task["id"])
    assert other_space.status_code == 422
    assert other_space.json()["code"] == "source_space_mismatch"
    missing = await upload(
        client, personal, PDF, source_type="journal_entry", source_id=str(uuid.uuid4())
    )
    assert missing.status_code == 404 and missing.json()["code"] == "source_not_found"
    half = await upload(client, personal, PDF, source_type="task")
    assert half.status_code == 422
    unknown_type = await upload(
        client, personal, PDF, source_type="space", source_id=str(uuid.uuid4())
    )
    assert unknown_type.status_code == 422

    attached = await created(
        client, personal, PDF, purpose="attachment", source_type="task", source_id=task["id"]
    )
    assert (attached["source_type"], attached["source_id"]) == ("task", task["id"])
    assert attached["title"] == "note" and attached["kind"] == "pdf"
    by_source = await client.get(
        "/v1/documents", params={"source_type": "task", "source_id": task["id"]}
    )
    assert [d["id"] for d in by_source.json()["items"]] == [attached["id"]]


# --- Processing ----------------------------------------------------------------------------


async def test_images_are_ocrd_sanitized_and_classified_locally(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    doc_settings: Settings,
) -> None:
    sanitized = b"\x89PNG\r\n\x1a\n" + b"re-encoded pixels, no metadata"

    def handler(data: bytes, mime: str, purpose: str) -> Extraction:
        assert (data, mime, purpose) == (JPEG, "image/jpeg", "document")
        return Extraction(
            text=RIB + "\x00",
            source="ocr",
            pages=1,
            confidence=0.87654,
            sanitized=sanitized,
            sanitized_mime="image/png",
        )

    document = await created(client, spaces["personal"], JPEG, filename="rib.jpg")
    extractor = FakeExtractor(handler=handler)
    assert await process(database, store, extractor, document["id"], doc_settings) == "ready"

    detail = (await client.get(f"/v1/documents/{document['id']}")).json()
    assert detail["extracted_text"] == RIB  # NUL characters removed
    assert (detail["text_source"], detail["pages"], detail["text_confidence"]) == ("ocr", 1, 0.877)
    assert detail["sensitivity"] == ["bank"]
    assert "IBAN" in detail["analysis"]["sensitivity_reasons"]["bank"]
    # The sanitized image replaced the upload.
    assert (detail["mime"], detail["size_bytes"]) == ("image/png", len(sanitized))
    assert detail["version"] == document["version"] + 1
    download = await client.get(f"/v1/documents/{document['id']}/original")
    assert download.content == sanitized
    assert download.headers["content-disposition"].startswith('attachment; filename="rib.png"')
    async with database.sessionmaker() as session:
        row = await session.get(Document, uuid.UUID(document["id"]))
        assert row is not None and row.sha256 == hashlib.sha256(sanitized).hexdigest()


async def test_receipts_get_a_local_proposal(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    doc_settings: Settings,
) -> None:
    extractor = FakeExtractor()
    extractor.register(JPEG, RECEIPT)
    document = await created(client, spaces["personal"], JPEG, purpose="receipt")
    assert document["title"] == "note"
    assert await process(database, store, extractor, document["id"], doc_settings) == "ready"
    detail = (await client.get(f"/v1/documents/{document['id']}")).json()
    receipt = detail["analysis"]["receipt"]
    assert receipt["total"] == "3.45" and receipt["merchant"] == "CARREFOUR MARKET"
    assert receipt["tx_date"] == "2026-10-10" and receipt["card_payment"] is True
    assert detail["sensitivity"] == []  # a card number masked on a receipt is not a leak

    # A correction re-runs the local analyses; keys added by finance are kept.
    async with database.sessionmaker() as session, session.begin():
        row = await session.get(Document, uuid.UUID(document["id"]))
        assert row is not None and row.analysis is not None
        row.analysis = {**row.analysis, "receipt": {**receipt, "transaction_id": "t-1"}}
    corrected = await client.patch(
        f"/v1/documents/{document['id']}",
        json={"extracted_text": RECEIPT.replace("3,45", "4,45")},
    )
    receipt = corrected.json()["analysis"]["receipt"]
    assert receipt["total"] == "4.45" and receipt["transaction_id"] == "t-1"


async def test_correction_reclassifies_sensitivity(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    extractor: FakeExtractor,
    doc_settings: Settings,
) -> None:
    document = await created(client, spaces["personal"], NOTE)
    while_pending = await client.patch(
        f"/v1/documents/{document['id']}", json={"extracted_text": "x"}
    )
    assert while_pending.status_code == 409
    assert while_pending.json()["code"] == "document_processing"

    await process(database, store, extractor, document["id"], doc_settings)
    corrected = await client.patch(f"/v1/documents/{document['id']}", json={"extracted_text": RIB})
    assert corrected.status_code == 200, corrected.text
    body = corrected.json()
    assert body["sensitivity"] == ["bank"] and body["text_corrected_at"] is not None
    assert body["text_source"] == "text" and body["extracted_text"] == RIB
    back = await client.patch(
        f"/v1/documents/{document['id']}", json={"extracted_text": "Liste de courses"}
    )
    assert back.json()["sensitivity"] == []
    assert back.json()["analysis"]["sensitivity_reasons"] == {}


async def test_unavailable_extraction_is_retried_and_unreadable_documents_fail(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    doc_settings: Settings,
) -> None:
    def down(data: bytes, mime: str, purpose: str) -> Extraction:
        raise ExtractionError("ml service down")

    image = await created(client, spaces["personal"], PNG)
    extractor = FakeExtractor(handler=down)
    assert await process(database, store, extractor, image["id"], doc_settings) == "deferred"
    detail = (await client.get(f"/v1/documents/{image['id']}")).json()
    assert (detail["status"], detail["error"]) == ("pending", "extraction_unavailable")

    # The periodic job waits before retrying, then succeeds once the service is back.
    assert await processing.process_pending(database, store, extractor, settings=doc_settings) == {}
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(image["id"]))
            .values(updated_at=utcnow() - processing.RETRY_AFTER - timedelta(seconds=1))
        )
    text_doc = await created(client, spaces["personal"], NOTE)
    extractor.handler = None
    extractor.text = "Photo du tableau blanc"
    counts = await processing.process_pending(database, store, extractor, settings=doc_settings)
    assert counts == {"ready": 2}
    for document_id in (image["id"], text_doc["id"]):
        assert (await client.get(f"/v1/documents/{document_id}")).json()["status"] == "ready"

    # Without an extractor, only plain text is processed; images wait.
    later = await created(client, spaces["personal"], PDF)
    assert await process(database, store, None, later["id"], doc_settings) == "deferred"

    def broken(data: bytes, mime: str, purpose: str) -> Extraction:
        raise UnreadableDocument("too_many_pages")

    unreadable = await created(client, spaces["personal"], PDF + b" other")
    extractor.handler = broken
    assert await process(database, store, extractor, unreadable["id"], doc_settings) == "failed"
    detail = (await client.get(f"/v1/documents/{unreadable['id']}")).json()
    assert (detail["status"], detail["error"]) == ("failed", "too_many_pages")
    # More pages than allowed, even if the service did not refuse: failed too.
    extractor.handler = None
    extractor.pages = doc_settings.documents_max_pages + 1
    broker.flush_all()
    retried = await client.post(f"/v1/documents/{unreadable['id']}/reprocess")
    assert retried.status_code == 202 and retried.json()["status"] == "pending"
    assert _queued() == [f"process_document:{unreadable['id']}"]
    assert await process(database, store, extractor, unreadable["id"], doc_settings) == "failed"

    # The owner can type the text of an unreadable document himself.
    typed = await client.patch(
        f"/v1/documents/{unreadable['id']}", json={"extracted_text": "Contrat de location"}
    )
    assert typed.json()["status"] == "ready" and typed.json()["error"] is None


async def test_a_document_deleted_during_extraction_is_not_resurrected(
    client: AsyncClient,
    spaces: dict[str, str],
    logged_in: Owner,
    database: Database,
    store: DocumentStore,
    doc_settings: Settings,
) -> None:
    document = await created(client, spaces["personal"], PNG)
    stored = Path(doc_settings.documents_dir) / DocumentStore.key_for(
        logged_in.user.id, uuid.UUID(document["id"])
    )
    assert stored.exists()
    deleted: list[int] = []

    def slow(data: bytes, mime: str, purpose: str) -> Extraction:
        deleted.append(1)
        return Extraction(
            text="x", source="ocr", sanitized=PNG + b"clean", sanitized_mime="image/png"
        )

    class DeletingExtractor(FakeExtractor):
        async def extract(
            self, data: bytes, mime: str, *, purpose: str, max_pages: int
        ) -> Extraction:
            # The owner deletes the document while the OCR runs (no transaction is held).
            response = await client.delete(f"/v1/documents/{document['id']}")
            assert response.status_code == 204
            return slow(data, mime, purpose)

    outcome = await process(database, store, DeletingExtractor(), document["id"], doc_settings)
    assert outcome == "skipped" and deleted == [1]
    assert not stored.exists()


async def test_purge_during_extraction_and_stale_claims(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    doc_settings: Settings,
) -> None:
    document = await created(client, spaces["personal"], PNG)

    class PurgingExtractor(FakeExtractor):
        async def extract(
            self, data: bytes, mime: str, *, purpose: str, max_pages: int
        ) -> Extraction:
            # The retention purge runs while the OCR is in progress.
            async with database.sessionmaker() as session, session.begin():
                await session.execute(
                    update(Document)
                    .where(Document.id == uuid.UUID(document["id"]))
                    .values(original_until=utcnow() - timedelta(seconds=1))
                )
                assert await processing.purge_originals(session, store) == 1
            return Extraction(
                text="Tableau", source="ocr", sanitized=PNG + b"clean", sanitized_mime="image/png"
            )

    outcome = await process(database, store, PurgingExtractor(), document["id"], doc_settings)
    assert outcome == "ready"
    assert _stored_files(doc_settings.documents_dir) == []  # not written back
    detail = (await client.get(f"/v1/documents/{document['id']}")).json()
    assert detail["original_available"] is False and detail["mime"] == "image/png"
    assert detail["size_bytes"] == len(PNG)
    [event] = [e for e in await _audit(database, document["id"]) if e.action == "document.process"]
    assert event.details["sanitized"] is False

    # A document left "processing" by a crashed worker is claimed again, but only later.
    other = await created(client, spaces["personal"], NOTE)
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(other["id"]))
            .values(status="processing")
        )
    extractor = FakeExtractor()
    assert await process(database, store, extractor, other["id"], doc_settings) == "skipped"
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(other["id"]))
            .values(updated_at=utcnow() - processing.STALE_PROCESSING - timedelta(seconds=1))
        )
    counts = await processing.process_pending(database, store, extractor, settings=doc_settings)
    assert counts == {"ready": 1}


# --- Retention ------------------------------------------------------------------------------


async def test_originals_are_purged_after_retention(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    doc_settings: Settings,
) -> None:
    expiring = await created(client, spaces["personal"], NOTE)
    kept = await created(client, spaces["personal"], PDF, keep_original=True)
    assert kept["keep_original"] is True and kept["original_until"] is None
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == uuid.UUID(expiring["id"]))
            .values(original_until=utcnow() - timedelta(minutes=1))
        )
    assert len(_stored_files(doc_settings.documents_dir)) == 2

    async with database.sessionmaker() as session, session.begin():
        assert await processing.purge_originals(session, store) == 1
    async with database.sessionmaker() as session, session.begin():
        assert await processing.purge_originals(session, store) == 0
    assert [p.stem for p in _stored_files(doc_settings.documents_dir)] == [kept["id"]]

    detail = (await client.get(f"/v1/documents/{expiring['id']}")).json()
    assert detail["original_available"] is False and detail["keep_original"] is False
    download = await client.get(f"/v1/documents/{expiring['id']}/original")
    assert download.status_code == 410 and download.json()["code"] == "original_purged"
    # A purged original cannot be resurrected, nor processed again.
    keep = await client.patch(f"/v1/documents/{expiring['id']}", json={"keep_original": True})
    assert keep.status_code == 410
    again = await client.post(f"/v1/documents/{expiring['id']}/reprocess")
    assert again.status_code == 410
    # Still pending (never processed before the purge): it fails cleanly.
    extractor = FakeExtractor()
    assert await process(database, store, extractor, expiring["id"], doc_settings) == "failed"
    assert (await client.get(f"/v1/documents/{expiring['id']}")).json()["error"] == (
        "original_purged"
    )

    async with database.sessionmaker() as session:
        [event] = await session.scalars(
            select(AuditEvent).where(AuditEvent.action == "document.purge_originals")
        )
    assert event.details == {"count": 1} and event.target_id is None


async def test_unreferenced_files_are_swept(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    doc_settings: Settings,
) -> None:
    kept = await created(client, spaces["personal"], NOTE)
    root = Path(doc_settings.documents_dir)
    [kept_file] = _stored_files(root)
    orphan = kept_file.parent / f"{uuid.uuid4()}.bin"
    leftover = kept_file.parent / f"{uuid.uuid4()}.{uuid.uuid4().hex}.tmp"
    recent = kept_file.parent / f"{uuid.uuid4()}.bin"
    foreign = kept_file.parent / "README"
    for path in (orphan, leftover, recent, foreign):
        path.write_bytes(b"x")
    old = time.time() - 3 * 86400
    for path in (kept_file, orphan, leftover, foreign):
        os.utime(path, (old, old))

    async with database.sessionmaker() as session, session.begin():
        assert await processing.sweep_orphans(session, store, root) == 2
    assert kept_file.exists() and recent.exists() and foreign.exists()
    assert not orphan.exists() and not leftover.exists()
    assert (await client.get(f"/v1/documents/{kept['id']}/original")).content == NOTE


# --- Search, semantic index, isolation, export ------------------------------------------


async def test_documents_are_searchable_and_leave_no_vector_once_deleted(
    app: FastAPI,
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    doc_settings: Settings,
) -> None:
    embedder = FakeEmbedder()
    app.state.embedder = embedder
    extractor = FakeExtractor()
    scan = PDF + b" contrat"
    extractor.register(scan, "Contrat de location de l'appartement, loyer mensuel.")
    document = await created(client, spaces["personal"], scan, title="Bail")
    pending = await created(client, spaces["personal"], PNG)

    # Not indexed before its text is extracted.
    assert (await index_pending(database, embedder, batch=50)).get("document") is None
    await process(database, store, extractor, document["id"], doc_settings)
    assert (await index_pending(database, embedder, batch=50))["document"] == 1

    result = (
        await client.get("/v1/search", params={"q": "loyer appartement", "types": "documents"})
    ).json()
    assert result["semantic"] is True
    [hit] = result["hits"]
    assert (hit["type"], hit["id"], hit["title"]) == ("document", document["id"], "Bail")
    assert hit["match"] == "both" and "«loyer»" in hit["snippet"]
    everywhere = (await client.get("/v1/search", params={"q": "loyer"})).json()
    assert [h["id"] for h in everywhere["hits"]] == [document["id"]]
    other_space = await client.get(
        "/v1/search", params={"q": "loyer", "space_id": spaces["professional"]}
    )
    assert other_space.json()["hits"] == []
    assert pending["id"] not in {h["id"] for h in everywhere["hits"]}

    assert (await client.delete(f"/v1/documents/{document['id']}")).status_code == 204
    async with database.sessionmaker() as session:
        vectors = await session.scalar(
            select(func.count())
            .select_from(Embedding)
            .where(Embedding.object_id == uuid.UUID(document["id"]))
        )
    assert vectors == 0


async def test_space_isolation_and_agent_read_rights(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    db: AsyncSession,
    settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    assert "documents" in RESOURCES
    assert ACTIONS["document.write"].resource == "documents"
    assert ACTIONS["document.delete"].agent_level is None  # agents never delete documents

    personal, professional = uuid.UUID(spaces["personal"]), uuid.UUID(spaces["professional"])
    pro_doc = await created(client, str(professional), NOTE)
    listed = await client.get("/v1/documents", params={"space_id": str(personal)})
    assert listed.json()["items"] == []

    def agent(*grants: tuple[uuid.UUID, str, str]) -> Principal:
        return Principal(
            kind="agent",
            user_id=logged_in.user.id,
            actor_id="documents",
            agent="documents",
            space_ids=frozenset(g[0] for g in grants),
            grants=frozenset(grants),
        )

    # An agent with rights on the space, but not on documents, sees nothing.
    blind = await make_ctx(
        database,
        db,
        settings,
        logged_in.user,
        fake_llm,
        principal=agent((professional, "tasks", "read")),
    )
    assert (await service.list_documents(blind, space_id=None)).items == []
    with pytest.raises(Exception, match="document_not_found"):
        await service.get_document(blind, uuid.UUID(pro_doc["id"]))
    reader = await make_ctx(
        database,
        db,
        settings,
        logged_in.user,
        fake_llm,
        principal=agent((professional, "documents", "read")),
    )
    [item] = (await service.list_documents(reader, space_id=None)).items
    assert str(item.id) == pro_doc["id"]
    assert (await service.get_document(reader, item.id)).id == item.id


async def test_export_has_metadata_and_text_but_never_originals(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    store: DocumentStore,
    extractor: FakeExtractor,
    doc_settings: Settings,
) -> None:
    document = await created(client, spaces["personal"], NOTE)
    await process(database, store, extractor, document["id"], doc_settings)
    export = (await client.get("/v1/privacy/export")).json()
    [exported] = export["documents"]
    assert exported["id"] == document["id"] and exported["extracted_text"] == NOTE.decode()
    assert exported["original_available"] is True and "storage_key" not in exported
    assert export["transactions"] == [] and export["finance_categories"] == []


# --- Extraction clients ------------------------------------------------------------------


async def test_remote_extractor_contract(doc_settings: Settings) -> None:
    sanitized = b"\xff\xd8\xff\xe0 clean jpeg"
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = request.content
        if b"broken" in body:
            return httpx.Response(422, json={"code": "encrypted"})
        if b"overloaded" in body:
            return httpx.Response(503)
        if b"svg" in body:
            return httpx.Response(
                200,
                json={
                    "text": "x",
                    "source": "ocr",
                    "sanitized_b64": base64.b64encode(b"<svg/>").decode(),
                    "sanitized_mime": "image/jpeg",
                },
            )
        return httpx.Response(
            200,
            json={
                "text": "Bonjour",
                "source": "ocr",
                "pages": 1,
                "confidence": 1.4,
                "sanitized_b64": base64.b64encode(sanitized).decode(),
                "sanitized_mime": "image/jpeg",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    remote = RemoteExtractor(doc_settings, client=client)
    result = await remote.extract(JPEG, "image/jpeg", purpose="receipt", max_pages=30)
    assert result == Extraction(
        text="Bonjour",
        source="ocr",
        pages=1,
        confidence=1.0,
        sanitized=sanitized,
        sanitized_mime="image/jpeg",
    )
    [request] = seen
    assert str(request.url) == doc_settings.ml_url.rstrip("/") + "/extract"
    assert b'name="purpose"\r\n\r\nreceipt' in request.content
    assert b'name="max_pages"\r\n\r\n30' in request.content

    with pytest.raises(UnreadableDocument) as unreadable:
        await remote.extract(JPEG + b"broken", "image/jpeg", purpose="document", max_pages=30)
    assert unreadable.value.code == "encrypted"
    with pytest.raises(ExtractionError):
        await remote.extract(JPEG + b"overloaded", "image/jpeg", purpose="document", max_pages=3)
    with pytest.raises(ExtractionError):  # a "sanitized" file of another type
        await remote.extract(JPEG + b"svg", "image/jpeg", purpose="document", max_pages=3)
    # Plain text never leaves the process.
    text = await remote.extract(NOTE, "text/plain", purpose="document", max_pages=3)
    assert text.text == NOTE.decode() and len(seen) == 4
    await client.aclose()

    down = RemoteExtractor(
        doc_settings,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")))
        ),
    )
    with pytest.raises(ExtractionError):
        await down.extract(PDF, "application/pdf", purpose="document", max_pages=3)


def test_extractor_selection_and_download_names(doc_settings: Settings) -> None:
    assert (
        build_extractor(doc_settings.model_copy(update={"extraction_provider": "disabled"})) is None
    )
    assert build_extractor(doc_settings.model_copy(update={"extraction_provider": "local"})) is None
    assert isinstance(
        build_extractor(doc_settings.model_copy(update={"extraction_provider": "fake"})),
        FakeExtractor,
    )
    assert isinstance(
        build_extractor(doc_settings.model_copy(update={"extraction_provider": "remote"})),
        RemoteExtractor,
    )
    assert content_disposition('Re: "facture"; x\r\n/../é', "application/pdf") == (
        'attachment; filename="Re facture x..e.pdf"; '
        "filename*=UTF-8''Re%20facture%20x..%C3%A9.pdf"
    )
    assert content_disposition("...", "image/png").startswith('attachment; filename="document.png"')


def test_document_jobs_are_scheduled() -> None:
    from lifeos.workers.scheduler import build_scheduler

    jobs = {job.id: job for job in build_scheduler().get_jobs()}
    assert "process_pending_documents" in jobs and "purge_document_originals" in jobs
