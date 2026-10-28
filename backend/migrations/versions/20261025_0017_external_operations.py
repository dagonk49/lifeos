"""approvals bound to their parameters, external operations

Revision ID: 0017
Revises: 0015
Create Date: 2026-10-25 18:00:00.000000+00:00
"""

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "('pending', 'approved', 'rejected', 'expired', 'executed', 'failed')"
_STATUSES_NEW = (
    "('pending', 'approved', 'rejected', 'expired', 'executed', 'failed', 'invalidated')"
)


def _digest(params: object) -> str:
    # Same canonical form as lifeos.modules.guardian.actions.params_digest.
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def upgrade() -> None:
    # Approved parameters are bound by their digest (ADR 0016): existing rows get theirs.
    op.add_column("approvals", sa.Column("params_sha256", sa.Text(), nullable=True))
    op.add_column("approvals", sa.Column("target", sa.Text(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, params FROM approvals")).all()
    for approval_id, params in rows:
        conn.execute(
            sa.text("UPDATE approvals SET params_sha256 = :digest WHERE id = :id"),
            {"digest": _digest(params), "id": approval_id},
        )
    # Stays nullable: the previous version, which does not know it, may still insert (a
    # missing digest never matches: such an approval is refused at execution).
    op.drop_constraint(op.f("ck_approvals_status"), "approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_approvals_status"), "approvals", f"status IN {_STATUSES_NEW}"
    )
    op.create_index(
        "ix_approvals_pending_target",
        "approvals",
        ["user_id", "target"],
        unique=False,
        postgresql_where="status = 'pending'",
    )
    # One claim per approval, committed before the provider is called.
    op.create_table(
        "external_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('claimed', 'succeeded', 'unknown', 'failed')",
            name=op.f("ck_external_operations_state"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["connector_accounts.id"],
            name=op.f("fk_external_operations_account_id_connector_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_external_operations_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_external_operations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_operations")),
        sa.UniqueConstraint("approval_id", name=op.f("uq_external_operations_approval_id")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_external_operations_idempotency_key")),
    )
    op.create_index(op.f("ix_external_operations_space_id"), "external_operations", ["space_id"])
    op.create_index(op.f("ix_external_operations_state"), "external_operations", ["state"])
    op.create_index(op.f("ix_external_operations_user_id"), "external_operations", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_external_operations_user_id"), table_name="external_operations")
    op.drop_index(op.f("ix_external_operations_state"), table_name="external_operations")
    op.drop_index(op.f("ix_external_operations_space_id"), table_name="external_operations")
    op.drop_table("external_operations")
    op.drop_index("ix_approvals_pending_target", table_name="approvals")
    # An invalidated proposal can no longer be approved: expired is its closest status.
    op.execute("UPDATE approvals SET status = 'expired' WHERE status = 'invalidated'")
    op.drop_constraint(op.f("ck_approvals_status"), "approvals", type_="check")
    op.create_check_constraint(op.f("ck_approvals_status"), "approvals", f"status IN {_STATUSES}")
    op.drop_column("approvals", "target")
    op.drop_column("approvals", "params_sha256")
