"""task dependencies and tags

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-25 09:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Classification of tasks (F03, task 22): free labels, searched with @> (GIN).
    op.add_column(
        "tasks",
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
    )
    op.create_index("ix_tasks_tags", "tasks", ["tags"], unique=False, postgresql_using="gin")
    # Dependencies: task_id waits for depends_on_id (same space, acyclic graph).
    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("depends_on_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("task_id <> depends_on_id", name=op.f("ck_task_dependencies_not_self")),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_task_dependencies_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_id"],
            ["tasks.id"],
            name=op.f("fk_task_dependencies_depends_on_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_task_dependencies_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_dependencies_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "depends_on_id", name=op.f("pk_task_dependencies")),
    )
    op.create_index(
        op.f("ix_task_dependencies_depends_on_id"),
        "task_dependencies",
        ["depends_on_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_task_dependencies_space_id"), "task_dependencies", ["space_id"], unique=False
    )
    op.create_index(
        op.f("ix_task_dependencies_user_id"), "task_dependencies", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_task_dependencies_user_id"), table_name="task_dependencies")
    op.drop_index(op.f("ix_task_dependencies_space_id"), table_name="task_dependencies")
    op.drop_index(op.f("ix_task_dependencies_depends_on_id"), table_name="task_dependencies")
    op.drop_table("task_dependencies")
    op.drop_index("ix_tasks_tags", table_name="tasks", postgresql_using="gin")
    op.drop_column("tasks", "tags")
