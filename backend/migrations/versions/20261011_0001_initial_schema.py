"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-11 01:56:24.084641+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Append-only audit trail. DELETE/TRUNCATE are only allowed for the retention purge, which
# sets `lifeos.audit_purge = on` for its transaction.
AUDIT_GUARD = [
    """
    CREATE FUNCTION lifeos_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP IN ('DELETE', 'TRUNCATE')
           AND current_setting('lifeos.audit_purge', true) = 'on' THEN
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NULL;
        END IF;
        RAISE EXCEPTION 'audit_events is append-only (% refused)', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END;
    $$
    """,
    "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION lifeos_audit_guard()",
    "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION lifeos_audit_guard()",
    "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION lifeos_audit_guard()",
]

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Creating `vector` needs a superuser; in Docker it is created by the init script, so
    # this is a no-op there. `unaccent` is a trusted extension.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    # French full-text search, accent-insensitive (docs/modele-de-donnees.md).
    op.execute("CREATE TEXT SEARCH CONFIGURATION lifeos_fr (COPY = french)")
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION lifeos_fr "
        "ALTER MAPPING FOR hword, hword_part, word WITH unaccent, french_stem"
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("space_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'agent', 'system', 'anonymous')",
            name=op.f("ck_audit_events_actor_type"),
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'denied', 'failure')", name=op.f("ck_audit_events_outcome")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"], unique=False)
    op.create_index(
        op.f("ix_audit_events_occurred_at"), "audit_events", ["occurred_at"], unique=False
    )
    op.create_index(op.f("ix_audit_events_user_id"), "audit_events", ["user_id"], unique=False)
    op.create_table(
        "users",
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("locale", sa.Text(), server_default="fr-FR", nullable=False),
        sa.Column("timezone", sa.Text(), server_default="Europe/Paris", nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint("role IN ('owner')", name=op.f("ck_users_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "enrollment_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('bootstrap', 'recovery')", name=op.f("ck_enrollment_tokens_purpose")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_enrollment_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrollment_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_enrollment_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_enrollment_tokens_user_id"), "enrollment_tokens", ["user_id"], unique=False
    )
    op.create_table(
        "passkey_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False),
        sa.Column("transports", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("aaguid", sa.Text(), nullable=False),
        sa.Column("backup_eligible", sa.Boolean(), nullable=False),
        sa.Column("backup_state", sa.Boolean(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_passkey_credentials_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_passkey_credentials")),
        sa.UniqueConstraint("credential_id", name=op.f("uq_passkey_credentials_credential_id")),
    )
    op.create_index(
        op.f("ix_passkey_credentials_user_id"), "passkey_credentials", ["user_id"], unique=False
    )
    op.create_table(
        "spaces",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint("kind IN ('personal', 'professional')", name=op.f("ck_spaces_kind")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_spaces_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spaces")),
        sa.UniqueConstraint("user_id", "name", name="uq_spaces_user_id_name"),
    )
    op.create_index(op.f("ix_spaces_user_id"), "spaces", ["user_id"], unique=False)
    op.create_table(
        "auth_challenges",
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("enrollment_token_id", sa.Uuid(), nullable=True),
        sa.Column("challenge", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('registration', 'authentication')", name=op.f("ck_auth_challenges_purpose")
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_token_id"],
            ["enrollment_tokens.id"],
            name=op.f("fk_auth_challenges_enrollment_token_id_enrollment_tokens"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_challenges_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_challenges")),
    )
    op.create_index(
        op.f("ix_auth_challenges_expires_at"), "auth_challenges", ["expires_at"], unique=False
    )
    op.create_table(
        "user_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["passkey_credentials.id"],
            name=op.f("fk_user_sessions_credential_id_passkey_credentials"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_sessions_token_hash")),
    )
    op.create_index(
        op.f("ix_user_sessions_expires_at"), "user_sessions", ["expires_at"], unique=False
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)
    for statement in AUDIT_GUARD:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER audit_events_no_delete ON audit_events")
    op.execute("DROP TRIGGER audit_events_no_update ON audit_events")
    op.execute("DROP FUNCTION lifeos_audit_guard()")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_expires_at"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index(op.f("ix_auth_challenges_expires_at"), table_name="auth_challenges")
    op.drop_table("auth_challenges")
    op.drop_index(op.f("ix_spaces_user_id"), table_name="spaces")
    op.drop_table("spaces")
    op.drop_index(op.f("ix_passkey_credentials_user_id"), table_name="passkey_credentials")
    op.drop_table("passkey_credentials")
    op.drop_index(op.f("ix_enrollment_tokens_user_id"), table_name="enrollment_tokens")
    op.drop_table("enrollment_tokens")
    op.drop_table("users")
    op.drop_index(op.f("ix_audit_events_user_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_table("audit_events")
    op.execute("DROP TEXT SEARCH CONFIGURATION lifeos_fr")
