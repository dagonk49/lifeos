import uuid
from datetime import date
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
)
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


class Transaction(UUIDPrimaryKey, Owned, Timestamps, Base):
    """One expense or income, entered manually, from a receipt or (later) an import."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("direction IN ('expense', 'income')", name="direction"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
        CheckConstraint("source_type IN ('manual', 'receipt', 'import')", name="source_type"),
        Index("ix_transactions_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_transactions_space_date", "space_id", "tx_date"),
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
