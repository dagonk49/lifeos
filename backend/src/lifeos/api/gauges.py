"""Operational gauges of the API process: dependencies, task queues, document backlog.

Refreshed in the background every LIFEOS_METRICS_REFRESH_SECONDS (main.py), so that a scrape
stays cheap and never waits on a dependency. Counts and ages only: no content, no
identifier (docs/exploitation.md §5). The alerts built on them are in
infra/prometheus/alerts.yml.
"""

import asyncio
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select, text

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.documents.models import DOCUMENT_PURPOSES, Document
from lifeos.observability.metrics import (
    DEPENDENCY_UP,
    DOCUMENTS_BACKLOG,
    DOCUMENTS_BACKLOG_OLDEST,
    QUEUE_DEAD_LETTERS,
    QUEUE_DELAYED,
    QUEUE_MESSAGES,
)
from lifeos.workers.broker import NAMESPACE
from lifeos.workers.queue import QUEUES

log = get_logger(__name__)
CHECK_TIMEOUT_S = 5.0
# A document in these states still waits for the worker (reading, OCR, transcription).
WAITING_STATUSES = ("pending", "processing")


async def database_error(database: Database) -> str | None:
    """None if the database answers, else the error type (never its message)."""
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S), database.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        return type(exc).__name__
    return None


async def redis_error(redis: Redis) -> str | None:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S):
            await redis.ping()
    except Exception as exc:
        return type(exc).__name__
    return None


async def service_error(client: httpx.AsyncClient, url: str) -> str | None:
    """An internal HTTP service (ml, stt): its /healthz must answer 200."""
    try:
        response = await client.get(f"{url.rstrip('/')}/healthz", timeout=CHECK_TIMEOUT_S)
    except httpx.HTTPError as exc:
        return type(exc).__name__
    return None if response.status_code == 200 else f"http_{response.status_code}"


def internal_services(settings: Settings) -> dict[str, str]:
    """The internal services this installation uses (a disabled one is not checked)."""
    services: dict[str, str] = {}
    if "remote" in (settings.embeddings_provider, settings.extraction_provider):
        services["ml"] = settings.ml_url
    if settings.stt_provider == "remote":
        services["stt"] = settings.stt_url
    return services


async def refresh_queues(redis: Redis) -> None:
    """Waiting messages, retries and dead letters of every task queue (Dramatiq keys)."""
    async with asyncio.timeout(CHECK_TIMEOUT_S), redis.pipeline(transaction=False) as pipe:
        for queue in QUEUES:
            pipe.llen(f"{NAMESPACE}:{queue}")
            pipe.llen(f"{NAMESPACE}:{queue}.DQ")
            pipe.zcard(f"{NAMESPACE}:{queue}.XQ")
        values = await pipe.execute()
    for index, queue in enumerate(QUEUES):
        waiting, delayed, dead = values[3 * index : 3 * index + 3]
        QUEUE_MESSAGES.labels(queue).set(waiting)
        QUEUE_DELAYED.labels(queue).set(delayed)
        QUEUE_DEAD_LETTERS.labels(queue).set(dead)


async def refresh_backlog(database: Database) -> None:
    """Documents (and dictations) not read yet, by purpose: how many, and the oldest's age."""
    query = (
        select(Document.purpose, func.count(), func.min(Document.created_at))
        .where(Document.status.in_(WAITING_STATUSES))
        .group_by(Document.purpose)
    )
    async with asyncio.timeout(CHECK_TIMEOUT_S), database.sessionmaker() as session:
        rows = (await session.execute(query)).all()
    now = utcnow()
    waiting = {purpose: (count, oldest) for purpose, count, oldest in rows}
    for purpose in DOCUMENT_PURPOSES:
        count, oldest = waiting.get(purpose, (0, None))
        DOCUMENTS_BACKLOG.labels(purpose).set(count)
        age = max((now - oldest).total_seconds(), 0.0) if oldest is not None else 0.0
        DOCUMENTS_BACKLOG_OLDEST.labels(purpose).set(age)


async def refresh(
    database: Database, redis: Redis, settings: Settings, client: httpx.AsyncClient
) -> None:
    """One refresh of every gauge. A dependency that fails is reported, never raised."""
    errors = {"database": await database_error(database), "redis": await redis_error(redis)}
    for name, url in internal_services(settings).items():
        errors[name] = await service_error(client, url)
    for name, error in errors.items():
        DEPENDENCY_UP.labels(name).set(0 if error else 1)
        if error:
            log.warning("dependency_down", dependency=name, error_type=error)
    if errors["redis"] is None:
        try:
            await refresh_queues(redis)
        except Exception as exc:
            log.warning("queue_gauges_failed", error_type=type(exc).__name__)
    if errors["database"] is None:
        try:
            await refresh_backlog(database)
        except Exception as exc:
            log.warning("backlog_gauges_failed", error_type=type(exc).__name__)


async def refresh_forever(state: Any, interval: float) -> None:
    """Background task of the API (main.py lifespan): refresh, then wait, until cancelled."""
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await refresh(state.db, state.redis, state.settings, client)
            except Exception as exc:  # never let the loop die: the gauges would go stale
                log.warning("gauges_refresh_failed", error_type=type(exc).__name__)
            await asyncio.sleep(interval)
