"""notification preferences, digests and critical bypass permission

Revision ID: 0015
Revises: 0012
Create Date: 2026-10-25 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Task 21: explicit, revocable permission for critical alerts to bypass quiet hours.
    op.add_column(
        "users",
        sa.Column(
            "critical_bypass_quiet_hours",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    # Task 20: suggestions grouped in digests (an item points to its digest).
    op.add_column("notifications", sa.Column("digest_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_notifications_digest_id_notifications"),
        "notifications",
        "notifications",
        ["digest_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_notifications_digest_id"), "notifications", ["digest_id"], unique=False
    )
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("max_per_day", sa.Integer(), nullable=True),
        sa.Column(
            "windows", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column(
            "digest_times",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('important', 'suggestion')",
            name=op.f("ck_notification_preferences_category"),
        ),
        sa.CheckConstraint(
            "max_per_day IS NULL OR max_per_day BETWEEN 1 AND 100",
            name=op.f("ck_notification_preferences_max_per_day"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "category", name=op.f("pk_notification_preferences")),
    )


def downgrade() -> None:
    op.drop_table("notification_preferences")
    op.drop_index(op.f("ix_notifications_digest_id"), table_name="notifications")
    op.drop_constraint(
        op.f("fk_notifications_digest_id_notifications"), "notifications", type_="foreignkey"
    )
    op.drop_column("notifications", "digest_id")
    op.drop_column("users", "critical_bypass_quiet_hours")
