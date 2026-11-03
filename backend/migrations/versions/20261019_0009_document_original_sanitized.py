"""document original sanitized

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A picture's original is served only once the stored file is the copy re-encoded from
    # its pixels (ADR 0011): the upload itself can hold its GPS position.
    op.add_column(
        "documents",
        sa.Column("original_sanitized", sa.Boolean(), server_default="false", nullable=False),
    )
    # Pictures already re-encoded: the worker audited each replacement (`sanitized`) when it
    # wrote it. One whose audit was purged is served again once read anew.
    op.execute(
        "UPDATE documents SET original_sanitized = true "
        "WHERE kind = 'image' AND storage_key IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM audit_events e WHERE e.action = 'document.process' "
        "AND e.target_type = 'document' AND e.target_id = documents.id::text "
        "AND e.details ->> 'sanitized' = 'true')"
    )


def downgrade() -> None:
    op.drop_column("documents", "original_sanitized")
