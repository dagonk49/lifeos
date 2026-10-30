"""Connected accounts, consents per space, mapped sources and pending consents (ADR 0012).

Keep the tuples in sync with migration 0010 and docs/modele-de-donnees.md.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey
from lifeos.modules.connectors.base import CAPABILITIES

PROVIDERS = ("google", "fake")
# active: usable; reauth_required: the provider refused the refresh token (or the key that
# sealed it is gone), tokens wiped; revoked: disconnected by the owner, tokens wiped.
ACCOUNT_STATUSES = ("active", "reauth_required", "revoked")
SOURCE_KINDS = ("calendar", "mail_label")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ConnectorAccount(UUIDPrimaryKey, Timestamps, Base):
    """An external account linked through OAuth. It spans spaces: what each space may use
    is in `connector_grants`. Tokens are sealed (AES-256-GCM, connectors key, account id
    as associated data) and never leave the server."""

    __tablename__ = "connector_accounts"
    __table_args__ = (
        CheckConstraint(_in("provider", PROVIDERS), name="provider"),
        CheckConstraint(_in("status", ACCOUNT_STATUSES), name="status"),
        UniqueConstraint("user_id", "provider", "subject", name="uq_connector_accounts_subject"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(Text)
    # Stable id of the external account (OpenID `sub`), never its address.
    subject: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active", server_default="active")
    # Scopes the provider ACTUALLY granted (granular consent may drop some).
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_expires_at: Mapped[datetime | None]
    # Reason code of the last failure (e.g. invalid_grant), never a provider message.
    last_error: Mapped[str | None] = mapped_column(Text)
    connected_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]


class ConnectorGrant(UUIDPrimaryKey, Base):
    """The owner's consent: this account may serve this capability in this space."""

    __tablename__ = "connector_grants"
    __table_args__ = (
        CheckConstraint(_in("capability", CAPABILITIES), name="capability"),
        # One active consent per account, space and capability; revoked ones stay as history.
        Index(
            "uq_connector_grants_active",
            "account_id",
            "space_id",
            "capability",
            unique=True,
            postgresql_where="revoked_at IS NULL",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_accounts.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    capability: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


class ConnectorSource(UUIDPrimaryKey, Owned, Timestamps, Base):
    """A calendar or a mail label of an account, mapped by the owner to one space.

    Synced rows reference their source with ON DELETE CASCADE: purging an account removes
    them.
    """

    __tablename__ = "connector_sources"
    __table_args__ = (
        CheckConstraint(_in("kind", SOURCE_KINDS), name="kind"),
        UniqueConstraint("account_id", "kind", "provider_ref", name="uq_connector_sources_ref"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_accounts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(Text)
    provider_ref: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    last_synced_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    # Sync state (task 14). One sync at a time per source: whoever holds an unexpired
    # lease. A pass is numbered (`sync_generation`); the provider's page token of the pass
    # in progress is kept after each page, so an interrupted pass resumes there.
    sync_generation: Mapped[int] = mapped_column(default=0, server_default="0")
    page_token: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None]
    # Mail labels (task 17): the provider's change cursor (Gmail historyId) after the
    # last complete pass; None until the first full pass is done.
    sync_cursor: Mapped[str | None] = mapped_column(Text)


class OAuthState(UUIDPrimaryKey, Base):
    """A consent in progress: single use, short-lived (`oauth_state_minutes`).

    Only the hash of `state` is stored; the PKCE verifier is sealed. Purged after a day.
    """

    __tablename__ = "oauth_states"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(Text)
    state_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    verifier_enc: Mapped[bytes] = mapped_column(LargeBinary)
    space_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spaces.id", ondelete="CASCADE"))
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # Incremental consent, or reconnection, of an account already linked: the provider
    # must send back that same account.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connector_accounts.id", ondelete="CASCADE")
    )
    # Reconnection: restores the tokens only, never adds a consent in a space.
    reconnect: Mapped[bool] = mapped_column(default=False, server_default="false")
    expires_at: Mapped[datetime] = mapped_column(index=True)
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
