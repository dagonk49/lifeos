"""Background processing of documents (F10): local extraction, sensitivity detection,
receipt parsing, and retention of the encrypted originals.

Everything runs on the server: the original is decrypted in the worker and, for images
and PDFs, read by the internal ml service. Nothing here calls the cloud.

No transaction is open while a document is decrypted and extracted (OCR can take
minutes), same pattern as the semantic indexer: the document is claimed atomically
(pending -> processing), extracted, then re-read under a row lock and written only if it
is still being processed. A concurrent deletion never waits for an extraction.
"""

import asyncio
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.documents.extraction import (
    Extraction,
    ExtractionCrashError,
    ExtractionError,
    Extractor,
    UnreadableDocument,
    decode_text,
)
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.schemas import MAX_TEXT_CHARS
from lifeos.modules.documents.sensitivity import classify
from lifeos.modules.documents.sniff import sniff
from lifeos.modules.documents.storage import DocumentStore, OriginalMissingError, StorageError
from lifeos.modules.finance.receipts import parse_receipt
from lifeos.modules.identity.models import User

log = get_logger(__name__)

Outcome = Literal["ready", "failed", "deferred", "skipped"]

# Audio documents belong to the voice module (transcription).
PROCESSED_KINDS = ("image", "pdf", "text")
# A document left "processing" longer than this (worker crash) can be claimed again.
STALE_PROCESSING = timedelta(minutes=30)
# A document whose extraction was unavailable waits this long before the next attempt
# by the periodic job (no retry storm while the ml service is down).
RETRY_AFTER = timedelta(minutes=5)
# A document on which the reading service failed this many times in a row fails for good
# (the owner can still type its text, or ask for a new reading).
MAX_FAILURES = 3
# Files on the volume that no document references (failed transaction, deleted space)
# are removed once older than this.
ORPHAN_AGE = timedelta(days=1)
# Arbitrary constant: one batch of pending documents at a time.
PROCESS_LOCK = 0x4C49_4644_4F43
_STORED_FILE = re.compile(
    r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}/"
    r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.(?:bin|[0-9a-f]{32}\.tmp)"
)


def clean_text(text: str) -> str:
    # PostgreSQL text cannot hold NUL characters.
    return text.replace("\x00", "").strip()[:MAX_TEXT_CHARS]


def owner_today(timezone: str | None) -> date:
    return utcnow().astimezone(ZoneInfo(timezone or "UTC")).date()


def analyse(
    text: str,
    purpose: str,
    today: date,
    previous: dict[str, Any] | None,
    *,
    title: str | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Local, deterministic analysis of a document's text: sensitivity categories and
    reasons (the title counts too: « Ordonnance Dr X »), and the receipt proposal for a
    receipt (stored, never applied)."""
    found = classify(f"{title}\n{text}" if title else text)
    analysis: dict[str, Any] = dict(previous or {})
    analysis["sensitivity_reasons"] = found.reasons
    if purpose == "receipt":
        proposal = parse_receipt(text, today).as_dict()
        earlier = analysis.get("receipt")
        # Keys added by the finance module (e.g. the transaction created) are kept.
        analysis["receipt"] = {**earlier, **proposal} if isinstance(earlier, dict) else proposal
    return found.categories, analysis


@dataclass(frozen=True)
class _Job:
    id: uuid.UUID
    user_id: uuid.UUID
    space_id: uuid.UUID
    kind: str
    mime: str
    purpose: str
    storage_key: str | None


def _claimable(now: datetime) -> ColumnElement[bool]:
    return and_(
        Document.kind.in_(PROCESSED_KINDS),
        or_(
            Document.status == "pending",
            and_(Document.status == "processing", Document.updated_at < now - STALE_PROCESSING),
        ),
    )


async def _claim(database: Database, document_id: uuid.UUID) -> _Job | None:
    now = utcnow()
    async with database.sessionmaker() as session, session.begin():
        row = (
            await session.execute(
                update(Document)
                .where(Document.id == document_id, _claimable(now))
                .values(status="processing", updated_at=now)
                .returning(
                    Document.id,
                    Document.user_id,
                    Document.space_id,
                    Document.kind,
                    Document.mime,
                    Document.purpose,
                    Document.storage_key,
                )
                .execution_options(synchronize_session=False)
            )
        ).one_or_none()
    return _Job(*row) if row is not None else None


async def _audit(session: AsyncSession, job: _Job, outcome: str, **details: Any) -> None:
    await audit.record(
        session,
        action="document.process",
        outcome="success" if outcome == "ready" else "failure",
        actor_type="system",
        actor_id="documents",
        user_id=job.user_id,
        space_id=job.space_id,
        target_type="document",
        target_id=job.id,
        details={"status": outcome, **details},
    )


async def _release(database: Database, job: _Job, *, error: str) -> Outcome:
    """Back to pending: retried later (the periodic job waits RETRY_AFTER)."""
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == job.id, Document.status == "processing")
            .values(status="pending", error=error, updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
    log.info("document_deferred", document=str(job.id), reason=error)
    return "deferred"


async def _fail(
    database: Database,
    job: _Job,
    *,
    error: str,
    drop_original: DocumentStore | None = None,
) -> Outcome:
    """Failed for good. With `drop_original`, the stored file is deleted too: a picture
    that could not be read could not be cleaned of its metadata (GPS...) either."""
    now = utcnow()
    values: dict[str, Any] = {
        "status": "failed",
        "error": error,
        "processed_at": now,
        "updated_at": now,
        "version": Document.version + 1,
    }
    if drop_original is not None:
        values.update(storage_key=None, original_until=None)
    async with database.sessionmaker() as session, session.begin():
        result = await session.execute(
            update(Document)
            .where(Document.id == job.id, Document.status == "processing")
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        failed = bool(result.rowcount)  # type: ignore[attr-defined]
        if failed:
            await _audit(session, job, "failed", reason=error)
    if failed and drop_original is not None and job.storage_key is not None:
        # After the commit: if this fails, the orphan sweep removes the file.
        try:
            await asyncio.to_thread(drop_original.delete, job.storage_key)
        except (StorageError, OSError) as exc:
            log.error("document_file_delete_failed", error=type(exc).__name__)
    log.info("document_failed", document=str(job.id), reason=error)
    return "failed"


async def _crashed(database: Database, store: DocumentStore, job: _Job, *, error: str) -> Outcome:
    """The service failed on this document: retried later, failed after MAX_FAILURES."""
    async with database.sessionmaker() as session, session.begin():
        failures = await session.scalar(
            update(Document)
            .where(Document.id == job.id, Document.status == "processing")
            .values(failures=Document.failures + 1)
            .returning(Document.failures)
            .execution_options(synchronize_session=False)
        )
    if failures is None:
        return "skipped"
    if failures >= MAX_FAILURES:
        drop = store if job.kind == "image" else None
        return await _fail(database, job, error=error, drop_original=drop)
    return await _release(database, job, error="extraction_unavailable")


def _replacement(job: _Job, extraction: Extraction) -> tuple[bytes, str] | None:
    """The sanitized image to store instead of the upload, if it is a valid image."""
    if extraction.sanitized is None:
        return None
    detected = sniff(extraction.sanitized)
    if (
        job.kind != "image"
        or detected is None
        or detected.kind != "image"
        or detected.mime != extraction.sanitized_mime
    ):
        log.warning("sanitized_image_ignored", document=str(job.id))
        return None
    return extraction.sanitized, detected.mime


def _confidence(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(min(1.0, max(0.0, value)), 3)))


async def _save(
    database: Database, store: DocumentStore, job: _Job, extraction: Extraction
) -> Outcome:
    text = clean_text(extraction.text)
    replacement = _replacement(job, extraction)
    async with database.sessionmaker() as session, session.begin():
        # Still there and still ours? Locked until commit: a deletion waits for us.
        document = await session.scalar(
            select(Document)
            .where(Document.id == job.id, Document.status == "processing")
            .with_for_update()
        )
        if document is None:
            return "skipped"
        timezone = await session.scalar(select(User.timezone).where(User.id == document.user_id))
        replaced = False
        # Not when the original was purged meanwhile: it would be written back.
        if replacement is not None and document.storage_key is not None:
            data, mime = replacement
            await asyncio.to_thread(store.write, document.storage_key, document.id, data)
            document.mime = mime
            document.size_bytes = len(data)
            document.sha256 = hashlib.sha256(data).hexdigest()
            replaced = True
        categories, analysis = analyse(
            text,
            document.purpose,
            owner_today(timezone),
            document.analysis,
            title=document.title,
        )
        document.extracted_text = text
        document.text_source = extraction.source
        document.text_confidence = _confidence(extraction.confidence)
        document.text_corrected_at = None
        document.pages = extraction.pages
        document.sensitivity = categories
        document.analysis = analysis
        document.status = "ready"
        document.error = None
        document.failures = 0
        document.processed_at = utcnow()
        await session.flush()
        await _audit(
            session,
            job,
            "ready",
            source=extraction.source,
            sensitivity=",".join(categories),
            sanitized=replaced,
        )
    return "ready"


async def _process(
    database: Database,
    store: DocumentStore,
    extractor: Extractor | None,
    job: _Job,
    settings: Settings,
) -> Outcome:
    if job.storage_key is None:
        return await _fail(database, job, error="original_purged")
    if not store.available:
        return await _release(database, job, error="documents_unavailable")
    try:
        data = await asyncio.to_thread(store.read, job.storage_key, job.id)
    except OriginalMissingError:
        return await _fail(database, job, error="original_purged")
    except StorageError as exc:
        log.error("original_unreadable", document=str(job.id), error=str(exc))
        return await _fail(database, job, error="original_unreadable")
    max_pages = settings.documents_max_pages
    if job.kind == "text":
        extraction = decode_text(data)
    elif extractor is None:
        return await _release(database, job, error="extraction_unavailable")
    else:
        try:
            extraction = await extractor.extract(
                data, job.mime, purpose=job.purpose, max_pages=max_pages
            )
        except ExtractionCrashError as exc:
            log.warning("extraction_crashed", document=str(job.id), error=str(exc)[:200])
            return await _crashed(database, store, job, error="extraction_crashed")
        except ExtractionError as exc:
            log.warning("extraction_unavailable", document=str(job.id), error=str(exc)[:200])
            return await _release(database, job, error="extraction_unavailable")
        except UnreadableDocument as exc:
            drop = store if job.kind == "image" else None
            return await _fail(database, job, error=exc.code, drop_original=drop)
    if extraction.pages is not None and extraction.pages > max_pages:
        return await _fail(database, job, error="too_many_pages")
    return await _save(database, store, job, extraction)


async def process_document(
    database: Database,
    store: DocumentStore,
    extractor: Extractor | None,
    document_id: uuid.UUID,
    *,
    settings: Settings,
) -> Outcome:
    """Extracts and analyses one pending document. Idempotent: a document that is not
    pending (already processed, being processed, deleted) is skipped."""
    job = await _claim(database, document_id)
    if job is None:
        return "skipped"
    try:
        return await _process(database, store, extractor, job, settings)
    except Exception:
        # A bug must not make the periodic job retry the same document forever.
        log.exception("document_processing_error", document=str(job.id))
        return await _fail(database, job, error="processing_error")


async def process_pending(
    database: Database,
    store: DocumentStore,
    extractor: Extractor | None,
    *,
    settings: Settings,
    batch: int = 5,
) -> dict[str, int]:
    """Processes the oldest pending documents (safety net for lost queue messages and
    retries); a concurrent run returns immediately."""
    counts: dict[str, int] = {}
    async with database.engine.connect() as guard:
        guard = await guard.execution_options(isolation_level="AUTOCOMMIT")
        if not await guard.scalar(select(func.pg_try_advisory_lock(PROCESS_LOCK))):
            return counts
        try:
            now = utcnow()
            # Without an extractor, only plain text documents can be processed.
            kinds = PROCESSED_KINDS if extractor is not None else ("text",)
            stmt = (
                select(Document.id)
                .where(
                    Document.kind.in_(kinds),
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
                .limit(batch)
            )
            async with database.sessionmaker() as session:
                ids = list(await session.scalars(stmt))
            for document_id in ids:
                outcome = await process_document(
                    database, store, extractor, document_id, settings=settings
                )
                counts[outcome] = counts.get(outcome, 0) + 1
                if outcome == "deferred":
                    break  # extraction or storage unavailable: the others would fail too
        finally:
            await guard.execute(select(func.pg_advisory_unlock(PROCESS_LOCK)))
    return counts


# --- Retention ------------------------------------------------------------------------------


async def purge_originals(
    session: AsyncSession, store: DocumentStore, *, now: datetime | None = None, batch: int = 500
) -> int:
    """Deletes the encrypted originals whose retention period is over. The documents
    (metadata, extracted text) stay; the audit keeps only the count."""
    now = now or utcnow()
    purged = 0
    while True:
        documents = list(
            await session.scalars(
                select(Document)
                .where(Document.storage_key.is_not(None), Document.original_until <= now)
                .order_by(Document.original_until)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        )
        for document in documents:
            assert document.storage_key is not None
            await asyncio.to_thread(store.delete, document.storage_key)
            document.storage_key = None
        await session.flush()
        purged += len(documents)
        if len(documents) < batch:
            break
    if purged:
        await audit.record(
            session,
            action="document.purge_originals",
            actor_type="system",
            actor_id="documents",
            details={"count": purged},
        )
    return purged


def _old_files(root: Path, before: float) -> list[str]:
    if not root.is_dir():
        return []
    keys = []
    for path in root.glob("*/*"):
        key = path.relative_to(root).as_posix()
        if _STORED_FILE.fullmatch(key) and path.is_file() and path.stat().st_mtime < before:
            keys.append(key)
    return keys


async def sweep_orphans(
    session: AsyncSession, store: DocumentStore, root: Path, *, now: datetime | None = None
) -> int:
    """Removes stored files that no document references (a failed upload transaction, a
    deleted space or account) and stale temporary files."""
    before = ((now or utcnow()) - ORPHAN_AGE).timestamp()
    keys = await asyncio.to_thread(_old_files, root, before)
    removed = 0
    for start in range(0, len(keys), 500):
        chunk = keys[start : start + 500]
        referenced = set(
            await session.scalars(
                select(Document.storage_key).where(Document.storage_key.in_(chunk))
            )
        )
        for key in chunk:
            if key not in referenced:
                await asyncio.to_thread(store.delete, key)
                removed += 1
    return removed
