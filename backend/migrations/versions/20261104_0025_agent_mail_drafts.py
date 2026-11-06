"""e-mail drafts proposed by an agent (author, conversation, owner's first edit)

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-04 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirror of guardian/models.py AGENTS at this revision.
_AGENTS = ("core", "planning", "memory", "coaching", "finance", "projects", "documents")


def upgrade() -> None:
    # ADR 0018 (amendment): an agent may write a draft, never send it. The draft says which
    # agent proposed it and from which conversation; once the owner edited it (or prepared
    # its sending), the agent can no longer change it.
    op.add_column("mail_drafts", sa.Column("author_agent", sa.Text(), nullable=True))
    op.add_column("mail_drafts", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.add_column(
        "mail_drafts", sa.Column("owner_edited_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_mail_drafts_author_agent"),
        "mail_drafts",
        "author_agent IN (" + ", ".join(f"'{a}'" for a in _AGENTS) + ")",
    )
    op.create_foreign_key(
        op.f("fk_mail_drafts_conversation_id_conversations"),
        "mail_drafts",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_mail_drafts_conversation_id"), "mail_drafts", ["conversation_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mail_drafts_conversation_id"), table_name="mail_drafts")
    op.drop_constraint(
        op.f("fk_mail_drafts_conversation_id_conversations"), "mail_drafts", type_="foreignkey"
    )
    op.drop_constraint(op.f("ck_mail_drafts_author_agent"), "mail_drafts", type_="check")
    op.drop_column("mail_drafts", "owner_edited_at")
    op.drop_column("mail_drafts", "conversation_id")
    op.drop_column("mail_drafts", "author_agent")
