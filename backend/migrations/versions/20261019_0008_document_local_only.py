"""document local only

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # « Toujours garder en local »: the owner's refusal to send a document to the cloud,
    # independent of the local detection (documents.sensitivity) and never reset by it.
    op.add_column(
        "documents",
        sa.Column("local_only", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    # The old schema has no manual refusal: a document kept local by the owner would become
    # sendable again. Its protection is carried over as a detected category (the closest
    # the old schema knows), so that it still never leaves the server.
    op.execute(
        "UPDATE documents SET sensitivity = ARRAY['identity']::text[] "
        "WHERE local_only AND cardinality(sensitivity) = 0"
    )
    op.drop_column("documents", "local_only")
