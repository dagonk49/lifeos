"""mail

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-25 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Change cursor of a mapped mail label (Gmail historyId) after its last full pass.
    op.add_column("connector_sources", sa.Column("sync_cursor", sa.Text(), nullable=True))
    # Third-party files (e-mail attachments): sender kept on the document, PDF warnings.
    op.add_column("documents", sa.Column("received_from", sa.Text(), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "active_content",
            postgresql.ARRAY(sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.create_table(
        "mail_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("provider_message_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("from_address", sa.Text(), nullable=True),
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column("to_addresses", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("cc_addresses", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_truncated", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attachments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sensitivity", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_generation", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["connector_sources.id"],
            name=op.f("fk_mail_messages_source_id_connector_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_mail_messages_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_mail_messages_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_messages")),
        sa.UniqueConstraint("source_id", "provider_message_id", name="uq_mail_messages_provider"),
    )
    op.create_index(op.f("ix_mail_messages_source_id"), "mail_messages", ["source_id"])
    op.create_index(op.f("ix_mail_messages_space_id"), "mail_messages", ["space_id"])
    op.create_index(op.f("ix_mail_messages_user_id"), "mail_messages", ["user_id"])
    op.create_index(op.f("ix_mail_messages_purge_after"), "mail_messages", ["purge_after"])
    op.create_index("ix_mail_messages_space_received", "mail_messages", ["space_id", "received_at"])


def downgrade() -> None:
    op.drop_table("mail_messages")
    op.drop_column("documents", "active_content")
    op.drop_column("documents", "received_from")
    op.drop_column("connector_sources", "sync_cursor")
