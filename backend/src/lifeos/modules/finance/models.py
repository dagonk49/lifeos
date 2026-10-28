import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey, search_vector_column


class FinanceCategory(UUIDPrimaryKey, Owned, Timestamps, Base):
    """Spending or income category of one space, with an optional monthly budget (F04)."""

    __tablename__ = "finance_categories"
    __table_args__ = (
        UniqueConstraint("space_id", "name", name="uq_finance_categories_name"),
        CheckConstraint("kind IN ('expense', 'income')", name="kind"),
        CheckConstraint("monthly_budget IS NULL OR monthly_budget >= 0", name="budget"),
    )

    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, default="expense", server_default="expense")
    # Exact decimals, never binary floats (architecture §6).
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


IMPORT_STATUSES = ("preview", "committed", "undone")
IMPORT_ROW_STATUSES = (
    "pending",
    "valid",
    "invalid",
    "in_file_duplicate",
    "already_imported",
    "possible_duplicate",
)


class FinanceImport(UUIDPrimaryKey, Owned, Timestamps, Base):
    """One bank statement (CSV) import (task 25): a preview until the owner validates it.

    The file itself is never kept (only its SHA-256); its rows live in `finance_import_rows`
    while the preview lasts, and are erased at validation, cancellation or expiry.
    """

    __tablename__ = "finance_imports"
    __table_args__ = (
        CheckConstraint("status IN ('preview', 'committed', 'undone')", name="status"),
        CheckConstraint("encoding IN ('utf-8', 'cp1252')", name="encoding"),
        Index("ix_finance_imports_space_created", "space_id", "created_at"),
    )

    status: Mapped[str] = mapped_column(Text, default="preview", server_default="preview")
    filename: Mapped[str | None] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(Text)
    # Distinguishes two accounts imported into one space (part of the import key).
    account_label: Mapped[str | None] = mapped_column(Text)
    encoding: Mapped[str] = mapped_column(Text)
    delimiter: Mapped[str] = mapped_column(Text)
    # Column names only (never the preamble: account number, balance).
    header: Mapped[list[str]] = mapped_column(JSONB)
    mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    row_count: Mapped[int] = mapped_column(Integer)
    imported_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Operations of this import edited by the owner before the undo (lost corrections).
    edited_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    expires_at: Mapped[datetime]
    committed_at: Mapped[datetime | None]
    undone_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class FinanceImportRow(Base):
    """One data row of a previewed import: its cells and what they say under the mapping."""

    __tablename__ = "finance_import_rows"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'valid', 'invalid', 'in_file_duplicate', 'already_imported',"
            " 'possible_duplicate')",
            name="status",
        ),
    )

    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_imports.id", ondelete="CASCADE"), primary_key=True
    )
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    cells: Mapped[list[str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    # Read under the mapping (exact decimal string, ISO date...).
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    import_key: Mapped[str | None] = mapped_column(Text)
    # The manual or receipt transaction this row may duplicate (advisory, no foreign key:
    # the preview is recomputed and short-lived).
    match_id: Mapped[uuid.UUID | None]


class Transaction(UUIDPrimaryKey, Owned, Timestamps, Base):
    """One expense or income, entered manually, from a receipt or from a CSV import."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("direction IN ('expense', 'income')", name="direction"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
        CheckConstraint("source_type IN ('manual', 'receipt', 'import')", name="source_type"),
        Index("ix_transactions_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_transactions_space_date", "space_id", "tx_date"),
        # Importing the same bank operation twice into a space is impossible (task 25).
        Index(
            "uq_transactions_import_key",
            "space_id",
            "import_key",
            unique=True,
            postgresql_where=text("import_key IS NOT NULL"),
        ),
    )

    tx_date: Mapped[date]
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    direction: Mapped[str] = mapped_column(Text, default="expense", server_default="expense")
    currency: Mapped[str] = mapped_column(Text, default="EUR", server_default="EUR")
    merchant: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("finance_categories.id", ondelete="SET NULL"), index=True
    )
    # True when the category was chosen or confirmed by the owner (not by a rule).
    category_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source_type: Mapped[str] = mapped_column(Text, default="manual", server_default="manual")
    # The receipt document, when there is one.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    # The CSV import it came from, and the stable identity of the bank operation.
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("finance_imports.id", ondelete="SET NULL"), index=True
    )
    import_key: Mapped[str | None] = mapped_column(Text)
    # The recurring expense or income this operation pays (not counted twice in projections).
    recurring_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("finance_recurring.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    search_vector: Mapped[Any] = search_vector_column(
        "coalesce(merchant, '') || ' ' || coalesce(label, '') || ' ' || coalesce(notes, '')"
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class CategoryRule(UUIDPrimaryKey, Owned, Timestamps, Base):
    """Merchant → category, learned from the owner's corrections ("classer une dépense")."""

    __tablename__ = "finance_category_rules"
    __table_args__ = (
        UniqueConstraint("space_id", "merchant_key", name="uq_finance_category_rules_merchant"),
    )

    # Normalised merchant name (lowercase, no accents, no digits/punctuation).
    merchant_key: Mapped[str] = mapped_column(Text)
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("finance_categories.id", ondelete="CASCADE"), index=True
    )
    hits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


RECURRING_CADENCES = ("once", "weekly", "monthly", "quarterly", "yearly")


class FinanceRecurring(UUIDPrimaryKey, Owned, Timestamps, Base):
    """A recurring expense or income declared by the owner (rent, salary, subscription), or
    a planned one-off ("once": a big purchase), for the projections (task 26).

    A fixed amount is « prévu »; a variable one (`amount_is_estimate`) is « estimé », with an
    optional low-high range.
    """

    __tablename__ = "finance_recurring"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("direction IN ('expense', 'income')", name="direction"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
        CheckConstraint(
            "cadence IN ('once', 'weekly', 'monthly', 'quarterly', 'yearly')", name="cadence"
        ),
        CheckConstraint(
            "(amount_low IS NULL OR (amount_low > 0 AND amount_low <= amount))"
            " AND (amount_high IS NULL OR amount_high >= amount)",
            name="range",
        ),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="dates"),
    )

    label: Mapped[str] = mapped_column(Text)
    merchant: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    amount_is_estimate: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    amount_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    amount_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    direction: Mapped[str] = mapped_column(Text, default="expense", server_default="expense")
    currency: Mapped[str] = mapped_column(Text, default="EUR", server_default="EUR")
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("finance_categories.id", ondelete="SET NULL"), index=True
    )
    cadence: Mapped[str] = mapped_column(Text, default="monthly", server_default="monthly")
    start_date: Mapped[date]
    end_date: Mapped[date | None]
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
