"""Adaptive notifications (F09): quiet hours, delivery windows, suggestion digests, maximum
per day, targeted disabling, de-duplication, reminders.

A notification is "due" once `deliver_after` has passed (`due()` below): that is what the
in-app menu lists and what a push sender (task 19) picks up. Suggestions are never due one
by one: `build_digests` groups the due ones into a single digest notification, which is.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.identity.models import User
from lifeos.modules.notifications.models import Notification, NotificationPreference
from lifeos.modules.notifications.policy import (
    DEFAULTS,
    CategoryPolicy,
    DeliveryPolicy,
    Window,
    next_delivery,
)
from lifeos.modules.tasks.models import Task, TaskDependency

# Read (or dismissed) notifications are purged this long after being read, unread ones
# this long after being delivered (docs/politique-de-donnees.md §1).
RETENTION = timedelta(days=90)
# Serialises, per user, the maximum-per-day checks and re-timings (two-key advisory lock,
# as guardian/gateway.py _quota_lock).
NOTIFY_LOCK_CLASS = 0x4E4F5446  # "NOTF"
DIGEST = "digest"

Outcome = Literal["created", "duplicate", "suppressed"]


def _lock(user_id: uuid.UUID) -> Any:
    return func.pg_advisory_xact_lock(NOTIFY_LOCK_CLASS, func.hashtext(str(user_id)))


def _not_digest() -> ColumnElement[bool]:
    return Notification.source_type.is_distinct_from(DIGEST)


def due(now: datetime) -> list[ColumnElement[bool]]:
    """Notifications that may reach the owner now (whatever the user and space): delivered,
    not inside a digest, and not a suggestion still waiting for its digest."""
    return [
        Notification.deliver_after <= now,
        Notification.digest_id.is_(None),
        or_(Notification.category != "suggestion", Notification.source_type == DIGEST),
    ]


# --- Preferences -----------------------------------------------------------------------------


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def category_policy(row: NotificationPreference) -> CategoryPolicy:
    return CategoryPolicy(
        enabled=row.enabled,
        max_per_day=row.max_per_day,
        windows=tuple(
            Window(_parse_time(w["start"]), _parse_time(w["end"])) for w in row.windows or []
        ),
        digest_times=tuple(_parse_time(t) for t in row.digest_times or []),
    )


async def load_policy(session: AsyncSession, user: User) -> DeliveryPolicy:
    """The owner's delivery policy: settings plus preference rows (defaults otherwise)."""
    categories = dict(DEFAULTS)
    rows = await session.scalars(
        select(NotificationPreference).where(NotificationPreference.user_id == user.id)
    )
    for row in rows:
        categories[row.category] = category_policy(row)
    return DeliveryPolicy(
        timezone=user.timezone,
        quiet_start=user.quiet_hours_start,
        quiet_end=user.quiet_hours_end,
        critical_bypass=user.critical_bypass_quiet_hours,
        categories=categories,
    )


def _day_start(policy: DeliveryPolicy, now: datetime) -> datetime:
    tz = ZoneInfo(policy.timezone)
    return datetime.combine(now.astimezone(tz).date(), time(), tzinfo=tz).astimezone(UTC)


async def _day_counts(
    session: AsyncSession,
    user_id: uuid.UUID,
    category: str,
    policy: DeliveryPolicy,
    now: datetime,
    *,
    until: datetime | None = None,
) -> dict[date, int]:
    """How many notifications of the category are scheduled per local day, from today
    (digests themselves do not count: their items do)."""
    local_day = func.date(func.timezone(policy.timezone, Notification.deliver_after))
    stmt = (
        select(local_day, func.count())
        .where(
            Notification.user_id == user_id,
            Notification.category == category,
            _not_digest(),
            Notification.deliver_after >= _day_start(policy, now),
        )
        .group_by(local_day)
    )
    if until is not None:
        stmt = stmt.where(Notification.deliver_after <= until)
    return {day: int(count) for day, count in await session.execute(stmt)}


# --- Creation --------------------------------------------------------------------------------


async def deliver(
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
) -> Outcome:
    """Schedules a notification according to the owner's policy.

    "suppressed": the owner disabled this category (critical alerts never are);
    "duplicate": one with the same key exists (anti-duplicates).
    """
    policy = await load_policy(session, user)
    if not policy.enabled(category):
        return "suppressed"
    # Concurrent producers (reminders, reviews, API) must not both take the last place of
    # the day: the count and the insert happen under a per-user lock.
    await session.execute(select(_lock(user.id)))
    now = utcnow()
    counts: dict[date, int] = {}
    if policy.category(category).max_per_day is not None:
        counts = await _day_counts(session, user.id, category, policy, now)
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
            deliver_after=next_delivery(now, policy, category, counts),
            status="pending",
            source_type=source_type,
            source_id=source_id,
            created_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_notifications_dedup")
        .returning(Notification.id)
    )
    return "created" if result.scalar_one_or_none() is not None else "duplicate"


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
    """Creates the notification unless one with the same key exists or the category is
    disabled. Returns whether it was created."""
    outcome = await deliver(
        session,
        user,
        category=category,
        title=title,
        dedup_key=dedup_key,
        body=body,
        space_id=space_id,
        source_type=source_type,
        source_id=source_id,
    )
    return outcome == "created"


# --- Re-timing -------------------------------------------------------------------------------


async def retime_pending(session: AsyncSession, user: User) -> dict[str, int]:
    """After a change of quiet hours, time zone, critical permission or preferences:
    notifications not delivered yet are scheduled again with the new policy (oldest first,
    so the maximum per day keeps the first ones), and those of a now disabled category are
    dismissed. Their dedup keys are untouched."""
    policy = await load_policy(session, user)
    await session.execute(select(_lock(user.id)))
    now = utcnow()
    pending = list(
        await session.scalars(
            select(Notification)
            .where(
                Notification.user_id == user.id,
                Notification.status == "pending",
                Notification.deliver_after > now,
                Notification.digest_id.is_(None),
                _not_digest(),
            )
            .order_by(Notification.created_at, Notification.id)
        )
    )
    counts: dict[str, dict[date, int]] = {}
    retimed = dismissed = 0
    for notification in pending:
        category = notification.category
        if not policy.enabled(category):
            notification.status = "dismissed"
            notification.read_at = now
            dismissed += 1
            continue
        if category not in counts:
            counts[category] = (
                # Only what was already delivered today: the others are being re-timed.
                await _day_counts(session, user.id, category, policy, now, until=now)
                if policy.category(category).max_per_day is not None
                else {}
            )
        target = next_delivery(now, policy, category, counts[category])
        day = policy.local_day(target)
        counts[category][day] = counts[category].get(day, 0) + 1
        if target != notification.deliver_after:
            notification.deliver_after = target
            retimed += 1
    return {"pending": len(pending), "retimed": retimed, "dismissed": dismissed}


# --- Digests ---------------------------------------------------------------------------------


def _digest_body(count: int) -> str:
    return "1 suggestion" if count == 1 else f"{count} suggestions"


async def build_digests(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Groups the due suggestions of each owner into one digest notification (F09:
    « suggestions : regroupement »). Idempotent: an item belongs to at most one digest
    (digest_id set in the same transaction, rows locked with SKIP LOCKED).

    The digest quotes no item title: an item erased with its source (a review, a task)
    must not survive in it. Its items are listed through GET /v1/notifications/{id}/items.
    """
    now = now or utcnow()
    items = list(
        await session.scalars(
            select(Notification)
            .where(
                Notification.category == "suggestion",
                Notification.status == "pending",
                Notification.deliver_after <= now,
                Notification.digest_id.is_(None),
                _not_digest(),
            )
            .order_by(Notification.user_id, Notification.deliver_after, Notification.id)
            .with_for_update(skip_locked=True)
            .limit(1000)
        )
    )
    by_user: dict[uuid.UUID, list[Notification]] = {}
    for item in items:
        by_user.setdefault(item.user_id, []).append(item)
    digests = 0
    for user_id, group in by_user.items():
        digest_id = await _create_digest(session, user_id, group, now)
        if digest_id is None:
            continue
        digests += 1
        await session.execute(
            update(Notification)
            .where(Notification.id.in_([item.id for item in group]))
            .values(digest_id=digest_id)
            .execution_options(synchronize_session=False)
        )
    if items:
        await audit.record(
            session,
            action="notification.digest",
            actor_type="system",
            actor_id="build_digests",
            details={"users": len(by_user), "digests": digests, "items": len(items)},
        )
    return {"digests": digests, "items": len(items)}


async def _create_digest(
    session: AsyncSession, user_id: uuid.UUID, group: Sequence[Notification], now: datetime
) -> uuid.UUID | None:
    spaces = {item.space_id for item in group}
    first = min(group, key=lambda item: (item.deliver_after, item.id))
    digest_id = await session.scalar(
        insert(Notification)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            # One space when every item shares it, otherwise none (visible to the owner).
            space_id=spaces.pop() if len(spaces) == 1 else None,
            category="suggestion",
            title="Suggestions regroupées",
            body=_digest_body(len(group)),
            dedup_key=f"digest:suggestion:{first.id}",
            deliver_after=now,
            status="pending",
            source_type=DIGEST,
            created_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_notifications_dedup")
        .returning(Notification.id)
    )
    return digest_id


# --- Reminders -------------------------------------------------------------------------------


async def dispatch_reminders(session: AsyncSession) -> int:
    """Turns due task reminders into notifications. Idempotent (dedup key + reminded_at).

    A reminder of a disabled category is not created, but the task is still marked as
    reminded: enabling the category again does not replay the old reminders."""
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
    created = suppressed = 0
    waiting = await _open_blocker_counts(session, [task.id for task, _ in rows])
    for task, user in rows:
        assert task.remind_at is not None
        # A blocked task is still reminded; the body counts what it waits for, without
        # naming it (that title would outlive the blocker's erasure).
        blockers = waiting.get(task.id, 0)
        outcome = await deliver(
            session,
            user,
            category="important",
            title=f"Rappel : {task.title}",
            body=f"Encore bloquée par {blockers} tâche(s) non terminée(s)." if blockers else None,
            dedup_key=f"task:{task.id}:remind:{task.remind_at.isoformat()}",
            space_id=task.space_id,
            source_type="task",
            source_id=task.id,
        )
        created += outcome == "created"
        suppressed += outcome == "suppressed"
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
            details={"reminders": len(rows), "created": created, "suppressed": suppressed},
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
