"""API shapes of planning (task 15). Titles of other spaces are never present."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BusyOut(BaseModel):
    # The occurrence, in the requested space; None elsewhere (« Occupé (autre espace) »).
    event_id: uuid.UUID | None
    label: str
    start: datetime
    end: datetime
    tentative: bool


class ConflictOut(BaseModel):
    first: BusyOut
    second: BusyOut
    start: datetime
    end: datetime


class ConflictsOut(BaseModel):
    items: list[ConflictOut]
    synced_at: datetime | None
    calendars: int


class SlotsIn(BaseModel):
    space_id: uuid.UUID
    duration_minutes: int | None = Field(default=None, ge=5, le=12 * 60)
    task_id: uuid.UUID | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None


class SlotOut(BaseModel):
    start: datetime
    end: datetime
    assumptions: list[str]
    events_used: list[BusyOut]


class SlotsOut(BaseModel):
    items: list[SlotOut]
    task_title: str | None
    synced_at: datetime | None
    calendars: int
