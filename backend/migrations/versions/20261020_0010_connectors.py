"""connectors

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESOURCES_V4 = (
    "('tasks', 'journal', 'facts', 'memories', 'messages', 'goals', 'reviews', "
    "'documents', 'finances')"
)
_RESOURCES_V5 = (
    "('tasks', 'journal', 'facts', 'memories', 'messages', 'goals', 'reviews', "
    "'documents', 'finances', 'calendar', 'email')"
)


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
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
    ]


def upgrade() -> None:
    # Connected accounts (ADR 0012): tokens sealed with the connectors key, never in clear.
    op.create_table(
        "connector_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "provider IN ('google', 'fake')", name=op.f("ck_connector_accounts_provider")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'reauth_required', 'revoked')",
            name=op.f("ck_connector_accounts_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_connector_accounts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_accounts")),
        sa.UniqueConstraint("user_id", "provider", "subject", name="uq_connector_accounts_subject"),
    )
    op.create_index(
        op.f("ix_connector_accounts_user_id"), "connector_accounts", ["user_id"], unique=False
    )

    # Consent per account, space and capability.
    op.create_table(
        "connector_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "capability IN ('calendar.read', 'calendar.write', 'mail.read', 'mail.send')",
            name=op.f("ck_connector_grants_capability"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["connector_accounts.id"],
            name=op.f("fk_connector_grants_account_id_connector_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_connector_grants_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_connector_grants_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_grants")),
    )
    op.create_index(
        op.f("ix_connector_grants_account_id"), "connector_grants", ["account_id"], unique=False
    )
    op.create_index(
        op.f("ix_connector_grants_space_id"), "connector_grants", ["space_id"], unique=False
    )
    op.create_index(
        op.f("ix_connector_grants_user_id"), "connector_grants", ["user_id"], unique=False
    )
    op.create_index(
        "uq_connector_grants_active",
        "connector_grants",
        ["account_id", "space_id", "capability"],
        unique=True,
        postgresql_where="revoked_at IS NULL",
    )

    # Calendars and mail labels mapped to a space (sync state added by later tasks).
    op.create_table(
        "connector_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('calendar', 'mail_label')", name=op.f("ck_connector_sources_kind")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["connector_accounts.id"],
            name=op.f("fk_connector_sources_account_id_connector_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_connector_sources_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_connector_sources_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_sources")),
        sa.UniqueConstraint("account_id", "kind", "provider_ref", name="uq_connector_sources_ref"),
    )
    op.create_index(
        op.f("ix_connector_sources_account_id"), "connector_sources", ["account_id"], unique=False
    )
    op.create_index(
        op.f("ix_connector_sources_space_id"), "connector_sources", ["space_id"], unique=False
    )
    op.create_index(
        op.f("ix_connector_sources_user_id"), "connector_sources", ["user_id"], unique=False
    )

    # Consents in progress: state hashed, PKCE verifier sealed, single use.
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("verifier_enc", sa.LargeBinary(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("capabilities", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("reconnect", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["connector_accounts.id"],
            name=op.f("fk_oauth_states_account_id_connector_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            ["spaces.id"],
            name=op.f("fk_oauth_states_space_id_spaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_oauth_states_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_states")),
        sa.UniqueConstraint("state_hash", name=op.f("uq_oauth_states_state_hash")),
    )
    op.create_index(
        op.f("ix_oauth_states_expires_at"), "oauth_states", ["expires_at"], unique=False
    )
    op.create_index(op.f("ix_oauth_states_user_id"), "oauth_states", ["user_id"], unique=False)

    # Agents need an explicit permission on calendar and e-mail data as well.
    op.drop_constraint(op.f("ck_agent_permissions_resource"), "agent_permissions", type_="check")
    op.create_check_constraint("resource", "agent_permissions", f"resource IN {_RESOURCES_V5}")


def downgrade() -> None:
    op.execute("DELETE FROM agent_permissions WHERE resource IN ('calendar', 'email')")
    op.drop_constraint(op.f("ck_agent_permissions_resource"), "agent_permissions", type_="check")
    op.create_check_constraint("resource", "agent_permissions", f"resource IN {_RESOURCES_V4}")
    op.drop_index(op.f("ix_oauth_states_user_id"), table_name="oauth_states")
    op.drop_index(op.f("ix_oauth_states_expires_at"), table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_index(op.f("ix_connector_sources_user_id"), table_name="connector_sources")
    op.drop_index(op.f("ix_connector_sources_space_id"), table_name="connector_sources")
    op.drop_index(op.f("ix_connector_sources_account_id"), table_name="connector_sources")
    op.drop_table("connector_sources")
    op.drop_index(
        "uq_connector_grants_active",
        table_name="connector_grants",
        postgresql_where="revoked_at IS NULL",
    )
    op.drop_index(op.f("ix_connector_grants_user_id"), table_name="connector_grants")
    op.drop_index(op.f("ix_connector_grants_space_id"), table_name="connector_grants")
    op.drop_index(op.f("ix_connector_grants_account_id"), table_name="connector_grants")
    op.drop_table("connector_grants")
    op.drop_index(op.f("ix_connector_accounts_user_id"), table_name="connector_accounts")
    op.drop_table("connector_accounts")
