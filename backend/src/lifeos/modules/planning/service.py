"""Conflicts and slot proposals from the synced calendars (task 15). Read-only.

Occurrences of every space of the principal block time (one owner, one agenda), but only
those of the requested space show their title: elsewhere, « Occupé (autre espace) ».
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import and_, func, or_, select

from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.modules.calendar.models import CalendarEvent
from lifeos.modules.calendar.service import owner_zone
from lifeos.modules.connectors.models import ConnectorSource
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.planning import slots
from lifeos.modules.tasks.models import Task

MAX_SPAN = timedelta(days=31)


@dataclass
class Snapshot:
    events: list[slots.BusyEvent]
    # Oldest read among the calendars used (None: nothing followed, or never read).
    synced_at: datetime | None
    calendars: int


def _bounds(event: CalendarEvent, zone: object) -> tuple[datetime, datetime]:
    if event.starts_at is not None and event.ends_at is not None:
        return event.starts_at, event.ends_at
    assert event.start_date is not None and event.end_date is not None
    return (
        datetime.combine(event.start_date, time(), tzinfo=zone),  # type: ignore[arg-type]
        datetime.combine(event.end_date, time(), tzinfo=zone),  # type: ignore[arg-type]
    )


async def snapshot(ctx: Ctx, space_id: uuid.UUID, start: datetime, end: datetime) -> Snapshot:
    """Occurrences of [start, end) in every space of the principal (titles of `space_id`
    only), with the freshness of the calendars they come from."""
    zone = owner_zone(ctx)
    spaces = sorted(ctx.principal.space_ids)
    rows = await ctx.db.scalars(
        select(CalendarEvent).where(
            CalendarEvent.user_id == ctx.principal.user_id,
            CalendarEvent.space_id.in_(spaces),
            or_(
                and_(CalendarEvent.starts_at < end, CalendarEvent.ends_at > start),
                and_(
                    CalendarEvent.start_date <= end.astimezone(zone).date(),
                    CalendarEvent.end_date > start.astimezone(zone).date(),
                ),
            ),
        )
    )
    events = []
    for row in rows:
        begin, finish = _bounds(row, zone)
        mine = row.space_id == space_id
        events.append(
            slots.BusyEvent(
                ref=str(row.id) if mine else "other",
                start=begin,
                end=finish,
                title=(row.summary or "(sans titre)") if mine else None,
                busy=row.busy,
                tentative=row.status == "tentative",
                all_day=row.start_date is not None,
            )
        )
    freshness = (
        await ctx.db.execute(
            select(func.min(ConnectorSource.last_synced_at), func.count()).where(
                ConnectorSource.user_id == ctx.principal.user_id,
                ConnectorSource.space_id.in_(spaces),
                ConnectorSource.kind == "calendar",
                ConnectorSource.enabled.is_(True),
            )
        )
    ).one()
    return Snapshot(events=events, synced_at=freshness[0], calendars=int(freshness[1]))


def _window(
    start: datetime | None, end: datetime | None, default_days: int, ctx: Ctx
) -> tuple[datetime, datetime]:
    zone = owner_zone(ctx)
    begin = start if start is not None else utcnow()
    begin = begin if begin.tzinfo else begin.replace(tzinfo=zone)
    finish = end if end is not None else begin + timedelta(days=default_days)
    finish = finish if finish.tzinfo else finish.replace(tzinfo=zone)
    if finish <= begin or finish - begin > MAX_SPAN:
        raise ProblemError(422, "invalid_window", "The window must be positive and at most 31 days")
    return begin.astimezone(UTC), finish.astimezone(UTC)


async def find_conflicts(
    ctx: Ctx, space_id: uuid.UUID, start: datetime | None, end: datetime | None
) -> tuple[list[slots.Conflict], Snapshot]:
    ctx.principal.require_space(space_id)
    begin, finish = _window(start, end, 14, ctx)
    snap = await snapshot(ctx, space_id, begin, finish)
    found = [
        c
        for c in slots.conflicts(snap.events)
        # A clash between two other spaces is not this space's business.
        if c.first.title is not None or c.second.title is not None
    ]
    return found, snap


async def propose(
    ctx: Ctx,
    space_id: uuid.UUID,
    *,
    duration_minutes: int | None,
    task_id: uuid.UUID | None,
    not_before: datetime | None,
    not_after: datetime | None,
    limit: int = 3,
) -> tuple[list[slots.Slot], Snapshot, str | None]:
    """Slots for a duration (given, or the task's estimate, else one hour), before the
    task's deadline when it has one."""
    ctx.principal.require_space(space_id)
    task: Task | None = None
    if task_id is not None:
        task = await ctx.db.scalar(
            select(Task).where(
                Task.id == task_id,
                Task.user_id == ctx.principal.user_id,
                Task.space_id == space_id,
            )
        )
        if task is None:
            raise ProblemError(404, "task_not_found", "Task not found")
    estimate = getattr(task, "estimate_minutes", None) if task is not None else None
    if duration_minutes is not None:
        duration, origin = timedelta(minutes=duration_minutes), "durée demandée"
    elif estimate:
        duration, origin = timedelta(minutes=estimate), "estimation de la tâche"
    else:
        duration, origin = slots.DEFAULT_DURATION, "valeur par défaut, sans estimation"
    if not_after is None and task is not None and task.due_at is not None:
        not_after = task.due_at
    begin, finish = _window(not_before, not_after, 14, ctx)
    snap = await snapshot(ctx, space_id, begin, finish)
    zone = owner_zone(ctx)
    proposals = slots.propose_slots(
        duration,
        snap.events,
        slots.Interval(begin, finish),
        zone,
        limit=limit,
        duration_origin=origin,
        synced_at=snap.synced_at,
    )
    if snap.calendars == 0:
        for proposal in proposals:
            proposal.assumptions.append("Aucun agenda suivi : seules les disponibilités comptent.")
    return proposals, snap, task.title if task is not None else None
