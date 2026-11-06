"""journal entry or review chosen as the subject of a chat message

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-04 10:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Task 24, continued: « Discuter de cette entrée » and « Discuter de ce bilan ».
    op.drop_constraint(op.f("ck_messages_context_type"), "messages", type_="check")
    op.create_check_constraint(
        op.f("ck_messages_context_type"),
        "messages",
        "context_type IN ('task', 'goal', 'journal', 'review')",
    )


def downgrade() -> None:
    # The older schema only knows tasks and goals: the messages stay, without their
    # reference to a journal entry or a review (both columns together, ck_messages_context_ref).
    op.execute(
        "UPDATE messages SET context_type = NULL, context_id = NULL"
        " WHERE context_type NOT IN ('task', 'goal')"
    )
    op.drop_constraint(op.f("ck_messages_context_type"), "messages", type_="check")
    op.create_check_constraint(
        op.f("ck_messages_context_type"), "messages", "context_type IN ('task', 'goal')"
    )
