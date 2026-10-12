"""chat attachments

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12 11:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "attachments", postgresql.ARRAY(sa.Uuid()), server_default="{}", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "attachments")
