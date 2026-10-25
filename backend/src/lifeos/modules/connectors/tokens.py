"""Sealed OAuth tokens and their refresh (ADR 0012, threat T19).

Tokens are sealed with AES-256-GCM under the connectors keyring (Docker secret
`lifeos_connectors_key`), with the account id and the token kind as associated data: a
blob copied onto another account, or from the refresh column to the access column, does
not open. A token is opened only right before a provider call, and never logged, audited
or returned by the API.
"""

import uuid
from datetime import timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core import crypto
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.connectors.base import ConnectorAuthError, OAuthProvider, TokenSet
from lifeos.modules.connectors.models import ConnectorAccount
from lifeos.modules.identity.models import User
from lifeos.modules.notifications.service import notify

log = get_logger(__name__)

TokenKind = Literal["refresh", "access", "pkce"]
# A cached access token is used only if it stays valid this long: a call never starts
# with a token about to expire.
REFRESH_MARGIN = timedelta(seconds=60)


def keyring(settings: Settings) -> crypto.Keyring:
    """Raises crypto.KeyUnavailableError when the connectors key is missing or invalid."""
    return crypto.load_keyring(settings.connectors_key, "connectors key")


def seal_token(ring: crypto.Keyring, owner_id: uuid.UUID, kind: TokenKind, token: str) -> bytes:
    return crypto.seal(ring.key, ring.current, token.encode(), owner_id.bytes + kind.encode())


def open_token(ring: crypto.Keyring, owner_id: uuid.UUID, kind: TokenKind, blob: bytes) -> str:
    return crypto.open_sealed(ring.keys, blob, owner_id.bytes + kind.encode()).decode()


def store_tokens(ring: crypto.Keyring, account: ConnectorAccount, tokens: TokenSet) -> None:
    """Seals fresh tokens on the account (the refresh token only if the provider sent one:
    most refresh responses do not, some providers rotate it on every refresh)."""
    account.access_token_enc = seal_token(ring, account.id, "access", tokens.access_token)
    account.access_expires_at = tokens.expires_at
    if tokens.refresh_token:
        account.refresh_token_enc = seal_token(ring, account.id, "refresh", tokens.refresh_token)
    if tokens.scopes:
        account.scopes = sorted(tokens.scopes)


def wipe_tokens(account: ConnectorAccount) -> None:
    account.refresh_token_enc = None
    account.access_token_enc = None
    account.access_expires_at = None


def _usable(account: ConnectorAccount | None) -> ConnectorAccount:
    if account is None:
        raise ConnectorAuthError("not_found")
    if account.status == "revoked":
        raise ConnectorAuthError("revoked")
    if account.status != "active" or account.refresh_token_enc is None:
        raise ConnectorAuthError("reauth_required")
    return account


def _cached(ring: crypto.Keyring, account: ConnectorAccount) -> str | None:
    if (
        account.access_token_enc is None
        or account.access_expires_at is None
        or account.access_expires_at - REFRESH_MARGIN <= utcnow()
    ):
        return None
    try:
        return open_token(ring, account.id, "access", account.access_token_enc)
    except crypto.CryptoError:
        return None  # sealed under a key no longer configured: refresh instead


async def require_reauth(session: AsyncSession, account: ConnectorAccount, *, reason: str) -> None:
    """The account cannot be used until the owner connects it again: tokens wiped, one
    notification per connection, failure audited (reason code only)."""
    account.status = "reauth_required"
    account.last_error = reason
    wipe_tokens(account)
    user = await session.get(User, account.user_id)
    if user is not None:
        await notify(
            session,
            user,
            category="important",
            title="Un compte connecté doit être reconnecté",
            body=(
                "LifeOS n'a plus accès à ce compte. Réglages > Comptes connectés : "
                "reconnectez-le pour reprendre."
            ),
            # One notification per connection: a later reconnection may notify again.
            dedup_key=f"connector_reauth:{account.id}:{account.connected_at.isoformat()}",
            source_type="connector_account",
            source_id=account.id,
        )
    await audit.record(
        session,
        action="connector.reauth_required",
        outcome="failure",
        actor_type="system",
        actor_id="connectors",
        user_id=account.user_id,
        target_type="connector_account",
        target_id=account.id,
        details={"provider": account.provider, "reason": reason},
    )
    log.warning("connector_reauth_required", account=str(account.id), reason=reason)


async def access_token(
    database: Database, settings: Settings, provider: OAuthProvider, account_id: uuid.UUID
) -> str:
    """A valid access token for the account, refreshed if needed.

    Raises ConnectorAuthError when the account must be connected again (and marks it so),
    and the provider's other errors (unavailable, quota) unchanged. Callers must have
    checked the owner's consent first (`service.require_capability`).
    """
    try:
        ring = keyring(settings)
    except crypto.CryptoError as exc:
        raise ConnectorAuthError("key_unavailable") from exc
    async with database.sessionmaker() as session:
        account = _usable(await session.get(ConnectorAccount, account_id))
        if (cached := _cached(ring, account)) is not None:
            return cached

    # Refresh under the account's row lock: the API and the worker never refresh the same
    # account at once (a provider that rotates refresh tokens would invalidate one of them).
    # The lock is held for one provider call at most, bounded by its timeout.
    async with database.sessionmaker() as session, session.begin():
        account = _usable(
            await session.scalar(
                select(ConnectorAccount).where(ConnectorAccount.id == account_id).with_for_update()
            )
        )
        if (cached := _cached(ring, account)) is not None:
            return cached  # refreshed by the other caller meanwhile
        assert account.refresh_token_enc is not None  # checked by _usable
        failure: ConnectorAuthError
        try:
            refresh_token = open_token(ring, account.id, "refresh", account.refresh_token_enc)
            tokens = await provider.refresh(refresh_token)
        except crypto.CryptoError:
            # Connectors key lost or replaced: the token cannot be read, only reconnecting
            # the account helps.
            failure = ConnectorAuthError("token_unreadable")
        except ConnectorAuthError as exc:
            failure = exc
        else:
            store_tokens(ring, account, tokens)
            if tokens.refresh_token is None:
                # Sealed again under the current key (rotation of the connectors key).
                account.refresh_token_enc = seal_token(ring, account.id, "refresh", refresh_token)
            account.last_error = None
            return tokens.access_token
        # Committed with the transaction: the account stays marked after the error.
        await require_reauth(session, account, reason=failure.reason)
    raise failure


async def forget_access_token(database: Database, account_id: uuid.UUID) -> None:
    """The provider refused the cached access token before its expiry: the next call
    refreshes it."""
    async with database.sessionmaker() as session, session.begin():
        account = await session.get(ConnectorAccount, account_id)
        if account is not None:
            account.access_token_enc = None
            account.access_expires_at = None
