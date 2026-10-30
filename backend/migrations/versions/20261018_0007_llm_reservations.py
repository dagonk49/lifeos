"""llm reservations

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOMES_BEFORE = "outcome IN ('ok', 'denied', 'budget_exceeded', 'error')"
OUTCOMES_AFTER = "outcome IN ('ok', 'denied', 'budget_exceeded', 'error', 'reserved', 'abandoned')"


def upgrade() -> None:
    # Quota reservations (ADR 0010): a row is written before the provider call and
    # settled afterwards with the real usage. correlation_id links a call to the run that
    # made it (filled by later features, no foreign key).
    op.add_column(
        "llm_calls",
        sa.Column("reserved_tokens", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "llm_calls",
        sa.Column(
            "reserved_cost_usd",
            sa.Numeric(precision=12, scale=6),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column("llm_calls", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("llm_calls", sa.Column("correlation_id", sa.Uuid(), nullable=True))
    op.drop_constraint(op.f("ck_llm_calls_outcome"), "llm_calls", type_="check")
    op.create_check_constraint("outcome", "llm_calls", OUTCOMES_AFTER)
    op.create_index(
        "ix_llm_calls_reserved",
        "llm_calls",
        ["user_id", "created_at"],
        unique=False,
        postgresql_where="outcome = 'reserved'",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_calls_reserved", table_name="llm_calls", postgresql_where="outcome = 'reserved'"
    )
    # The old schema knows neither state: a pending or released reservation becomes a
    # failed call, which still counts against the daily caps (it may have reached the
    # provider).
    op.execute(
        "UPDATE llm_calls SET outcome = 'error', "
        "error_code = COALESCE(error_code, 'llm_reservation_abandoned') "
        "WHERE outcome IN ('reserved', 'abandoned')"
    )
    op.drop_constraint(op.f("ck_llm_calls_outcome"), "llm_calls", type_="check")
    op.create_check_constraint("outcome", "llm_calls", OUTCOMES_BEFORE)
    op.drop_column("llm_calls", "correlation_id")
    op.drop_column("llm_calls", "settled_at")
    op.drop_column("llm_calls", "reserved_cost_usd")
    op.drop_column("llm_calls", "reserved_tokens")
