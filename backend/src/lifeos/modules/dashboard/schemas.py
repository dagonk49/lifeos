"""API shapes of the home page (task 23). Everything is computed locally, never by the LLM,
and says where it comes from (`sources`, `source_*` of each item)."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from lifeos.modules.finance.schemas import BudgetSnapshot
from lifeos.modules.journal.schemas import JournalEntryOut
from lifeos.modules.tasks.schemas import TaskOut

CardId = Literal[
    "priorities",
    "agenda",
    "week",
    "suggestions",
    "journal",
    "approvals",
    "memory",
    "budget",
    "assistant",
]
View = Literal["day", "week"]
WeekStart = Literal["monday", "today"]


class MemoryStats(BaseModel):
    facts: int
    memories: int
    protected: int


class AssistantStatus(BaseModel):
    provider: str
    enabled: bool
    paused: bool
    billing: str
    cloud_enabled_spaces: list[uuid.UUID]
    month_tokens_used: int
    month_token_budget: int
    requests_today: dict[str, int]
    daily_request_caps: dict[str, int]
    cost_month_usd: str


class AgendaItem(BaseModel):
    """One line of the agenda: a synced occurrence, the time of another space (« Occupé
    (autre espace) », nothing else), a task deadline, a goal milestone or a scheduled
    payment."""

    kind: Literal["event", "busy", "task", "milestone", "payment"]
    title: str
    # Local day it is listed under (an occurrence over several days is listed on each).
    day: date
    # Instants in the owner's zone; None for an all-day item (a date).
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    # None for « busy » (another space: neither its space, its title nor its source).
    space_id: uuid.UUID | None = None
    source_type: str | None = None
    source_id: uuid.UUID | None = None
    # Where it comes from, e.g. the calendar's name or the goal's title.
    source_label: str | None = None
    href: str | None = None
    # When the calendar it comes from was last read.
    synced_at: datetime | None = None
    # Short explanation: location, amount and how sure it is, what a task waits for.
    detail: str | None = None
    tentative: bool = False
    blocked: bool = False


class DaySummary(BaseModel):
    date: date
    items: list[AgendaItem]


class WeekOut(BaseModel):
    start: date
    # Last day included.
    end: date
    timezone: str
    days: list[DaySummary]
    # Followed calendars in scope, and the oldest last read among them (None: never read).
    calendars: int
    synced_at: datetime | None


class SuggestionItem(BaseModel):
    """A suggestion already stored (a review's, a grouped notification) or a budget alert:
    never generated on the fly."""

    text: str
    why: str | None
    source_type: Literal["review", "notification", "budget"]
    source_id: uuid.UUID
    source_label: str
    # When it was produced (the review's generation, the notification's delivery, now for
    # a budget alert computed from the current month).
    date: datetime
    href: str
    space_id: uuid.UUID | None


class CardSource(BaseModel):
    """What a card's figures rest on: `local` (computed on this server from your data at
    `as_of`), `calendar` (synced calendars, oldest read at `as_of`), `review` (stored
    reviews, the latest generated at `as_of`)."""

    origin: Literal["local", "calendar", "review"]
    as_of: datetime | None
    count: int | None = None


class LayoutCard(BaseModel):
    id: str = Field(max_length=40)
    visible: bool = True


class DashboardLayout(BaseModel):
    cards: list[LayoutCard]
    default_view: View = "day"
    week_start: WeekStart = "monday"
    # None while the defaults apply.
    updated_at: datetime | None = None


class LayoutIn(BaseModel):
    cards: list[LayoutCard] = Field(max_length=30)
    default_view: View = "day"
    week_start: WeekStart = "monday"


class Dashboard(BaseModel):
    generated_at: datetime
    timezone: str
    today: date
    tasks_overdue: list[TaskOut]
    tasks_today: list[TaskOut]
    tasks_upcoming: list[TaskOut]
    tasks_without_due: int
    journal_today: list[JournalEntryOut]
    pending_approvals: int
    unread_notifications: int
    memory: MemoryStats
    assistant: AssistantStatus
    # Current month budget of the selected space (personal space by default); None while
    # that space has no finance data. Computed locally, like everything here.
    budget: BudgetSnapshot | None = None
    # Task 23 (optional additions).
    agenda_today: list[AgendaItem] = Field(default_factory=list)
    week: WeekOut | None = None
    suggestions: list[SuggestionItem] = Field(default_factory=list)
    sources: dict[str, CardSource] = Field(default_factory=dict)
    layout: DashboardLayout | None = None
