"""Delivery policy of notifications (F09): pure functions, no database access.

When may a notification of a given category reach the owner?

- `critical`: at once only with the explicit permission to bypass quiet hours
  (`users.critical_bypass_quiet_hours`, a reinforced consent); otherwise held until the end
  of quiet hours like the others. Never capped, never disabled, never restricted to slots.
- `important`: as soon as possible outside quiet hours, inside the owner's delivery
  windows when any are set (none by default: reminders stay immediate).
- `suggestion`: grouped in a digest at the next digest time outside quiet hours
  (18:00 local by default).

A maximum per local day (per category) moves the surplus to the next day that still has
room. Times are local wall-clock times in the owner's time zone; instants are compared in
UTC, and local times that are skipped or repeated at DST changes are resolved with `fold`.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CATEGORIES = ("critical", "important", "suggestion")
# The categories the owner may tune (critical alerts are never silenced or capped).
CONFIGURABLE = ("important", "suggestion")
DAY = 86_400
# How far ahead a capped notification may be pushed before the cap is ignored: a
# notification is never lost.
_HORIZON_DAYS = 366


@dataclass(frozen=True)
class Window:
    """A local time window [start, end) on one day (start < end; no midnight wrap)."""

    start: time
    end: time


@dataclass(frozen=True)
class CategoryPolicy:
    enabled: bool = True
    max_per_day: int | None = None
    # important only: delivery windows; empty means "any time outside quiet hours".
    windows: tuple[Window, ...] = ()
    # suggestion only: local times of the digests.
    digest_times: tuple[time, ...] = ()


DEFAULT_IMPORTANT = CategoryPolicy()
DEFAULT_SUGGESTION = CategoryPolicy(max_per_day=3, digest_times=(time(18, 0),))
DEFAULTS: dict[str, CategoryPolicy] = {
    "important": DEFAULT_IMPORTANT,
    "suggestion": DEFAULT_SUGGESTION,
}


@dataclass(frozen=True)
class DeliveryPolicy:
    timezone: str = "UTC"
    quiet_start: time | None = None
    quiet_end: time | None = None
    critical_bypass: bool = False
    categories: Mapping[str, CategoryPolicy] = field(default_factory=lambda: dict(DEFAULTS))

    def category(self, category: str) -> CategoryPolicy:
        if category == "critical":
            return CategoryPolicy()
        return self.categories.get(category, DEFAULTS.get(category, CategoryPolicy()))

    def enabled(self, category: str) -> bool:
        return category == "critical" or self.category(category).enabled

    def local_day(self, instant: datetime) -> date:
        return instant.astimezone(ZoneInfo(self.timezone)).date()


def _seconds(value: time) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _quiet(policy: DeliveryPolicy) -> list[tuple[int, int]]:
    """Quiet intervals of any local day, in seconds since midnight."""
    start, end = policy.quiet_start, policy.quiet_end
    if start is None or end is None or start == end:
        return []
    s, e = _seconds(start), _seconds(end)
    if s < e:  # e.g. 13:00-14:00
        return [(s, e)]
    return [(0, e), (s, DAY)]  # wraps midnight, e.g. 22:00-07:00


def _subtract(
    intervals: list[tuple[int, int]], holes: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    result = intervals
    for h_start, h_end in holes:
        next_result: list[tuple[int, int]] = []
        for start, end in result:
            if h_end <= start or h_start >= end:
                next_result.append((start, end))
                continue
            if start < h_start:
                next_result.append((start, h_start))
            if h_end < end:
                next_result.append((h_end, end))
        result = next_result
    return result


def _in(intervals: list[tuple[int, int]], second: int) -> bool:
    return any(start <= second < end for start, end in intervals)


def _localize(day: date, second: int, tz: ZoneInfo, now: datetime) -> datetime:
    """The UTC instant of a local wall-clock time. A repeated time (DST end) takes its
    first occurrence unless that one is already past; a skipped time (DST start) moves
    forward by the size of the gap."""
    naive = datetime.combine(day, time()) + timedelta(seconds=second)
    # Compared in UTC: aware datetimes sharing a tzinfo compare by wall clock, without fold.
    first = naive.replace(tzinfo=tz, fold=0)
    second_reading = naive.replace(tzinfo=tz, fold=1)
    if first.utcoffset() == second_reading.utcoffset():
        return first.astimezone(UTC)
    if first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive:
        # Skipped: fold=0 reads it with the offset before the change, i.e. after the gap.
        return first.astimezone(UTC)
    earlier, later = first.astimezone(UTC), second_reading.astimezone(UTC)
    return earlier if earlier >= now else later


def _days(now: datetime, tz: ZoneInfo) -> Iterator[date]:
    today = now.astimezone(tz).date()
    for offset in range(_HORIZON_DAYS):
        yield today + timedelta(days=offset)


def _earliest_on(
    day: date, policy: DeliveryPolicy, category: str, now: datetime, *, slots: bool
) -> datetime | None:
    """The earliest instant >= now on this local day that the category may use."""
    tz = ZoneInfo(policy.timezone)
    quiet = _quiet(policy)
    rules = policy.category(category)
    if category == "suggestion" and slots and rules.digest_times:
        for digest in sorted(rules.digest_times):
            second = _seconds(digest)
            if _in(quiet, second):
                continue
            instant = _localize(day, second, tz, now)
            if instant >= now:
                return instant
        return None
    allowed: list[tuple[int, int]] = [(0, DAY)]
    if category == "important" and slots and rules.windows:
        allowed = sorted((_seconds(w.start), _seconds(w.end)) for w in rules.windows)
    for start, end in _subtract(allowed, quiet):
        candidate = _localize(day, start, tz, now)
        if candidate >= now:
            return candidate
        # `now` falls in this interval (or after it): now itself if still inside.
        local_now = now.astimezone(tz)
        if local_now.date() == day:
            current = local_now.hour * 3600 + local_now.minute * 60 + local_now.second
            if start <= current < end:
                return now
    return None


def next_delivery(
    now: datetime,
    policy: DeliveryPolicy,
    category: str,
    day_counts: Mapping[date, int] | None = None,
) -> datetime:
    """When a notification created at `now` may be delivered.

    `day_counts` holds, per local day, how many notifications of this category are already
    scheduled for that day (it is only read when the category has a maximum per day).
    """
    now = now.astimezone(UTC)
    if category == "critical" and policy.critical_bypass:
        return now
    tz = ZoneInfo(policy.timezone)
    # Critical alerts: quiet hours only (policy.category gives them no cap and no slot).
    cap = policy.category(category).max_per_day
    counts = day_counts or {}
    for slots in (True, False):
        for day in _days(now, tz):
            if cap is not None and counts.get(day, 0) >= cap:
                continue
            instant = _earliest_on(day, policy, category, now, slots=slots)
            if instant is not None:
                return instant
        # Windows or digest times that never fall outside quiet hours: ignored rather
        # than losing the notification.
    return now


def in_quiet_hours(instant: datetime, policy: DeliveryPolicy) -> bool:
    """Whether this instant falls inside the owner's quiet hours (local wall clock)."""
    local = instant.astimezone(ZoneInfo(policy.timezone))
    return _in(_quiet(policy), local.hour * 3600 + local.minute * 60 + local.second)


def quiet_hours_delivery(
    now: datetime,
    timezone: str,
    quiet_start: time | None,
    quiet_end: time | None,
    category: str,
    *,
    critical_bypass: bool = False,
) -> datetime:
    """Quiet hours alone (no windows, digests or caps): kept for simple callers."""
    policy = DeliveryPolicy(
        timezone=timezone,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        critical_bypass=critical_bypass,
        categories={c: CategoryPolicy() for c in CONFIGURABLE},
    )
    return next_delivery(now, policy, category)
