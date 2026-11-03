import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GoalStatus = Literal["active", "paused", "achieved", "abandoned", "archived"]


class GoalCreate(BaseModel):
    space_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    motivation: str | None = Field(default=None, max_length=2000)
    kind: Literal["measurable", "qualitative"] = "qualitative"
    metric_unit: str | None = Field(default=None, max_length=30)
    start_value: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    target_value: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    start_date: date | None = None
    due_date: date | None = None
    review_cadence: Literal["weekly", "monthly"] = "weekly"
    # Savings goal (task 26): the values are amounts in this currency.
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    monthly_contribution: Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=2
    )

    @model_validator(mode="after")
    def _measurable_needs_target(self) -> "GoalCreate":
        if self.kind == "measurable" and self.target_value is None:
            raise ValueError("a measurable goal needs a target_value")
        if self.kind == "measurable" and self.start_value is None:
            # Without a starting point, progress is ambiguous (lose 10 kg vs read 20 books).
            raise ValueError("a measurable goal needs a start_value")
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValueError("due_date is before start_date")
        if self.currency is not None and self.kind != "measurable":
            raise ValueError("a savings goal (with a currency) must be measurable")
        if self.monthly_contribution is not None and self.currency is None:
            raise ValueError("a monthly contribution needs a currency")
        if self.currency is not None and (
            (self.start_value is not None and self.start_value < 0)
            or (
                self.target_value is not None
                and self.start_value is not None
                and self.target_value <= self.start_value
            )
        ):
            raise ValueError("a savings goal goes from a saved amount up to a larger target")
        return self


class GoalUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    motivation: str | None = Field(default=None, max_length=2000)
    metric_unit: str | None = Field(default=None, max_length=30)
    start_value: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    target_value: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    start_date: date | None = None
    due_date: date | None = None
    status: GoalStatus | None = None
    review_cadence: Literal["weekly", "monthly"] | None = None
    # Explicit null turns a savings goal back into a plain measurable goal.
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    monthly_contribution: Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=2
    )


class MilestoneCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    due_date: date | None = None


class MilestoneUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_date: date | None = None
    done: bool | None = None


class CheckinCreate(BaseModel):
    value: Decimal | None = Field(default=None, max_digits=14, decimal_places=2)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _not_empty(self) -> "CheckinCreate":
        if self.value is None and not (self.note and self.note.strip()):
            raise ValueError("a check-in needs a value or a note")
        return self


class MilestoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    due_date: date | None
    done_at: datetime | None
    position: int


class CheckinOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    value: Decimal | None
    note: str | None
    created_at: datetime


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    title: str
    description: str | None
    motivation: str | None
    kind: str
    metric_unit: str | None
    start_value: Decimal | None
    target_value: Decimal | None
    current_value: Decimal | None
    currency: str | None
    monthly_contribution: Decimal | None
    start_date: date | None
    due_date: date | None
    status: str
    review_cadence: str
    achieved_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class GoalProgress(BaseModel):
    """Computed locally. `ratio` is None when there is nothing to measure yet."""

    ratio: float | None
    basis: Literal["metric", "milestones", "none"]
    milestones_done: int
    milestones_total: int
    tasks_open: int
    tasks_done: int


class GoalSummary(GoalOut):
    progress: GoalProgress


class GoalDetail(GoalSummary):
    milestones: list[MilestoneOut]
    checkins: list[CheckinOut]
