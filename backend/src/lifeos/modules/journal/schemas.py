import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JournalEntryCreate(BaseModel):
    space_id: uuid.UUID
    entry_date: date
    content: str = Field(min_length=1, max_length=20_000)
    mood: int | None = Field(default=None, ge=1, le=5)


class JournalEntryUpdate(BaseModel):
    entry_date: date | None = None
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    mood: int | None = Field(default=None, ge=1, le=5)


class JournalEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    entry_date: date
    content: str
    mood: int | None
    analyzed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


# --- Analysis (LLM structured output) and proposals (approved by the owner) ---------------


class ProposedTask(BaseModel):
    title: str = Field(min_length=1, max_length=300, description="Action à faire, à l'infinitif")
    due_date: date | None = Field(default=None, description="Échéance explicitement mentionnée")


class ProposedFact(BaseModel):
    kind: Literal["fact", "event", "preference", "decision"] = Field(
        description="event: accomplissement ou événement daté ; preference ; decision ; fact"
    )
    statement: str = Field(min_length=1, max_length=500, description="Énoncé factuel court")
    occurred_on: date | None = None


class JournalAnalysis(BaseModel):
    """What the model may propose. Nothing is saved before the owner approves."""

    tasks: list[ProposedTask] = Field(default_factory=list, max_length=20)
    facts: list[ProposedFact] = Field(default_factory=list, max_length=20)


class JournalProposals(BaseModel):
    """Exact parameters of the `journal.apply_proposals` action."""

    entry_id: uuid.UUID
    tasks: list[ProposedTask] = Field(default_factory=list, max_length=20)
    facts: list[ProposedFact] = Field(default_factory=list, max_length=20)
