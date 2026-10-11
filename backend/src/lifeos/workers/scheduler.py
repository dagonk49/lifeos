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
    scheduler.add_job(
        actors.purge_auth_artifacts.send,
        CronTrigger(hour=3, minute=10, timezone=tz),
        id="purge_auth_artifacts",
    )
    scheduler.add_job(
        actors.purge_audit_events.send,
        CronTrigger(hour=3, minute=40, timezone=tz),
        id="purge_audit_events",
    )
    scheduler.add_job(
        actors.purge_messages.send,
        CronTrigger(hour=3, minute=20, timezone=tz),
        id="purge_messages",
    )
    scheduler.add_job(
        actors.purge_idempotency_keys.send,
        CronTrigger(hour=3, minute=30, timezone=tz),
        id="purge_idempotency_keys",
    )
    scheduler.add_job(
        actors.expire_approvals.send, CronTrigger(minute=5, timezone=tz), id="expire_approvals"
    )
    # Reminders are "real time": checked every minute, never coalesced into the past.
    scheduler.add_job(
        actors.send_reminders.send,
        IntervalTrigger(minutes=1, timezone=tz),
        id="send_reminders",
        misfire_grace_time=60,
    )
    scheduler.add_job(
        actors.index_embeddings.send,
        IntervalTrigger(minutes=2, timezone=tz),
        id="index_embeddings",
        misfire_grace_time=120,
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
