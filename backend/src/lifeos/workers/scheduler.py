"""Scheduler process: only enqueues Dramatiq messages on cron triggers (ADR 0004).

Run a single instance. Missed runs (downtime) are coalesced and caught up once.
"""

from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

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
    return scheduler


def main() -> None:
    scheduler = build_scheduler()
    log.info("scheduler_started", jobs=[job.id for job in scheduler.get_jobs()])
    scheduler.start()


if __name__ == "__main__":
    main()
