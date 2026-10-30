"""Conflicts and free slots (task 15): pure functions, no database.

Acceptance criteria: a real conflict is reported; a proposed slot never overlaps a known
unavailability.
"""

import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from lifeos.modules.planning.slots import (
    BusyEvent,
    Interval,
    available_intervals,
    blocking,
    conflicts,
    deadline_at_risk,
    describe_availability,
    propose_slots,
)

PARIS = ZoneInfo("Europe/Paris")


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=PARIS)


def event(ref: str, start: datetime, minutes: int, **extra: object) -> BusyEvent:
    return BusyEvent(
        ref=ref, start=start, end=start + timedelta(minutes=minutes), title=ref, **extra
    )  # type: ignore[arg-type]


def test_overlaps_are_conflicts_touching_events_are_not() -> None:
    a = event("a", at(21, 9), 60)
    b = event("b", at(21, 9, 30), 60)
    c = event("c", at(21, 10, 30), 30)  # starts when b ends
    d = event("d", at(21, 11), 30)
    found = conflicts([d, c, b, a])
    assert [(x.first.ref, x.second.ref) for x in found] == [("a", "b")]
    assert (found[0].start, found[0].end) == (at(21, 9, 30), at(21, 10))
    assert conflicts([c, d]) == []


def test_free_tentative_and_all_day_rules() -> None:
    free = event("free", at(21, 9), 60, busy=False)
    maybe = event("maybe", at(21, 9), 60, tentative=True)
    holiday = BusyEvent("holiday", at(21, 0), at(22, 0), "Férié", all_day=True)
    meeting = event("meeting", at(21, 9, 30), 30)
    assert [x.second.ref for x in conflicts([free, meeting])] == []
    assert [x.second.ref for x in conflicts([maybe, meeting])] == ["meeting"]
    assert [e.ref for e in blocking([maybe], tentative_busy=False)] == []
    assert [e.ref for e in blocking([holiday])] == []
    assert [e.ref for e in blocking([holiday], all_day_busy=True)] == ["holiday"]


def test_slots_avoid_busy_time_with_their_margin_and_say_why() -> None:
    window = Interval(at(21, 0), at(23, 0))  # Wednesday and Thursday
    events = [event("Comité", at(21, 9), 120), BusyEvent("x", at(21, 11, 30), at(21, 17, 55))]
    slots = propose_slots(
        timedelta(minutes=45),
        events,
        window,
        PARIS,
        synced_at=datetime(2026, 10, 21, 6, 0, tzinfo=UTC),
        duration_origin="valeur par défaut",
    )
    # Wednesday: 11:10-11:20 too short, after 18:05 outside hours: next day 09:00.
    assert [(s.start, s.end) for s in slots] == [(at(22, 9), at(22, 9, 45))]
    assumptions = slots[0].assumptions
    assert assumptions[0] == "Durée : 45 min (valeur par défaut)."
    assert "lundi à vendredi, 09:00-18:00 (Europe/Paris)" in assumptions[1]
    assert "Marge de 10 min" in assumptions[2]
    assert any("Agenda lu le 21/10/2026 à 08:00" in a for a in assumptions)


def test_slots_keep_local_hours_across_daylight_saving_changes() -> None:
    # Sunday 25 October 2026 lasts 25 hours in Paris, Sunday 29 March 2026 lasts 23.
    every_day = {d: [(time(0), time(0))] for d in range(7)}
    autumn = available_intervals(Interval(at(25, 0), at(26, 0)), every_day, PARIS)
    assert sum((i.end - i.start for i in autumn), timedelta()) == timedelta(hours=25)
    spring = available_intervals(Interval(at(29, 0, month=3), at(30, 0, month=3)), every_day, PARIS)
    assert sum((i.end - i.start for i in spring), timedelta()) == timedelta(hours=23)

    weekdays = {d: [(time(9), time(18))] for d in range(7)}
    slots = propose_slots(
        timedelta(hours=1),
        [],
        Interval(at(24, 0), at(27, 0)),
        PARIS,
        availability=weekdays,
    )
    # 09:00 local every day, whatever the offset that day.
    assert [s.start.astimezone(PARIS).strftime("%d %H:%M %z") for s in slots] == [
        "24 09:00 +0200",
        "25 09:00 +0100",
        "26 09:00 +0100",
    ]


def test_random_calendars_never_get_a_slot_on_busy_time() -> None:
    rng = random.Random(14)
    buffer = timedelta(minutes=10)
    for _ in range(200):
        day = rng.randint(19, 30)
        events = []
        for n in range(rng.randint(0, 8)):
            start = at(day, rng.randint(7, 19), rng.choice([0, 15, 30, 45]))
            events.append(event(f"e{n}", start, rng.choice([15, 30, 60, 90, 180])))
        window = Interval(at(day, 0), at(day, 0) + timedelta(days=3))
        duration = timedelta(minutes=rng.choice([15, 30, 45, 60, 120]))
        for slot in propose_slots(duration, events, window, PARIS, buffer=buffer):
            assert slot.end - slot.start == duration
            assert window.start <= slot.start and slot.end <= window.end
            local_start, local_end = slot.start.astimezone(PARIS), slot.end.astimezone(PARIS)
            assert local_start.weekday() < 5 and local_start.date() == local_end.date()
            assert time(9) <= local_start.time() and local_end.time() <= time(18)
            for busy in events:
                widened = Interval(busy.start - buffer, busy.end + buffer)
                assert not widened.overlaps(Interval(slot.start, slot.end)), (busy, slot)


def test_a_deadline_without_enough_free_time_is_at_risk() -> None:
    now = at(21, 9)
    due = at(21, 12)
    meeting = event("Réunion", at(21, 9, 30), 120)  # leaves 09:00-09:20 and 11:40-12:00
    assert deadline_at_risk(due, timedelta(minutes=45), [meeting], now, PARIS)
    assert not deadline_at_risk(due, timedelta(minutes=40), [meeting], now, PARIS)
    assert not deadline_at_risk(due, timedelta(minutes=30), [], now, PARIS)
    assert deadline_at_risk(at(21, 8), timedelta(minutes=5), [], now, PARIS)


def test_availability_is_described_in_french() -> None:
    assert describe_availability({0: [(time(9), time(12)), (time(14), time(18))]}) == (
        "lundi, 09:00-12:00, 14:00-18:00"
    )
    assert describe_availability({0: [(time(9), time(12))], 2: [(time(10), time(11))]}) == (
        "lundi 09:00-12:00 ; mercredi 10:00-11:00"
    )
