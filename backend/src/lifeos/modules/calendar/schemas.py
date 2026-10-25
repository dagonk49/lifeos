"""API shapes of the calendar (read). Event texts come from third parties: data only."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel


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


class EventsOut(BaseModel):
    items: list[EventOut]
    # The window asked for, in the owner's time zone.
    timezone: str
