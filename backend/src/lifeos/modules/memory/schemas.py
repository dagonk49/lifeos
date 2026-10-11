import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FactKind = Literal["fact", "event", "preference", "decision"]


class FactCreate(BaseModel):
    space_id: uuid.UUID
    kind: FactKind = "fact"
    statement: str = Field(min_length=1, max_length=2000)
    occurred_at: datetime | None = None


class FactCorrect(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    kind: FactKind | None = None
    occurred_at: datetime | None = None


class FactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    kind: str
    statement: str
    origin: str
    confidence: Decimal
    status: str
    occurred_at: datetime | None
    source_type: str
    source_id: uuid.UUID | None
    supersedes_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class MemoryCreate(BaseModel):
    space_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    importance: int = Field(default=3, ge=1, le=5)
    protected: bool = False


class MemoryUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    importance: int | None = Field(default=None, ge=1, le=5)
    protected: bool | None = None
    change_note: str | None = Field(default=None, max_length=300)


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    title: str
    content: str
    importance: int
    protected: bool
    origin: str
    source_type: str
    source_id: uuid.UUID | None
    revision: int
    created_at: datetime
    updated_at: datetime


class MemoryRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    revision: int
    title: str
    content: str
    importance: int
    protected: bool
    change_note: str | None
    changed_at: datetime
