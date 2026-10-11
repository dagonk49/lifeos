import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal["todo", "in_progress", "done", "cancelled"]
TaskPriority = Literal["low", "normal", "high"]


class TaskCreate(BaseModel):
    space_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=10_000)
    priority: TaskPriority = "normal"
    due_at: datetime | None = None
    remind_at: datetime | None = None
    goal_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    remind_at: datetime | None = None
    goal_id: uuid.UUID | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    title: str
    notes: str | None
    status: str
    priority: str
    due_at: datetime | None
    remind_at: datetime | None
    completed_at: datetime | None
    source_type: str
    source_id: uuid.UUID | None
    goal_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
