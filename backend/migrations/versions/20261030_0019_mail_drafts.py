"""mail drafts (sending under approval, task 18)

Revision ID: 0019
Revises: 0021
Create Date: 2026-10-30 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # What a reply refers to (In-Reply-To, References); unknown for older copies.
    op.add_column("mail_messages", sa.Column("message_id_header", sa.Text(), nullable=True))
    op.create_table(
        "mail_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("to_addresses", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("cc_addresses", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("bcc_addresses", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("attachment_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("in_reply_to_id", sa.Uuid(), nullable=True),
        sa.Column("reply_message_id_header", sa.Text(), nullable=True),
        sa.Column("reply_thread_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("message_id_header", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("provider_thread_id", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
        sa.CheckConstraint(
            "status IN ('draft', 'awaiting_approval', 'sent')",
            name=op.f("ck_mail_drafts_status"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["connector_accounts.id"],
            name=op.f("fk_mail_drafts_account_id_connector_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.id"],
            name=op.f("fk_mail_drafts_approval_id_approvals"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["in_reply_to_id"],
            ["mail_messages.id"],
            name=op.f("fk_mail_drafts_in_reply_to_id_mail_messages"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_mail_drafts_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mail_drafts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_drafts")),
        sa.UniqueConstraint("message_id_header", name=op.f("uq_mail_drafts_message_id_header")),
    )
    op.create_index(op.f("ix_mail_drafts_user_id"), "mail_drafts", ["user_id"])
    op.create_index(op.f("ix_mail_drafts_space_id"), "mail_drafts", ["space_id"])
    op.create_index(op.f("ix_mail_drafts_account_id"), "mail_drafts", ["account_id"])
    op.create_index(op.f("ix_mail_drafts_sent_at"), "mail_drafts", ["sent_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_drafts_sent_at"), table_name="mail_drafts")
    op.drop_index(op.f("ix_mail_drafts_account_id"), table_name="mail_drafts")
    op.drop_index(op.f("ix_mail_drafts_space_id"), table_name="mail_drafts")
    op.drop_index(op.f("ix_mail_drafts_user_id"), table_name="mail_drafts")
    op.drop_table("mail_drafts")
    op.drop_column("mail_messages", "message_id_header")
