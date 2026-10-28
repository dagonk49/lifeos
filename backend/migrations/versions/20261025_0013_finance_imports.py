"""finance csv imports

Downgrade: lossy (the import history and the transactions' import keys, which keep a
re-imported statement from being added twice, cannot be kept by the older schema).

Revision ID: 0013
Revises: 0018
Create Date: 2026-10-25 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Bank statement imports (F04, task 25): a preview until the owner validates it.
    op.create_table(
        "finance_imports",
        sa.Column("status", sa.Text(), server_default="preview", nullable=False),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("file_sha256", sa.Text(), nullable=False),
        sa.Column("account_label", sa.Text(), nullable=True),
        sa.Column("encoding", sa.Text(), nullable=False),
        sa.Column("delimiter", sa.Text(), nullable=False),
        sa.Column("header", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("imported_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("edited_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
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
            "status IN ('preview', 'committed', 'undone')", name=op.f("ck_finance_imports_status")
        ),
        sa.CheckConstraint(
            "encoding IN ('utf-8', 'cp1252')", name=op.f("ck_finance_imports_encoding")
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_finance_imports_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_finance_imports_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finance_imports")),
    )
    op.create_index(
        "ix_finance_imports_space_created",
        "finance_imports",
        ["space_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_finance_imports_space_id"), "finance_imports", ["space_id"], unique=False
    )
    op.create_index(
        op.f("ix_finance_imports_user_id"), "finance_imports", ["user_id"], unique=False
    )
    op.create_table(
        "finance_import_rows",
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("cells", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("parsed", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("import_key", sa.Text(), nullable=True),
        sa.Column("match_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'valid', 'invalid', 'in_file_duplicate', 'already_imported',"
            " 'possible_duplicate')",
            name=op.f("ck_finance_import_rows_status"),
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["finance_imports.id"],
            name=op.f("fk_finance_import_rows_import_id_finance_imports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("import_id", "line_no", name=op.f("pk_finance_import_rows")),
    )
    # Imported operations: their batch (undo) and their identity (no double import).
    op.add_column("transactions", sa.Column("import_id", sa.Uuid(), nullable=True))
    op.add_column("transactions", sa.Column("import_key", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_transactions_import_id_finance_imports"),
        "transactions",
        "finance_imports",
        ["import_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_transactions_import_id"), "transactions", ["import_id"], unique=False)
    op.create_index(
        "uq_transactions_import_key",
        "transactions",
        ["space_id", "import_key"],
        unique=True,
        postgresql_where=sa.text("import_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_transactions_import_key",
        table_name="transactions",
        postgresql_where=sa.text("import_key IS NOT NULL"),
    )
    op.drop_index(op.f("ix_transactions_import_id"), table_name="transactions")
    op.drop_constraint(
        op.f("fk_transactions_import_id_finance_imports"), "transactions", type_="foreignkey"
    )
    op.drop_column("transactions", "import_key")
    op.drop_column("transactions", "import_id")
    op.drop_table("finance_import_rows")
    op.drop_index(op.f("ix_finance_imports_user_id"), table_name="finance_imports")
    op.drop_index(op.f("ix_finance_imports_space_id"), table_name="finance_imports")
    op.drop_index("ix_finance_imports_space_created", table_name="finance_imports")
    op.drop_table("finance_imports")
