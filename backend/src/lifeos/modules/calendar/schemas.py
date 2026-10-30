"""API shapes of the calendar. Event texts come from third parties: data only."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class EventOut(BaseModel):
    id: uuid.UUID
    space_id: uuid.UUID
    source_id: uuid.UUID
    # Where it comes from: the calendar's name and when it was last read.
    source_name: str
    synced_at: datetime | None
    summary: str
    description: str | None
    location: str | None
    all_day: bool
    # Timed: instants with offset (ISO 8601). All-day: dates, `end_date` exclusive.
    starts_at: datetime | None
    ends_at: datetime | None
    start_date: date | None
    end_date: date | None
    timezone: str | None
    status: str
    busy: bool
    recurring: bool
    response: str | None
    # Its account may modify this calendar here (calendar.write consent): « Déplacer »,
    # « Annuler » propose an approval.
    editable: bool = False


class WhenIn(BaseModel):
    """Instants (without offset: read in the owner's zone), or dates (`end_date`
    exclusive) for an all-day event."""

    starts_at: datetime | None = None
    ends_at: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None


class CreateEventIn(WhenIn):
    space_id: uuid.UUID
    # A followed calendar of the space; default: the first one that may be modified.
    source_id: uuid.UUID | None = None
    summary: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=300)
    guests: list[str] = Field(default_factory=list, max_length=50)
    notify: bool = False


class MoveEventIn(WhenIn):
    notify: bool = False


class CancelEventIn(BaseModel):
    notify: bool = False


class WritableCalendarOut(BaseModel):
    source_id: uuid.UUID
    name: str
    account_id: uuid.UUID


class EventsOut(BaseModel):
    items: list[EventOut]
    # The window asked for, in the owner's time zone.
    timezone: str
