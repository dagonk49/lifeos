"""Dramatiq actors. Run with: dramatiq lifeos.workers.actors"""

import dramatiq
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import get_settings
from lifeos.core.idempotency import purge_expired_keys
from lifeos.logging import configure_logging
from lifeos.modules.audit.service import purge_older_than
from lifeos.modules.chat.service import purge_expired_messages
from lifeos.modules.guardian.actions import expire_stale
from lifeos.modules.identity.service import purge_expired
from lifeos.modules.notifications.service import dispatch_reminders
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


@dramatiq.actor(queue_name="realtime", max_retries=5, time_limit=60_000)
async def send_reminders() -> None:
    await run_job("send_reminders", dispatch_reminders)
