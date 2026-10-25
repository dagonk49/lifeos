"""Reading the synced calendar (task 14): occurrences of the principal's spaces only."""

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, or_, select

from lifeos.core.errors import ProblemError
from lifeos.modules.calendar.models import CalendarEvent
from lifeos.modules.calendar.schemas import EventOut
from lifeos.modules.connectors.models import ConnectorSource
from lifeos.modules.guardian.context import Ctx

# One request covers at most this span (a month view and a little more).
MAX_SPAN = timedelta(days=62)


def owner_zone(ctx: Ctx) -> ZoneInfo:
    try:
        return ZoneInfo(ctx.user.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _aware(moment: datetime, zone: ZoneInfo) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)


async def list_events(
    ctx: Ctx,
    *,
    start: datetime,
    end: datetime,
    space_id: uuid.UUID | None = None,
) -> list[EventOut]:
    """Occurrences overlapping [start, end), sorted by start. All-day occurrences are
    placed with the owner's time zone (they are dates, not instants)."""
    zone = owner_zone(ctx)
    start, end = _aware(start, zone), _aware(end, zone)
    if end <= start or end - start > MAX_SPAN:
        raise ProblemError(422, "invalid_window", "The window must be positive and at most 62 days")
    spaces = [space_id] if space_id is not None else sorted(ctx.principal.space_ids)
    if space_id is not None:
        ctx.principal.require_space(space_id)
    first_day: date = start.astimezone(zone).date()
    last_day: date = (end - timedelta(microseconds=1)).astimezone(zone).date()
    result = await ctx.db.execute(
        select(CalendarEvent, ConnectorSource.name, ConnectorSource.last_synced_at)
        .join(ConnectorSource, ConnectorSource.id == CalendarEvent.source_id)
        .where(
            CalendarEvent.user_id == ctx.principal.user_id,
            CalendarEvent.space_id.in_(spaces),
            or_(
                and_(
                    CalendarEvent.starts_at < end.astimezone(UTC),
                    CalendarEvent.ends_at > start.astimezone(UTC),
                ),
                and_(
                    CalendarEvent.start_date <= last_day,
                    CalendarEvent.end_date > first_day,
                ),
                # A zero-length timed occurrence at the start of the window.
                and_(
                    CalendarEvent.starts_at == start.astimezone(UTC),
                    CalendarEvent.ends_at == start.astimezone(UTC),
                ),
            ),
        )
    )
    rows = list(result.all())

    def key(event: CalendarEvent) -> datetime:
        if event.starts_at is not None:
            return event.starts_at
        assert event.start_date is not None
        return datetime.combine(event.start_date, datetime.min.time(), tzinfo=zone)

    rows.sort(key=lambda r: (key(r[0]), r[0].summary))
    return [
        EventOut(
            id=event.id,
            space_id=event.space_id,
            source_id=event.source_id,
            source_name=name,
            synced_at=synced_at,
            summary=event.summary,
            description=event.description,
            location=event.location,
            all_day=event.start_date is not None,
            starts_at=event.starts_at.astimezone(zone) if event.starts_at else None,
            ends_at=event.ends_at.astimezone(zone) if event.ends_at else None,
            start_date=event.start_date,
            end_date=event.end_date,
            timezone=event.timezone,
            status=event.status,
            busy=event.busy,
            recurring=event.series_id is not None,
            response=event.response,
        )
        for event, name, synced_at in rows
    ]
