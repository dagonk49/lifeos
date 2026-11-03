"""dashboard preferences (cards shown, their order, default view)

Revision ID: 0021
Revises: 0014
Create Date: 2026-10-30 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Task 23: the home page layout, one row per owner (no row: defaults).
    op.create_table(
        "dashboard_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "cards", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("default_view", sa.Text(), server_default="day", nullable=False),
        sa.Column("week_start", sa.Text(), server_default="monday", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "default_view IN ('day', 'week')", name=op.f("ck_dashboard_preferences_default_view")
        ),
        sa.CheckConstraint(
            "week_start IN ('monday', 'today')", name=op.f("ck_dashboard_preferences_week_start")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_dashboard_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_dashboard_preferences")),
    )


def downgrade() -> None:
    op.drop_table("dashboard_preferences")
