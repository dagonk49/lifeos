import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # The recurring item this operation pays (same space and direction), task 26.
    recurring_id: uuid.UUID | None = None

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
    # Links the operation to a recurring item (null unlinks it).
    recurring_id: uuid.UUID | None = None

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
    import_id: uuid.UUID | None
    recurring_id: uuid.UUID | None
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


# --- Bank statement imports (task 25) --------------------------------------------------------

DateFormat = Literal["DD/MM/YYYY", "DD/MM/YY", "YYYY-MM-DD", "DD.MM.YYYY", "DD-MM-YYYY"]
ImportRowStatus = Literal[
    "pending", "valid", "invalid", "in_file_duplicate", "already_imported", "possible_duplicate"
]


class ImportUploadFields(BaseModel):
    """Form fields of `POST /finance/imports` (multipart, beside the file)."""

    space_id: uuid.UUID
    account_label: str | None = Field(default=None, max_length=60)
    # "auto": UTF-8 if the file decodes as such, else Windows-1252 (French bank exports).
    encoding: Literal["auto", "utf-8", "cp1252"] = "auto"

    @field_validator("account_label")
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        return _clean(value)


class ImportFingerprint(ImportUploadFields):
    """What an Idempotency-Key is bound to: the fields and the file's SHA-256."""

    sha256: str


class ImportMapping(BaseModel):
    """Which column says what (0-based column indexes of the header)."""

    date_column: int = Field(ge=0, lt=40)
    date_format: DateFormat = "DD/MM/YYYY"
    label_column: int = Field(ge=0, lt=40)
    amount_mode: Literal["signed", "debit_credit"] = "signed"
    amount_column: int | None = Field(default=None, ge=0, lt=40)
    debit_column: int | None = Field(default=None, ge=0, lt=40)
    credit_column: int | None = Field(default=None, ge=0, lt=40)
    decimal_separator: Literal[",", "."] = ","
    reference_column: int | None = Field(default=None, ge=0, lt=40)
    currency_column: int | None = Field(default=None, ge=0, lt=40)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def _columns(self) -> "ImportMapping":
        if self.amount_mode == "signed" and self.amount_column is None:
            raise ValueError("amount_column is required for a signed amount")
        if self.amount_mode == "debit_credit" and (
            self.debit_column is None or self.credit_column is None
        ):
            raise ValueError("debit_column and credit_column are required")
        return self


class ImportTotals(BaseModel):
    """Exact sums of the operations that would be imported (or were)."""

    expenses: Decimal
    income: Decimal
    # Operations in another currency are counted but not summed.
    other_currency: int


class ImportOut(BaseModel):
    id: uuid.UUID
    space_id: uuid.UUID
    status: str
    filename: str | None
    account_label: str | None
    encoding: str
    delimiter: str
    header: list[str]
    # First data rows as read (preview only): to check the decoding and the columns.
    sample: list[list[str]]
    suggested_mapping: ImportMapping | None
    mapping: ImportMapping | None
    row_count: int
    # Rows per status, once mapped (preview only).
    counts: dict[str, int]
    # What « Importer » adds with the default choices (preview only).
    to_import: int
    totals: ImportTotals | None
    imported_count: int
    skipped_count: int
    edited_count: int
    # The same file was already imported into this space (by its SHA-256).
    already_imported_file: bool
    expires_at: datetime
    committed_at: datetime | None
    undone_at: datetime | None
    version: int
    created_at: datetime


class ImportRowOut(BaseModel):
    line_no: int
    status: ImportRowStatus
    # Why the row cannot be imported (stable code: date_invalid, amount_precision...).
    reason: str | None
    cells: list[str]
    tx_date: date | None
    amount: Decimal | None
    direction: Direction | None
    currency: str | None
    label: str | None
    merchant: str | None
    category_id: uuid.UUID | None
    # The manual or receipt transaction this row may duplicate.
    match_id: uuid.UUID | None
    match_date: date | None


class ImportCommit(BaseModel):
    """Lines to leave out (valid ones) and lines to import anyway (possible duplicates)."""

    exclude_lines: list[int] = Field(default_factory=list, max_length=50_000)
    include_lines: list[int] = Field(default_factory=list, max_length=50_000)


class ImportUndo(BaseModel):
    # The owner confirmed by name (« Annuler cet import ») in the interface.
    confirm: Literal[True]


# --- Recurring items and projections (task 26) -----------------------------------------------

Cadence = Literal["once", "weekly", "monthly", "quarterly", "yearly"]
Basis = Literal["known", "scheduled", "estimated"]


class RecurringBase(BaseModel):
    @field_validator("label", "merchant", check_fields=False)
    @classmethod
    def _line(cls, value: str | None) -> str | None:
        return _clean(value)


class RecurringCreate(RecurringBase):
    space_id: uuid.UUID
    label: str = Field(min_length=1, max_length=120)
    merchant: str | None = Field(default=None, max_length=120)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    # A variable amount (electricity, groceries): « estimé », between low and high.
    amount_is_estimate: bool = False
    amount_low: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    amount_high: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    direction: Direction = "expense"
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    category_id: uuid.UUID | None = None
    cadence: Cadence = "monthly"
    start_date: date
    end_date: date | None = None


class RecurringUpdate(RecurringBase):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    merchant: str | None = Field(default=None, max_length=120)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    amount_is_estimate: bool | None = None
    amount_low: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    amount_high: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    direction: Direction | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    category_id: uuid.UUID | None = None
    cadence: Cadence | None = None
    start_date: date | None = None
    end_date: date | None = None
    active: bool | None = None


class RecurringOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    label: str
    merchant: str | None
    amount: Decimal
    amount_is_estimate: bool
    amount_low: Decimal | None
    amount_high: Decimal | None
    direction: str
    currency: str
    category_id: uuid.UUID | None
    cadence: str
    start_date: date
    end_date: date | None
    active: bool
    # Next occurrence from today (None once ended).
    next_date: date | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class ProjectionItem(BaseModel):
    """One line of a projected month, tagged with what it rests on."""

    label: str
    direction: Direction
    # known: recorded operations; scheduled: a fixed recurring amount; estimated: a
    # variable recurring amount or the average of past months (with its range).
    basis: Basis
    source: Literal["transactions", "recurring", "baseline"]
    amount: Decimal
    low: Decimal
    high: Decimal
    occurrences: int = 0
    recurring_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None


class ProjectionMonth(BaseModel):
    month: str
    state: Literal["current", "future"]
    known_income: Decimal
    known_expenses: Decimal
    scheduled_income: Decimal
    scheduled_expenses: Decimal
    estimated_income: Decimal
    estimated_income_low: Decimal
    estimated_income_high: Decimal
    estimated_expenses: Decimal
    estimated_expenses_low: Decimal
    estimated_expenses_high: Decimal
    # Income minus expenses of the month (no starting balance: no bank connection).
    balance: Decimal
    balance_low: Decimal
    balance_high: Decimal
    # Sum of the balances from the current month to this one.
    cumulative: Decimal
    cumulative_low: Decimal
    cumulative_high: Decimal
    items: list[ProjectionItem]


class SavingsGoalProjection(BaseModel):
    goal_id: uuid.UUID
    title: str
    currency: str
    target: Decimal
    # Known: the last value recorded on the goal (check-in), never estimated.
    saved: Decimal
    saved_at: date | None
    remaining: Decimal
    reached: bool
    contribution: Decimal | None
    # declared: the owner's monthly saving; estimated: the projected monthly balance.
    contribution_source: Literal["declared", "estimated", "none"]
    eta_month: str | None
    eta_early: str | None
    eta_late: str | None
    due_date: date | None
    on_track: bool | None


class ProjectionNote(BaseModel):
    code: str
    label: str
    value: str | None = None


class ProjectionBaseline(BaseModel):
    months: int
    start: str | None
    end: str | None


class FinanceProjection(BaseModel):
    """Computed locally on every read (never stored, never by the LLM): it follows each
    correction of an operation, a recurring item or a goal."""

    space_id: uuid.UUID
    currency: str
    today: date
    horizon: int
    baseline: ProjectionBaseline
    months: list[ProjectionMonth]
    # None when the principal may not read the goals of the space.
    savings_goals: list[SavingsGoalProjection] | None
    assumptions: list[ProjectionNote]
    warnings: list[ProjectionNote]
