"""Dictations (UC01): a recording kept encrypted only until it is transcribed locally.

A dictation is a document with the purpose "dictation": its audio goes through the same
encrypted storage as any upload, and is deleted as soon as the transcript exists. The
dictation itself is transient: the client deletes it once the owner used the text, and
any dictation left over is erased after DICTATION_TTL. Dictations never appear in the
documents list, the search or the semantic index.
"""

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.sniff import Detected
from lifeos.modules.documents.storage import DocumentStore, StorageError
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.voice.schemas import DictationOut

log = get_logger(__name__)

PURPOSE = "dictation"
# A dictation (and its recording, if never transcribed) is erased after this long.
DICTATION_TTL = timedelta(hours=24)


def out(document: Document) -> DictationOut:
    analysis = document.analysis or {}
    duration = analysis.get("duration")
    return DictationOut(
        id=document.id,
        space_id=document.space_id,
        status=document.status,
        error=document.error,
        text=document.extracted_text if document.status == "ready" else None,
        duration=float(duration) if isinstance(duration, int | float) else None,
        expires_at=document.created_at + DICTATION_TTL,
        created_at=document.created_at,
    )


async def _audit(ctx: Ctx, action: str, document: Document, **details: Any) -> None:
    # Identifiers and sizes only: never audio nor text.
    await audit.record(
        ctx.db,
        action=action,
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=document.space_id,
        target_type="document",
        target_id=document.id,
        details=details,
    )


async def create_dictation(
    ctx: Ctx, store: DocumentStore, space_id: uuid.UUID, *, data: bytes, detected: Detected
) -> Document:
    ctx.principal.require_space(space_id)
    await authorize(ctx, "document.write", space_id)
    document_id = uuid.uuid4()
    now = utcnow()
    document = Document(
        id=document_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        title="Dictée",
        kind="audio",
        mime=detected.mime,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        purpose=PURPOSE,
        status="pending",
        sensitivity=[],
        storage_key=DocumentStore.key_for(ctx.principal.user_id, document_id),
        # The retention purge removes a recording that was never transcribed.
        original_until=now + DICTATION_TTL,
        created_at=now,
    )
    ctx.db.add(document)
    await ctx.db.flush()
    assert document.storage_key is not None
    try:
        await asyncio.to_thread(store.write, document.storage_key, document.id, data)
    except (StorageError, OSError) as exc:
        log.error("dictation_store_failed", error=type(exc).__name__)
        raise ProblemError(503, "documents_unavailable", "Document storage is unavailable") from exc
    await _audit(ctx, "dictation.create", document, mime=document.mime, size_bytes=len(data))
    return document


async def get_dictation(ctx: Ctx, dictation_id: uuid.UUID, *, for_update: bool = False) -> Document:
    stmt = select(Document).where(
        Document.id == dictation_id,
        Document.purpose == PURPOSE,
        Document.user_id == ctx.principal.user_id,
        Document.space_id.in_(ctx.principal.space_ids),
    )
    if for_update:
        stmt = stmt.with_for_update()
    document = await ctx.db.scalar(stmt)
    if document is None:
        raise not_found("dictation_not_found", "Dictation not found")
    return document


async def delete_dictation(ctx: Ctx, store: DocumentStore, dictation_id: uuid.UUID) -> None:
    document = await get_dictation(ctx, dictation_id, for_update=True)
    await authorize(ctx, "document.delete", document.space_id)
    storage_key = document.storage_key
    await ctx.db.delete(document)
    await ctx.db.flush()
    if storage_key is not None:
        # A failure leaves an orphan file, removed by the daily sweep.
        try:
            await asyncio.to_thread(store.delete, storage_key)
        except (StorageError, OSError) as exc:
            log.error("dictation_file_delete_failed", error=type(exc).__name__)
    await _audit(ctx, "dictation.delete", document)


async def purge_dictations(
    session: AsyncSession, store: DocumentStore, *, now: datetime | None = None, batch: int = 500
) -> int:
    """Erases the dictations older than DICTATION_TTL (row, transcript and any audio)."""
    limit = (now or utcnow()) - DICTATION_TTL
    purged = 0
    while True:
        documents = list(
            await session.scalars(
                select(Document)
                .where(Document.purpose == PURPOSE, Document.created_at < limit)
                .order_by(Document.created_at)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        )
        for document in documents:
            if document.storage_key is not None:
                await asyncio.to_thread(store.delete, document.storage_key)
            await session.delete(document)
        await session.flush()
        purged += len(documents)
        if len(documents) < batch:
            break
    if purged:
        await audit.record(
            session,
            action="dictation.purge",
            actor_type="system",
            actor_id="voice",
            details={"count": purged},
        )
    return purged
