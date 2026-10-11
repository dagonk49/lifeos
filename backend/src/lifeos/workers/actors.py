"""Dramatiq actors. Run with: dramatiq lifeos.workers.actors"""

import dramatiq
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import get_settings
from lifeos.logging import configure_logging
from lifeos.modules.audit.service import purge_older_than
from lifeos.modules.identity.service import purge_expired
from lifeos.workers.broker import broker  # noqa: F401  (must be set before actors)
from lifeos.workers.jobs import run_job

configure_logging(get_settings())


@dramatiq.actor(queue_name="maintenance", max_retries=3)
async def purge_auth_artifacts() -> None:
    await run_job("purge_auth_artifacts", purge_expired)


@dramatiq.actor(queue_name="maintenance", max_retries=3)
async def purge_audit_events() -> None:
    days = get_settings().audit_retention_days

    async def job(session: AsyncSession) -> int:
        return await purge_older_than(session, days=days)

    await run_job("purge_audit_events", job)
