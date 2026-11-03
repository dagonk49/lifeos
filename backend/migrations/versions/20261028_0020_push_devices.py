"""web push devices and per-device deliveries

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-28 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Task 19 (ADR 0019): browsers subscribed to Web Push, one row per subscription.
    op.create_table(
        "push_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth_secret", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("preview", sa.Text(), server_default="generic", nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("preview IN ('generic', 'full')", name=op.f("ck_push_devices_preview")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_push_devices_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["user_sessions.id"],
            name=op.f("fk_push_devices_session_id_user_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["passkey_credentials.id"],
            name=op.f("fk_push_devices_credential_id_passkey_credentials"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_devices")),
        sa.UniqueConstraint("endpoint", name=op.f("uq_push_devices_endpoint")),
    )
    op.create_index(op.f("ix_push_devices_user_id"), "push_devices", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_push_devices_session_id"), "push_devices", ["session_id"], unique=False
    )
    op.create_index(
        op.f("ix_push_devices_credential_id"), "push_devices", ["credential_id"], unique=False
    )
    # The per-device sent marker: at most one delivery per (notification, device).
    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'expired', 'cancelled')",
            name=op.f("ck_push_deliveries_status"),
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            name=op.f("fk_push_deliveries_notification_id_notifications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["push_devices.id"],
            name=op.f("fk_push_deliveries_device_id_push_devices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_deliveries")),
        sa.UniqueConstraint("notification_id", "device_id", name="uq_push_deliveries_pair"),
    )
    op.create_index(
        op.f("ix_push_deliveries_device_id"), "push_deliveries", ["device_id"], unique=False
    )
    op.create_index(
        op.f("ix_push_deliveries_next_attempt_at"),
        "push_deliveries",
        ["next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_push_deliveries_created_at"), "push_deliveries", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_table("push_deliveries")
    op.drop_table("push_devices")
