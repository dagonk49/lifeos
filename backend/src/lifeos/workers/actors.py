"""Dramatiq actors. Run with: dramatiq lifeos.workers.actors"""

import uuid
from pathlib import Path

import dramatiq
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import get_settings
from lifeos.core.idempotency import purge_expired_keys
from lifeos.logging import configure_logging, get_logger
from lifeos.modules.audit.service import purge_older_than
from lifeos.modules.chat.service import purge_expired_messages
from lifeos.modules.connectors.service import purge_oauth_states
from lifeos.modules.documents import processing as documents
from lifeos.modules.guardian.actions import expire_stale
from lifeos.modules.guardian.gateway import release_stale_reservations
from lifeos.modules.identity.service import purge_expired
from lifeos.modules.notifications.service import dispatch_reminders
from lifeos.modules.retrieval.indexer import drop_other_models, index_pending
from lifeos.modules.reviews.service import run_scheduled
from lifeos.modules.voice import processing as voice
from lifeos.modules.voice.service import purge_dictations
from lifeos.workers.broker import broker  # noqa: F401  (must be set before actors)
from lifeos.workers.jobs import (
    database,
    document_store,
    embedder,
    extractor,
    llm,
    run_job,
    run_task,
    transcriber,
)

configure_logging(get_settings())
log = get_logger("lifeos.actors")


@dramatiq.actor(queue_name="maintenance", max_retries=3)
async def purge_auth_artifacts() -> None:
    await run_job("purge_auth_artifacts", purge_expired)


@dramatiq.actor(queue_name="maintenance", max_retries=3)
async def purge_connector_states() -> None:
    """Consents in progress (OAuth states) expired or used more than a day ago."""
    await run_job("purge_connector_states", purge_oauth_states)


@dramatiq.actor(queue_name="maintenance", max_retries=3)
async def purge_audit_events() -> None:
    days = get_settings().audit_retention_days

    async def job(session: AsyncSession) -> int:
        return await purge_older_than(session, days=days)

    await run_job("purge_audit_events", job)


@dramatiq.actor(queue_name="maintenance", max_retries=3)
async def purge_messages() -> None:
    """Raw conversations older than 90 days (Mémoire Premium)."""
    await run_job("purge_messages", purge_expired_messages)


@dramatiq.actor(queue_name="maintenance", max_retries=3)
async def purge_idempotency_keys() -> None:
    await run_job("purge_idempotency_keys", purge_expired_keys)


@dramatiq.actor(queue_name="maintenance", max_retries=3)
async def expire_approvals() -> None:
    await run_job("expire_approvals", expire_stale)


@dramatiq.actor(queue_name="maintenance", max_retries=3)
async def release_llm_reservations() -> None:
    """LLM quota reservations whose process died during the call (ADR 0010)."""
    ttl = get_settings().llm_reservation_ttl

    async def job(session: AsyncSession) -> int:
        return await release_stale_reservations(session, ttl=ttl)

    await run_job("release_llm_reservations", job)


@dramatiq.actor(queue_name="realtime", max_retries=5, time_limit=60_000)
async def send_reminders() -> None:
    await run_job("send_reminders", dispatch_reminders)


@dramatiq.actor(queue_name="maintenance", max_retries=2, time_limit=15 * 60_000)
async def scheduled_reviews(period: str) -> None:
    """Automatic reviews of the spaces that enabled them (coaching agent permissions)."""

    async def job(session: AsyncSession) -> dict[str, int]:
        return await run_scheduled(
            session, database=database(), settings=get_settings(), llm=llm(), period=period
        )

    await run_job(f"scheduled_reviews_{period}", job)


@dramatiq.actor(queue_name="maintenance", max_retries=1, time_limit=10 * 60_000)
async def index_embeddings() -> None:
    """Embeds new or changed objects (local model). No-op when embeddings are disabled."""
    current = embedder()
    if current is None:
        return

    async def job(session: AsyncSession) -> dict[str, int]:
        return {"dropped": await drop_other_models(session, current.model)}

    await run_job("drop_other_embedding_models", job)
    # Manages its own short transactions: none is held while the model computes.
    await run_task("index_embeddings", lambda: index_pending(database(), current, batch=64))


# --- Documents (F10) -----------------------------------------------------------------------
# Messages for the "documents" queue are also sent by the API through
# lifeos.workers.queue.enqueue (by actor name): keep these names stable.


@dramatiq.actor(queue_name="documents", max_retries=2, time_limit=15 * 60_000)
async def process_document(document_id: str) -> None:
    """Extracts and analyses one uploaded document (sent by the API after an upload)."""
    try:
        target = uuid.UUID(document_id)
    except ValueError:
        log.warning("invalid_document_id")
        return
    # Manages its own short transactions: none is held during the extraction.
    await run_task(
        "process_document",
        lambda: documents.process_document(
            database(), document_store(), extractor(), target, settings=get_settings()
        ),
    )


@dramatiq.actor(queue_name="documents", max_retries=0, time_limit=30 * 60_000)
async def process_pending_documents() -> None:
    """Safety net and retries: the oldest pending documents, a few at a time."""
    await run_task(
        "process_pending_documents",
        lambda: documents.process_pending(
            database(), document_store(), extractor(), settings=get_settings()
        ),
    )


@dramatiq.actor(queue_name="documents", max_retries=1, time_limit=30 * 60_000)
async def reclassify_documents() -> None:
    """Documents classified with older rules: classified again, locally (categories are
    only added). Nothing to do once every document is up to date."""
    await run_task("reclassify_documents", lambda: documents.reclassify_documents(database()))


@dramatiq.actor(queue_name="maintenance", max_retries=3, time_limit=30 * 60_000)
async def purge_document_originals() -> None:
    """Encrypted originals past their retention date, then unreferenced files."""
    store = document_store()

    async def purge(session: AsyncSession) -> int:
        return await documents.purge_originals(session, store)

    async def sweep(session: AsyncSession) -> int:
        return await documents.sweep_orphans(session, store, Path(get_settings().documents_dir))

    await run_job("purge_document_originals", purge)
    await run_job("sweep_document_files", sweep)


# --- Voice (UC01) --------------------------------------------------------------------------
# "transcribe_dictations" is also sent by the API (lifeos.workers.queue.enqueue).


@dramatiq.actor(queue_name="stt", max_retries=0, time_limit=60 * 60_000)
async def transcribe_dictations() -> None:
    """Waiting dictations, one at a time across workers (a concurrent run returns at once:
    at most one worker thread waits on the stt service)."""
    await run_task(
        "transcribe_dictations",
        lambda: voice.transcribe_pending(
            database(), document_store(), transcriber(), settings=get_settings()
        ),
    )


@dramatiq.actor(queue_name="maintenance", max_retries=3, time_limit=10 * 60_000)
async def purge_old_dictations() -> None:
    """Dictations nobody used within a day: row, transcript and any recording."""
    store = document_store()

    async def purge(session: AsyncSession) -> int:
        return await purge_dictations(session, store)

    await run_job("purge_dictations", purge)
