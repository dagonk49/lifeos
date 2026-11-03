"""calendar

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-24 20:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sync state of a mapped calendar (task 14): lease, pass number, page to resume from.
    op.add_column(
        "connector_sources",
        sa.Column("sync_generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("connector_sources", sa.Column("page_token", sa.Text(), nullable=True))
    op.add_column(
        "connector_sources",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    # Occurrences of the mapped calendars, in the space of their source.
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("series_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=True),
        sa.Column("busy", sa.Boolean(), nullable=False),
        sa.Column("organizer_self", sa.Boolean(), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("attendees", sa.Integer(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('confirmed', 'tentative')", name=op.f("ck_calendar_events_status")
        ),
        sa.CheckConstraint(
            "(starts_at IS NOT NULL AND ends_at IS NOT NULL AND start_date IS NULL "
            "AND end_date IS NULL) OR (starts_at IS NULL AND ends_at IS NULL "
            "AND start_date IS NOT NULL AND end_date IS NOT NULL)",
            name=op.f("ck_calendar_events_when"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["connector_sources.id"],
            name=op.f("fk_calendar_events_source_id_connector_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_calendar_events_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_calendar_events_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_events")),
        sa.UniqueConstraint("source_id", "provider_event_id", name="uq_calendar_events_provider"),
    )
    op.create_index(
        op.f("ix_calendar_events_source_id"), "calendar_events", ["source_id"], unique=False
    )
    op.create_index(
        op.f("ix_calendar_events_space_id"), "calendar_events", ["space_id"], unique=False
    )
    op.create_index(
        op.f("ix_calendar_events_user_id"), "calendar_events", ["user_id"], unique=False
    )
    op.create_index("ix_calendar_events_space_starts", "calendar_events", ["space_id", "starts_at"])
    op.create_index("ix_calendar_events_space_dates", "calendar_events", ["space_id", "start_date"])


def downgrade() -> None:
    op.drop_table("calendar_events")
    op.drop_column("connector_sources", "lease_until")
    op.drop_column("connector_sources", "page_token")
    op.drop_column("connector_sources", "sync_generation")
