"""Adaptive notifications: quiet hours, de-duplication, reminders."""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.identity.models import User
from lifeos.modules.notifications.models import Notification
from lifeos.modules.tasks.models import Task, TaskDependency

# Read (or dismissed) notifications are purged this long after being read, unread ones
# this long after being delivered (docs/politique-de-donnees.md §1).
RETENTION = timedelta(days=90)


def next_delivery(
    now: datetime,
    timezone: str,
    quiet_start: time | None,
    quiet_end: time | None,
    category: str,
) -> datetime:
    """Critical notifications go through; others wait for the end of quiet hours."""
    if category == "critical" or quiet_start is None or quiet_end is None:
        return now
    if quiet_start == quiet_end:
        return now
    tz = ZoneInfo(timezone)
    local = now.astimezone(tz)
    current = local.time().replace(tzinfo=None)
    end_day: date
    if quiet_start < quiet_end:  # e.g. 13:00-14:00
        if not quiet_start <= current < quiet_end:
            return now
        end_day = local.date()
    else:  # wraps midnight, e.g. 22:00-07:00
        if current >= quiet_start:
            end_day = local.date() + timedelta(days=1)
        elif current < quiet_end:
            end_day = local.date()
        else:
            return now
    return datetime.combine(end_day, quiet_end, tzinfo=tz)


async def notify(
    session: AsyncSession,
    user: User,
    *,
    category: str,
    title: str,
    dedup_key: str,
    body: str | None = None,
    space_id: uuid.UUID | None = None,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
) -> bool:
    """Creates the notification unless one with the same key exists (anti-duplicates)."""
    now = utcnow()
    result = await session.execute(
        insert(Notification)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            space_id=space_id,
            category=category,
            title=title,
            body=body,
            dedup_key=dedup_key,
            deliver_after=next_delivery(
                now, user.timezone, user.quiet_hours_start, user.quiet_hours_end, category
            ),
            status="pending",
            source_type=source_type,
            source_id=source_id,
            created_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_notifications_dedup")
        .returning(Notification.id)
    )
    return result.scalar_one_or_none() is not None


async def dispatch_reminders(session: AsyncSession) -> int:
    """Turns due task reminders into notifications. Idempotent (dedup key + reminded_at)."""
    now = utcnow()
    rows = (
        await session.execute(
            select(Task, User)
            .join(User, User.id == Task.user_id)
            .where(
                Task.remind_at.is_not(None),
                Task.remind_at <= now,
                Task.reminded_at.is_(None),
                Task.status.in_(("todo", "in_progress")),
            )
            .with_for_update(of=Task, skip_locked=True)
            .limit(500)
        )
    ).all()
    created = 0
    waiting = await _open_blocker_counts(session, [task.id for task, _ in rows])
    for task, user in rows:
        assert task.remind_at is not None
        # A blocked task is still reminded; the body counts what it waits for, without
        # naming it (that title would outlive the blocker's erasure).
        blockers = waiting.get(task.id, 0)
        if await notify(
            session,
            user,
            category="important",
            title=f"Rappel : {task.title}",
            body=f"Encore bloquée par {blockers} tâche(s) non terminée(s)." if blockers else None,
            dedup_key=f"task:{task.id}:remind:{task.remind_at.isoformat()}",
            space_id=task.space_id,
            source_type="task",
            source_id=task.id,
        ):
            created += 1
        await session.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(reminded_at=now)
            .execution_options(synchronize_session=False)
        )
    if rows:
        await audit.record(
            session,
            action="notification.dispatch_reminders",
            actor_type="system",
            actor_id="dispatch_reminders",
            details={"reminders": len(rows), "created": created},
        )
    return created


async def _open_blocker_counts(
    session: AsyncSession, task_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not task_ids:
        return {}
    rows = await session.execute(
        select(TaskDependency.task_id, func.count())
        .join(Task, Task.id == TaskDependency.depends_on_id)
        .where(
            TaskDependency.task_id.in_(task_ids),
            Task.status.in_(("todo", "in_progress")),
        )
        .group_by(TaskDependency.task_id)
    )
    return {task_id: int(count) for task_id, count in rows}


async def purge_old(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Notifications read more than RETENTION ago, or delivered that long ago and never
    read; audited by count. Their titles may quote a task (« Rappel : <titre> »)."""
    cutoff = (now or utcnow()) - RETENTION
    result = await session.execute(
        delete(Notification).where(
            or_(
                and_(Notification.read_at.is_not(None), Notification.read_at < cutoff),
                and_(Notification.read_at.is_(None), Notification.deliver_after < cutoff),
            )
        )
    )
    count = int(result.rowcount or 0)  # type: ignore[attr-defined]
    if count:
        await audit.record(
            session,
            action="notification.purge",
            actor_type="system",
            actor_id="purge_notifications",
            details={"count": count},
        )
    return count
