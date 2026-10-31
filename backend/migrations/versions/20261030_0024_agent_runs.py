"""agent runs (coordinated agents: which agent read what, the synthesis built from it)

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-30 16:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Task 28: one row per coordinated run (UC03 « achat important »), purged after 90 days.
    # Its cloud call carries the run's id in llm_calls.correlation_id (no foreign key).
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("workflow", sa.Text(), nullable=False),
        sa.Column("question", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("advice", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("cloud_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("workflow IN ('purchase')", name=op.f("ck_agent_runs_workflow")),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_agent_runs_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_agent_runs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(op.f("ix_agent_runs_user_id"), "agent_runs", ["user_id"])
    op.create_index(op.f("ix_agent_runs_space_id"), "agent_runs", ["space_id"])
    op.create_index(op.f("ix_agent_runs_purge_after"), "agent_runs", ["purge_after"])


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_runs_purge_after"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_space_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_user_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
