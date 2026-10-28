"""Scheduler process: only enqueues Dramatiq messages on cron triggers (ADR 0004).

Run a single instance. Missed runs (downtime) are coalesced and caught up once.
"""

from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from lifeos.config import get_settings
from lifeos.logging import get_logger

log = get_logger("lifeos.scheduler")


def build_scheduler() -> BlockingScheduler:
    from lifeos.workers import actors

    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    scheduler = BlockingScheduler(
        timezone=tz,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 6 * 3600},
    )
    if settings.connectors_provider != "disabled":
        scheduler.add_job(
            actors.sync_calendars.send,
            IntervalTrigger(minutes=5, timezone=tz),
            id="sync_calendars",
            # A sync running late is not replayed: the next one reads everything again.
            misfire_grace_time=60,
        )
        # Approved calendar writes whose outcome is unknown (ADR 0016).
        scheduler.add_job(
            actors.reconcile_external_operations.send,
            IntervalTrigger(minutes=5, timezone=tz),
            id="reconcile_external_operations",
            misfire_grace_time=300,
        )
        scheduler.add_job(
            actors.sync_mail.send,
            IntervalTrigger(minutes=5, timezone=tz),
            id="sync_mail",
            misfire_grace_time=60,
        )
    scheduler.add_job(
        actors.purge_auth_artifacts.send,
        CronTrigger(hour=3, minute=10, timezone=tz),
        id="purge_auth_artifacts",
    )
    scheduler.add_job(
        actors.purge_connector_states.send,
        CronTrigger(hour=3, minute=15, timezone=tz),
        id="purge_connector_states",
    )
    scheduler.add_job(
        actors.purge_audit_events.send,
        CronTrigger(hour=3, minute=40, timezone=tz),
        id="purge_audit_events",
    )
    scheduler.add_job(
        actors.purge_decided_items.send,
        CronTrigger(hour=3, minute=15, timezone=tz),
        id="purge_decided_items",
    )
    scheduler.add_job(
        actors.purge_messages.send,
        CronTrigger(hour=3, minute=20, timezone=tz),
        id="purge_messages",
    )
    scheduler.add_job(
        actors.purge_mail.send,
        CronTrigger(hour=3, minute=25, timezone=tz),
        id="purge_mail",
    )
    scheduler.add_job(
        actors.purge_idempotency_keys.send,
        CronTrigger(hour=3, minute=30, timezone=tz),
        id="purge_idempotency_keys",
    )
    # CSV import previews expire after `finance_import_preview_hours`: swept every hour.
    scheduler.add_job(
        actors.purge_finance_imports.send,
        IntervalTrigger(hours=1, timezone=tz),
        id="purge_finance_imports",
    )
    scheduler.add_job(
        actors.expire_approvals.send, CronTrigger(minute=5, timezone=tz), id="expire_approvals"
    )
    # Abandoned LLM quota reservations: also released before each new reservation; this
    # keeps the usage panel right when no call follows.
    scheduler.add_job(
        actors.release_llm_reservations.send,
        IntervalTrigger(minutes=5, timezone=tz),
        id="release_llm_reservations",
        misfire_grace_time=300,
    )
    # Supervision: proves the scheduler -> Redis -> worker chain runs (alert
    # TachesPlanifieesBloquees, docs/exploitation.md §5).
    scheduler.add_job(
        actors.heartbeat.send,
        IntervalTrigger(minutes=1, timezone=tz),
        id="heartbeat",
        misfire_grace_time=30,
    )
    # Reminders are "real time": checked every minute, never coalesced into the past.
    scheduler.add_job(
        actors.send_reminders.send,
        IntervalTrigger(minutes=1, timezone=tz),
        id="send_reminders",
        misfire_grace_time=60,
    )
    # Suggestions become due at the owner's digest times (18:00 local by default): grouped
    # within the minute.
    scheduler.add_job(
        actors.build_suggestion_digests.send,
        IntervalTrigger(minutes=1, timezone=tz),
        id="build_suggestion_digests",
        misfire_grace_time=60,
    )
    scheduler.add_job(
        actors.index_embeddings.send,
        IntervalTrigger(minutes=2, timezone=tz),
        id="index_embeddings",
        misfire_grace_time=120,
    )
    # Documents: pending extractions (lost messages, retries) and original retention.
    scheduler.add_job(
        actors.process_pending_documents.send,
        IntervalTrigger(minutes=1, timezone=tz),
        id="process_pending_documents",
        misfire_grace_time=60,
    )
    # After an update of the sensitivity rules, stored documents are classified again
    # (attachments are also checked with the current rules when sent).
    scheduler.add_job(
        actors.reclassify_documents.send,
        IntervalTrigger(hours=1, timezone=tz),
        id="reclassify_documents",
    )
    scheduler.add_job(
        actors.purge_document_originals.send,
        CronTrigger(hour=3, minute=50, timezone=tz),
        id="purge_document_originals",
    )
    # Voice: waiting dictations (lost messages, retries), and dictations left unused.
    scheduler.add_job(
        actors.transcribe_dictations.send,
        IntervalTrigger(minutes=1, timezone=tz),
        id="transcribe_dictations",
        misfire_grace_time=60,
    )
    scheduler.add_job(
        actors.purge_old_dictations.send,
        IntervalTrigger(hours=1, timezone=tz),
        id="purge_old_dictations",
    )
    # Reviews: daily summary (local only), weekly on Sunday evening, monthly on the 1st.
    scheduler.add_job(
        actors.scheduled_reviews.send,
        CronTrigger(hour=21, minute=30, timezone=tz),
        args=["daily"],
        id="reviews_daily",
    )
    scheduler.add_job(
        actors.scheduled_reviews.send,
        CronTrigger(day_of_week="sun", hour=19, minute=0, timezone=tz),
        args=["weekly"],
        id="reviews_weekly",
    )
    scheduler.add_job(
        actors.scheduled_reviews.send,
        CronTrigger(day=1, hour=8, minute=0, timezone=tz),
        args=["monthly"],
        id="reviews_monthly",
    )
    return scheduler


def main() -> None:
    scheduler = build_scheduler()
    log.info("scheduler_started", jobs=[job.id for job in scheduler.get_jobs()])
    scheduler.start()


if __name__ == "__main__":
    main()
