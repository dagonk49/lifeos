"""Job runtime: one database per worker process, one transaction per job run."""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import get_settings
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.llm import build_provider
from lifeos.modules.llm.base import LLMProvider
from lifeos.modules.retrieval.embedders import Embedder, build_embedder
from lifeos.observability.metrics import JOB_RUNS

log = get_logger("lifeos.jobs")
_db: Database | None = None
_llm: LLMProvider | None = None
_embedder: Embedder | None = None
_embedder_built = False


def llm() -> LLMProvider:
    global _llm
    if _llm is None:
        _llm = build_provider(get_settings())
    return _llm


def embedder() -> Embedder | None:
    global _embedder, _embedder_built
    if not _embedder_built:
        _embedder = build_embedder(get_settings())
        _embedder_built = True
    return _embedder


def database() -> Database:
    return _database()


def _database() -> Database:
    # Created lazily inside the worker's event loop thread (asyncpg is loop-bound).
    global _db
    if _db is None:
        _db = Database(get_settings().database_url, pool_size=2)
    return _db


async def run_job(name: str, fn: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    """Runs `fn` in its own transaction. Jobs must be idempotent: they may be replayed."""
    start = time.perf_counter()
    try:
        async with _database().sessionmaker() as session, session.begin():
            result = await fn(session)
    except Exception:
        JOB_RUNS.labels(name, "failure").inc()
        log.exception("job_failed", job=name)
        raise
    JOB_RUNS.labels(name, "success").inc()
    log.info("job_done", job=name, duration_ms=round((time.perf_counter() - start) * 1000, 1))
    return result
