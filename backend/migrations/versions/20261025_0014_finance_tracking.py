"""recurring expenses and savings goals

Downgrade: lossy (the recurring items, the operations' links to them and the savings goals'
currency and monthly contribution cannot be kept by the older schema).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-25 15:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Recurring (or planned one-off) expenses and incomes, for the projections (task 26).
    op.create_table(
        "finance_recurring",
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("merchant", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("amount_is_estimate", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("amount_low", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("amount_high", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("direction", sa.Text(), server_default="expense", nullable=False),
        sa.Column("currency", sa.Text(), server_default="EUR", nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("cadence", sa.Text(), server_default="monthly", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_finance_recurring_amount")),
        sa.CheckConstraint(
            "direction IN ('expense', 'income')", name=op.f("ck_finance_recurring_direction")
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_finance_recurring_currency")),
        sa.CheckConstraint(
            "cadence IN ('once', 'weekly', 'monthly', 'quarterly', 'yearly')",
            name=op.f("ck_finance_recurring_cadence"),
        ),
        sa.CheckConstraint(
            "(amount_low IS NULL OR (amount_low > 0 AND amount_low <= amount))"
            " AND (amount_high IS NULL OR amount_high >= amount)",
            name=op.f("ck_finance_recurring_range"),
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name=op.f("ck_finance_recurring_dates")
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["finance_categories.id"],
            name=op.f("fk_finance_recurring_category_id_finance_categories"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_finance_recurring_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_finance_recurring_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finance_recurring")),
    )
    op.create_index(
        op.f("ix_finance_recurring_category_id"), "finance_recurring", ["category_id"], unique=False
    )
    op.create_index(
        op.f("ix_finance_recurring_space_id"), "finance_recurring", ["space_id"], unique=False
    )
    op.create_index(
        op.f("ix_finance_recurring_user_id"), "finance_recurring", ["user_id"], unique=False
    )
    op.add_column("transactions", sa.Column("recurring_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_transactions_recurring_id_finance_recurring"),
        "transactions",
        "finance_recurring",
        ["recurring_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_transactions_recurring_id"), "transactions", ["recurring_id"], unique=False
    )
    # Savings goals: a measurable goal counted in a currency, with a planned contribution.
    op.add_column("goals", sa.Column("currency", sa.Text(), nullable=True))
    op.add_column(
        "goals",
        sa.Column("monthly_contribution", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_goals_currency"), "goals", "currency IS NULL OR currency ~ '^[A-Z]{3}$'"
    )
    op.create_check_constraint(
        op.f("ck_goals_monthly_contribution"),
        "goals",
        "monthly_contribution IS NULL OR monthly_contribution >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_goals_monthly_contribution"), "goals", type_="check")
    op.drop_constraint(op.f("ck_goals_currency"), "goals", type_="check")
    op.drop_column("goals", "monthly_contribution")
    op.drop_column("goals", "currency")
    op.drop_index(op.f("ix_transactions_recurring_id"), table_name="transactions")
    op.drop_constraint(
        op.f("fk_transactions_recurring_id_finance_recurring"), "transactions", type_="foreignkey"
    )
    op.drop_column("transactions", "recurring_id")
    op.drop_index(op.f("ix_finance_recurring_user_id"), table_name="finance_recurring")
    op.drop_index(op.f("ix_finance_recurring_space_id"), table_name="finance_recurring")
    op.drop_index(op.f("ix_finance_recurring_category_id"), table_name="finance_recurring")
    op.drop_table("finance_recurring")
