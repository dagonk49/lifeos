import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Direction = Literal["expense", "income"]
CategoryKind = Literal["expense", "income"]
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_MONTH_RE = re.compile(MONTH_PATTERN)


def _clean(value: str | None, *, collapse: bool = True) -> str | None:
    """Trimmed text (single-line fields: inner whitespace collapsed); blank becomes NULL."""
    if value is None:
        return None
    cleaned = " ".join(value.split()) if collapse else value.strip()
    return cleaned or None


# --- Categories ------------------------------------------------------------------------------


class CategoryCreate(BaseModel):
    space_id: uuid.UUID
    name: str = Field(min_length=1, max_length=60)
    kind: CategoryKind = "expense"
    monthly_budget: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("the name is empty")
        return cleaned


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    kind: CategoryKind | None = None
    # Explicit null removes the budget.
    monthly_budget: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    archived: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("the name is empty")
        return cleaned


class CategoryDefaults(BaseModel):
    space_id: uuid.UUID


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    name: str
    kind: str
    monthly_budget: Decimal | None
    archived: bool
    created_at: datetime
    updated_at: datetime


# --- Transactions ----------------------------------------------------------------------------


class TransactionCreate(BaseModel):
    space_id: uuid.UUID
    # Exact decimals, strictly positive, at most two decimal places: the sign is `direction`.
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    direction: Direction = "expense"
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    tx_date: date
    merchant: str | None = Field(default=None, max_length=120)
    label: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    category_id: uuid.UUID | None = None
    source_type: Literal["manual", "receipt"] = "manual"
    # The receipt document (purpose "receipt", same space); required for source "receipt".
    document_id: uuid.UUID | None = None

    @field_validator("merchant", "label")
    @classmethod
    def _line(cls, value: str | None) -> str | None:
        return _clean(value)

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str | None) -> str | None:
        return _clean(value, collapse=False)


class TransactionUpdate(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    direction: Direction | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    tx_date: date | None = None
    merchant: str | None = Field(default=None, max_length=120)
    label: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    # Setting (or confirming) a category teaches the rule for this merchant; null removes it.
    category_id: uuid.UUID | None = None

    @field_validator("merchant", "label")
    @classmethod
    def _line(cls, value: str | None) -> str | None:
        return _clean(value)

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str | None) -> str | None:
        return _clean(value, collapse=False)


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    tx_date: date
    amount: Decimal
    direction: str
    currency: str
    merchant: str | None
    label: str | None
    notes: str | None
    category_id: uuid.UUID | None
    # False: chosen automatically by a learned rule (correctable); True: by the owner.
    category_confirmed: bool
    source_type: str
    document_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


# --- Receipts --------------------------------------------------------------------------------


class SuggestedCategory(BaseModel):
    id: uuid.UUID
    name: str
    # "rule": learned from the owner's earlier choices; "keyword": merchant name heuristic.
    source: Literal["rule", "keyword"]


class ReceiptLines(BaseModel):
    """The receipt lines each value was read from (explainability)."""

    merchant: str | None = None
    tx_date: str | None = None
    total: str | None = None


class ReceiptProposalOut(BaseModel):
    """What the local parser read; nothing is recorded until the owner posts a transaction."""

    document_id: uuid.UUID
    space_id: uuid.UUID
    title: str
    merchant: str | None
    tx_date: date | None
    total: Decimal | None
    currency: str
    vat_rates: list[str]
    card_payment: bool
    confidence: float
    warnings: list[str]
    lines: ReceiptLines
    suggested_category: SuggestedCategory | None
    # Transactions already recorded from this receipt (to avoid recording it twice).
    recorded_transaction_ids: list[uuid.UUID]


# --- Budget summary --------------------------------------------------------------------------


class CategorySummary(BaseModel):
    category_id: uuid.UUID
    name: str
    kind: str
    archived: bool
    budget: Decimal | None
    spent: Decimal
    received: Decimal
    # budget - spent (negative when over budget); None without a budget.
    remaining: Decimal | None
    # spent / budget; None without a (non-zero) budget.
    ratio: float | None
    over_budget: bool
    transactions: int


class UncategorisedSummary(BaseModel):
    expenses: Decimal
    income: Decimal
    transactions: int


class SummaryTotals(BaseModel):
    expenses: Decimal
    income: Decimal
    # income - expenses
    balance: Decimal
    # Sum of the monthly budgets of the active expense categories.
    budget: Decimal
    # Expenses in categories that have a budget, and what is left of the total budget.
    budgeted_spent: Decimal
    budget_remaining: Decimal


class BudgetSummary(BaseModel):
    """Computed locally in SQL with exact decimals: works without any cloud access."""

    space_id: uuid.UUID
    month: str
    currency: str
    period_start: date
    period_end: date
    categories: list[CategorySummary]
    uncategorised: UncategorisedSummary
    totals: SummaryTotals
    # Transactions of the month in another currency, not converted nor counted above.
    other_currency_transactions: int


class BudgetAlert(BaseModel):
    category_id: uuid.UUID
    name: str
    budget: Decimal
    spent: Decimal
    ratio: float


class BudgetSnapshot(BaseModel):
    """Compact month budget for the dashboard (computed locally)."""

    space_id: uuid.UUID
    month: str
    currency: str
    spent: Decimal
    budget: Decimal
    # At most three categories at 80 % of their budget or more, the most consumed first.
    alerts: list[BudgetAlert]


def is_month(value: str) -> bool:
    return bool(_MONTH_RE.match(value))
