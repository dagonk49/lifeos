"""task or goal chosen as the subject of a chat message

Revision ID: 0023
Revises: 0020
Create Date: 2026-10-30 15:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Task 24: the object the owner explicitly chose (« Discuter de cet objectif »).
    # References only, without foreign keys: a deleted object stays referenced, like
    # `messages.attachments`.
    op.add_column("messages", sa.Column("context_type", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("context_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        op.f("ck_messages_context_type"), "messages", "context_type IN ('task', 'goal')"
    )
    op.create_check_constraint(
        op.f("ck_messages_context_ref"),
        "messages",
        "(context_type IS NULL) = (context_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_messages_context_ref"), "messages", type_="check")
    op.drop_constraint(op.f("ck_messages_context_type"), "messages", type_="check")
    op.drop_column("messages", "context_id")
    op.drop_column("messages", "context_type")
