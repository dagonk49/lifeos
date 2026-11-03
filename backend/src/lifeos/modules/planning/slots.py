"""Conflicts and free slots, computed locally (task 15). Pure functions: no I/O.

Times are aware datetimes. Availability is a weekly pattern of LOCAL hours (the owner's
time zone): days are walked locally, so a 23- or 25-hour day (change of daylight saving
time) gives slots at the right local hours and of the right length.

Computations run on UTC instants: Python subtracts two datetimes of the same ZoneInfo as
wall-clock times, which is wrong across a change of time. Local time only places the
availability on the days and rounds the proposals.

Every proposal says what it rests on (`assumptions`) and which occurrences it avoided
(`events_used`): the owner can check it, the assistant only explains it.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Monday = 0 ... Sunday = 6 -> list of (start, end) local times.
Availability = dict[int, list[tuple[time, time]]]

DEFAULT_AVAILABILITY: Availability = {day: [(time(9, 0), time(18, 0))] for day in range(5)}
DEFAULT_DURATION = timedelta(minutes=60)
WEEKDAYS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def utc(self) -> "Interval":
        return Interval(_utc(self.start), _utc(self.end))

    def overlaps(self, other: "Interval") -> bool:
        """Strictly positive overlap: touching intervals do not conflict."""
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class BusyEvent:
    """An occurrence that may block time. `title` is None outside the requested space:
    only « Occupé (autre espace) » is shown, never its content."""

    ref: str
    start: datetime
    end: datetime
    title: str | None = None
    busy: bool = True
    tentative: bool = False
    all_day: bool = False

    @property
    def interval(self) -> Interval:
        return Interval(_utc(self.start), _utc(self.end))

    @property
    def label(self) -> str:
        return self.title if self.title is not None else "Occupé (autre espace)"


@dataclass(frozen=True)
class Conflict:
    first: BusyEvent
    second: BusyEvent
    start: datetime
    end: datetime


@dataclass
class Slot:
    start: datetime
    end: datetime
    assumptions: list[str] = field(default_factory=list)
    events_used: list[BusyEvent] = field(default_factory=list)


def blocking(
    events: Iterable[BusyEvent], *, tentative_busy: bool = True, all_day_busy: bool = False
) -> list[BusyEvent]:
    """Occurrences that take time: free (transparent) ones never; tentative ones by
    default; all-day ones (holidays, birthdays) only when asked."""
    kept = []
    for event in events:
        if not event.busy or event.end <= event.start:
            continue
        if event.tentative and not tentative_busy:
            continue
        if event.all_day and not all_day_busy:
            continue
        kept.append(event)
    return kept


def conflicts(events: Iterable[BusyEvent]) -> list[Conflict]:
    """Pairs of blocking occurrences that overlap (sweep over start times)."""
    ordered = sorted(blocking(events), key=lambda e: (_utc(e.start), _utc(e.end), e.ref))
    found: list[Conflict] = []
    active: list[BusyEvent] = []
    for event in ordered:
        active = [a for a in active if _utc(a.end) > _utc(event.start)]
        for other in active:
            found.append(
                Conflict(
                    first=other,
                    second=event,
                    start=max(_utc(other.start), _utc(event.start)),
                    end=min(_utc(other.end), _utc(event.end)),
                )
            )
        active.append(event)
    return found


def _merge(intervals: Iterable[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for interval in sorted(intervals, key=lambda i: i.start):
        if merged and interval.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = Interval(last.start, max(last.end, interval.end))
        else:
            merged.append(interval)
    return merged


def _local(day: date, moment: time, zone: ZoneInfo) -> datetime:
    """A local wall-clock time, as a UTC instant (a time that does not exist, in the spring
    gap, moves forward)."""
    return datetime.combine(day, moment, tzinfo=zone).astimezone(UTC)


def available_intervals(
    window: Interval, availability: Availability, zone: ZoneInfo
) -> list[Interval]:
    """The availability pattern laid on the local days of `window`, clipped to it."""
    window = window.utc()
    first = window.start.astimezone(zone).date()
    last = window.end.astimezone(zone).date()
    result: list[Interval] = []
    day = first
    while day <= last:
        for start, end in availability.get(day.weekday(), []):
            begin = _local(day, start, zone)
            finish = (
                _local(day + timedelta(days=1), time(0), zone)
                if end == time(0)
                else _local(day, end, zone)
            )
            begin, finish = max(begin, window.start), min(finish, window.end)
            if finish > begin:
                result.append(Interval(begin, finish))
        day += timedelta(days=1)
    return _merge(result)


def free_intervals(
    window: Interval,
    events: Sequence[BusyEvent],
    availability: Availability,
    zone: ZoneInfo,
    *,
    buffer: timedelta = timedelta(0),
) -> list[Interval]:
    """Available time minus blocking occurrences (each widened by `buffer`)."""
    busy = _merge(Interval(e.start - buffer, e.end + buffer) for e in blocking(events))
    free: list[Interval] = []
    for span in available_intervals(window, availability, zone):
        cursor = span.start
        for block in busy:
            if block.end <= cursor or block.start >= span.end:
                continue
            if block.start > cursor:
                free.append(Interval(cursor, block.start))
            cursor = max(cursor, block.end)
        if cursor < span.end:
            free.append(Interval(cursor, span.end))
    return free


def describe_availability(availability: Availability) -> str:
    """« lundi à vendredi, 09:00-18:00 » or the days one by one."""
    days = sorted(availability)
    patterns = {tuple(availability[d]) for d in days}

    def hours(spans: list[tuple[time, time]]) -> str:
        return ", ".join(f"{s:%H:%M}-{e:%H:%M}" for s, e in spans)

    if days and len(patterns) == 1 and days == list(range(days[0], days[-1] + 1)):
        span = (
            WEEKDAYS_FR[days[0]]
            if len(days) == 1
            else f"{WEEKDAYS_FR[days[0]]} à {WEEKDAYS_FR[days[-1]]}"
        )
        return f"{span}, {hours(availability[days[0]])}"
    return " ; ".join(f"{WEEKDAYS_FR[d]} {hours(availability[d])}" for d in days) or "aucune"


def propose_slots(
    duration: timedelta,
    events: Sequence[BusyEvent],
    window: Interval,
    zone: ZoneInfo,
    *,
    availability: Availability | None = None,
    buffer: timedelta = timedelta(minutes=10),
    limit: int = 3,
    duration_origin: str = "estimation de la tâche",
    synced_at: datetime | None = None,
    tentative_busy: bool = True,
) -> list[Slot]:
    """The first `limit` slots of `duration` that fit the availability, outside every
    blocking occurrence widened by `buffer`, at most one per local day (spread choices)."""
    pattern = availability if availability is not None else DEFAULT_AVAILABILITY
    considered = blocking(events, tentative_busy=tentative_busy)
    assumptions = [
        f"Durée : {int(duration.total_seconds() // 60)} min ({duration_origin}).",
        f"Disponibilités : {describe_availability(pattern)} ({zone.key}).",
        f"Marge de {int(buffer.total_seconds() // 60)} min autour de chaque événement.",
        "Les événements provisoires comptent comme occupés."
        if tentative_busy
        else "Les événements provisoires ne comptent pas.",
        "Les événements marqués « disponible » et les journées entières ne bloquent pas.",
    ]
    if synced_at is not None:
        assumptions.append(
            f"Agenda lu le {synced_at.astimezone(zone):%d/%m/%Y à %H:%M} : un changement "
            "plus récent n'est pas pris en compte."
        )
    slots: list[Slot] = []
    days_used: set[date] = set()
    for free in free_intervals(window, considered, pattern, zone, buffer=buffer):
        day = free.start.astimezone(zone).date()
        if day in days_used or free.end - free.start < duration:
            continue
        start = free.start
        # Round up to the next quarter of an hour (local), when it still fits.
        local = start.astimezone(zone)
        extra = (-local.minute) % 15
        if extra or local.second or local.microsecond:
            rounded = _utc((local + timedelta(minutes=extra)).replace(second=0, microsecond=0))
            if rounded + duration <= free.end:
                start = rounded
        end = start + duration
        nearby = Interval(start - timedelta(hours=2), end + timedelta(hours=2))
        slots.append(
            Slot(
                start=start,
                end=end,
                assumptions=list(assumptions),
                events_used=[e for e in considered if e.interval.overlaps(nearby)],
            )
        )
        days_used.add(day)
        if len(slots) >= limit:
            break
    return slots


def deadline_at_risk(
    due: datetime,
    duration: timedelta,
    events: Sequence[BusyEvent],
    now: datetime,
    zone: ZoneInfo,
    *,
    availability: Availability | None = None,
    buffer: timedelta = timedelta(minutes=10),
) -> bool:
    """True when the free time before `due` is shorter than the task needs."""
    if due <= now:
        return True
    pattern = availability if availability is not None else DEFAULT_AVAILABILITY
    free = free_intervals(Interval(now, due).utc(), events, pattern, zone, buffer=buffer)
    return sum((f.end - f.start for f in free), timedelta(0)) < duration
