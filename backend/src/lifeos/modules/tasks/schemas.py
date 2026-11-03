import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from lifeos.modules.tasks.models import MAX_TAG_LENGTH, MAX_TAGS

TaskStatus = Literal["todo", "in_progress", "done", "cancelled"]
TaskPriority = Literal["low", "normal", "high"]
# A task cannot wait for more tasks than this (and a creation names at most this many).
MAX_DEPENDENCIES = 20


def normalize_tag(raw: str) -> str:
    """One label: no leading '#', single spaces, lower case. Empty or too long is refused."""
    tag = " ".join(raw.split()).lstrip("#").strip().lower()
    if not tag:
        raise ValueError("empty tag")
    if len(tag) > MAX_TAG_LENGTH or "," in tag:
        raise ValueError("invalid tag")
    return tag


def normalize_tags(raw: list[str]) -> list[str]:
    """Labels in their first order, without duplicates."""
    tags = list(dict.fromkeys(normalize_tag(t) for t in raw))
    if len(tags) > MAX_TAGS:
        raise ValueError("too many tags")
    return tags


Tags = Annotated[list[str], Field(max_length=MAX_TAGS * 2), AfterValidator(normalize_tags)]


class TaskCreate(BaseModel):
    space_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=10_000)
    priority: TaskPriority = "normal"
    due_at: datetime | None = None
    remind_at: datetime | None = None
    goal_id: uuid.UUID | None = None
    tags: Tags = Field(default_factory=list)
    # Tasks of the same space this one waits for.
    depends_on: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_DEPENDENCIES)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    remind_at: datetime | None = None
    goal_id: uuid.UUID | None = None
    tags: Tags | None = None


class DependencyCreate(BaseModel):
    depends_on_id: uuid.UUID


class TaskBlocker(BaseModel):
    """An unfinished task this one waits for."""

    id: uuid.UUID
    title: str
    status: str


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
    tags: list[str] = Field(default_factory=list)
    # Every task this one waits for, finished or not (oldest link first).
    depends_on: list[uuid.UUID] = Field(default_factory=list)
    # Those still open (todo or in_progress): why the task is blocked.
    blocked_by: list[TaskBlocker] = Field(default_factory=list)
    # Open and waiting for at least one open task: it can be neither started nor finished.
    blocked: bool = False
    version: int
    created_at: datetime
    updated_at: datetime
