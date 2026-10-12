"""Background transcription of dictations (UC01, ADR 0009).

One dictation at a time across all workers (a PostgreSQL advisory lock): transcription
takes the CPU, or the GPU, for up to a few minutes, and must not hold more than one worker
thread. Same claim pattern as the documents: no transaction is open while the recording is
decrypted and transcribed; the result is written only if the dictation still exists.

The recording is deleted as soon as the transcript is saved, or when it turns out to be
untranscribable: audio is never kept longer than needed.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import and_, func, or_, select, update

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.processing import clean_text
from lifeos.modules.documents.storage import DocumentStore, OriginalMissingError, StorageError
from lifeos.modules.voice.service import PURPOSE
from lifeos.modules.voice.transcriber import (
    Transcriber,
    TranscriptionError,
    UntranscribableAudio,
)

log = get_logger(__name__)

Outcome = Literal["ready", "failed", "deferred", "skipped"]
# Longer than the transcription timeout: a dictation left "processing" this long was
# abandoned (worker crash) and can be claimed again.
STALE_PROCESSING = timedelta(minutes=45)
# A dictation whose transcription was unavailable waits this long before the next attempt.
RETRY_AFTER = timedelta(minutes=2)
# Arbitrary constant: one transcription at a time.
TRANSCRIBE_LOCK = 0x4C49_4644_4943


@dataclass(frozen=True)
class _Job:
    id: uuid.UUID
    user_id: uuid.UUID
    space_id: uuid.UUID
    mime: str
    storage_key: str | None


async def _next(database: Database) -> uuid.UUID | None:
    now = utcnow()
    async with database.sessionmaker() as session:
        found: uuid.UUID | None = await session.scalar(
            select(Document.id)
            .where(
                Document.purpose == PURPOSE,
                or_(
                    and_(
                        Document.status == "pending",
                        or_(Document.error.is_(None), Document.updated_at < now - RETRY_AFTER),
                    ),
                    and_(
                        Document.status == "processing",
                        Document.updated_at < now - STALE_PROCESSING,
                    ),
                ),
            )
            .order_by(Document.created_at)
            .limit(1)
        )
    return found


async def _claim(database: Database, dictation_id: uuid.UUID) -> _Job | None:
    now = utcnow()
    async with database.sessionmaker() as session, session.begin():
        row = (
            await session.execute(
                update(Document)
                .where(
                    Document.id == dictation_id,
                    Document.purpose == PURPOSE,
                    or_(
                        Document.status == "pending",
                        and_(
                            Document.status == "processing",
                            Document.updated_at < now - STALE_PROCESSING,
                        ),
                    ),
                )
                .values(status="processing", updated_at=now)
                .returning(
                    Document.id,
                    Document.user_id,
                    Document.space_id,
                    Document.mime,
                    Document.storage_key,
                )
                .execution_options(synchronize_session=False)
            )
        ).one_or_none()
    return _Job(*row) if row is not None else None


async def _finish(
    database: Database,
    store: DocumentStore,
    job: _Job,
    *,
    status: Literal["ready", "failed"],
    text: str | None = None,
    duration: float | None = None,
    error: str | None = None,
) -> Outcome:
    now = utcnow()
    async with database.sessionmaker() as session, session.begin():
        document = await session.scalar(
            select(Document)
            .where(Document.id == job.id, Document.status == "processing")
            .with_for_update()
        )
        if document is None:  # deleted meanwhile
            return "skipped"
        storage_key = document.storage_key
        document.status = status
        document.error = error
        document.extracted_text = text
        document.text_source = "speech" if text is not None else None
        document.analysis = {"duration": round(duration, 2)} if duration is not None else None
        document.processed_at = now
        # The recording is not needed any more, whatever the outcome.
        document.storage_key = None
        document.original_until = None
        await session.flush()
        await audit.record(
            session,
            action="dictation.transcribe",
            outcome="success" if status == "ready" else "failure",
            actor_type="system",
            actor_id="voice",
            user_id=job.user_id,
            space_id=job.space_id,
            target_type="document",
            target_id=job.id,
            details={
                "status": status,
                **({"reason": error} if error else {}),
                **({"seconds": round(duration)} if duration is not None else {}),
            },
        )
    # After the commit: if the deletion fails, the orphan sweep removes the file.
    if storage_key is not None:
        try:
            await asyncio.to_thread(store.delete, storage_key)
        except (StorageError, OSError) as exc:
            log.error("dictation_file_delete_failed", error=type(exc).__name__)
    log.info("dictation_" + status, dictation=str(job.id), reason=error)
    return status


async def _release(database: Database, job: _Job, *, error: str) -> Outcome:
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == job.id, Document.status == "processing")
            .values(status="pending", error=error, updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
    log.info("dictation_deferred", dictation=str(job.id), reason=error)
    return "deferred"


async def _transcribe(
    database: Database,
    store: DocumentStore,
    transcriber: Transcriber,
    job: _Job,
    settings: Settings,
) -> Outcome:
    if job.storage_key is None:
        return await _finish(database, store, job, status="failed", error="original_purged")
    if not store.available:
        return await _release(database, job, error="documents_unavailable")
    try:
        data = await asyncio.to_thread(store.read, job.storage_key, job.id)
    except OriginalMissingError:
        return await _finish(database, store, job, status="failed", error="original_purged")
    except StorageError as exc:
        log.error("dictation_unreadable", dictation=str(job.id), error=str(exc))
        return await _finish(database, store, job, status="failed", error="original_unreadable")
    try:
        result = await transcriber.transcribe(
            data, job.mime, max_seconds=settings.voice_max_seconds
        )
    except TranscriptionError as exc:
        log.warning("transcription_unavailable", dictation=str(job.id), error=str(exc)[:200])
        return await _release(database, job, error="transcription_unavailable")
    except UntranscribableAudio as exc:
        return await _finish(database, store, job, status="failed", error=exc.code)
    return await _finish(
        database,
        store,
        job,
        status="ready",
        text=clean_text(result.text),
        duration=result.duration,
    )


async def transcribe_dictation(
    database: Database,
    store: DocumentStore,
    transcriber: Transcriber,
    dictation_id: uuid.UUID,
    *,
    settings: Settings,
) -> Outcome:
    job = await _claim(database, dictation_id)
    if job is None:
        return "skipped"
    try:
        return await _transcribe(database, store, transcriber, job, settings)
    except Exception:
        # A bug must not make the periodic job retry the same dictation forever.
        log.exception("dictation_processing_error", dictation=str(job.id))
        return await _finish(database, store, job, status="failed", error="processing_error")


async def transcribe_pending(
    database: Database,
    store: DocumentStore,
    transcriber: Transcriber | None,
    *,
    settings: Settings,
    batch: int = 10,
) -> dict[str, int]:
    """Transcribes the waiting dictations, oldest first, one at a time; a concurrent run
    returns immediately (the running one picks up what was added meanwhile)."""
    counts: dict[str, int] = {}
    if transcriber is None:
        return counts
    async with database.engine.connect() as guard:
        guard = await guard.execution_options(isolation_level="AUTOCOMMIT")
        if not await guard.scalar(select(func.pg_try_advisory_lock(TRANSCRIBE_LOCK))):
            return counts
        try:
            for _ in range(batch):
                dictation_id = await _next(database)
                if dictation_id is None:
                    break
                outcome = await transcribe_dictation(
                    database, store, transcriber, dictation_id, settings=settings
                )
                counts[outcome] = counts.get(outcome, 0) + 1
                if outcome == "deferred":
                    break  # transcription or storage unavailable: the others would wait too
        finally:
            await guard.execute(select(func.pg_advisory_unlock(TRANSCRIBE_LOCK)))
    return counts
