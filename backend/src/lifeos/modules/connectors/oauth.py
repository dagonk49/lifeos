"""Connecting an account: OAuth 2.0 authorization code flow with PKCE (ADR 0012).

1. `start` (reinforced: passkey login of less than 10 minutes) records a single-use state
   (only its hash) and the sealed PKCE verifier, and returns the provider's consent URL.
2. The provider sends the browser back to the web page `/connexions/retour`, which posts
   `state` and `code` to the API: the session cookie is SameSite=Strict and is not sent
   on the provider's cross-site redirect, but it is on this same-site request.
3. `complete` claims the state (same owner, unexpired, unused: CSRF and replay), exchanges
   the code with the verifier, records the scopes ACTUALLY granted and creates the
   consents of the space for those capabilities only.

For an account already linked (incremental consent, reconnection), the provider must send
back that same account: another one is refused and its fresh grant revoked. A reconnection
restores the tokens only: the consents per space stay exactly as they were.
"""

import base64
import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update

from lifeos.core import crypto
from lifeos.core.errors import ProblemError
from lifeos.core.security import hash_token, new_token, utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.connectors import Connectors
from lifeos.modules.connectors.base import (
    AuthorizationRequest,
    Capability,
    ConnectorAuthError,
    ConnectorError,
    OAuthProvider,
    TokenSet,
)
from lifeos.modules.connectors.models import ConnectorAccount, OAuthState
from lifeos.modules.connectors.service import (
    apply_grants,
    normalize_capabilities,
    own_account,
    provider_for,
    unavailable,
)
from lifeos.modules.connectors.tokens import keyring, open_token, seal_token, store_tokens
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx

_ERROR_CODE = re.compile(r"^[a-z_]{1,64}$")


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _keyring(ctx: Ctx) -> crypto.Keyring:
    try:
        return keyring(ctx.settings)
    except crypto.CryptoError as exc:
        raise unavailable() from exc


async def start(
    ctx: Ctx,
    connectors: Connectors,
    *,
    provider_name: str,
    space_id: uuid.UUID,
    capabilities: list[str],
    account_id: uuid.UUID | None = None,
    reconnect: bool = False,
) -> str:
    """Records a consent in progress and returns the provider's consent URL.

    `reconnect`: new tokens for `account_id`, with the accesses it held at the provider
    (plus `capabilities`, if any); no consent of `space_id` is added on return.
    """
    ctx.principal.require_space(space_id)
    wanted = normalize_capabilities(capabilities)
    if reconnect and account_id is None:
        raise ProblemError(422, "invalid_reconnect", "Reconnecting needs the account's id")
    if not wanted and not reconnect:
        raise ProblemError(422, "invalid_capabilities", "Choose at least one access")
    # Incremental consent (or reconnection) of a linked account: its own provider.
    account = await own_account(ctx, account_id) if account_id is not None else None
    provider = provider_for(connectors, account.provider if account else provider_name)
    if reconnect and account is not None:
        wanted = normalize_capabilities([*wanted, *provider.capabilities_in(account.scopes)])
    await authorize(ctx, "connector.connect", space_id)
    ring = _keyring(ctx)
    state = new_token()
    # RFC 7636: 43 to 128 characters of the unreserved set; 64 random bytes give 86.
    verifier = secrets.token_urlsafe(64)
    now = utcnow()
    row = OAuthState(
        id=uuid.uuid4(),
        user_id=ctx.principal.user_id,
        provider=provider.name,
        state_hash=hash_token(state),
        space_id=space_id,
        capabilities=list(wanted),
        account_id=account.id if account else None,
        reconnect=reconnect,
        expires_at=now + timedelta(minutes=ctx.settings.oauth_state_minutes),
        created_at=now,
    )
    row.verifier_enc = seal_token(ring, row.id, "pkce", verifier)
    ctx.db.add(row)
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="connector.connect.start",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type="connector_account" if account else None,
        target_id=account.id if account else None,
        details={
            "provider": provider.name,
            "capabilities": ",".join(wanted),
            "reconnect": reconnect,
        },
    )
    # Ask for consent again unless the account holds a working refresh token: that is
    # when the provider is sure to send a new one.
    holds_refresh = (
        account is not None and account.status == "active" and account.refresh_token_enc is not None
    )
    return provider.authorization_url(
        AuthorizationRequest(
            state=state,
            code_challenge=_challenge(verifier),
            redirect_uri=ctx.settings.oauth_redirect_uri,
            scopes=provider.scopes_for(wanted),
            login_hint=account.email if account else None,
            force_consent=not holds_refresh,
        )
    )


@dataclass(frozen=True)
class ConnectResult:
    account: ConnectorAccount
    space_id: uuid.UUID
    requested: tuple[Capability, ...]
    granted: tuple[Capability, ...]
    missing: tuple[Capability, ...]
    # Tokens restored only: the consents per space were left as they were.
    reconnected: bool = False


async def _fail(
    ctx: Ctx,
    reason: str,
    space_id: uuid.UUID | None = None,
    *,
    target_id: uuid.UUID | None = None,
    **extra: str,
) -> None:
    await audit.record_detached(
        ctx.database,
        action="connector.connect",
        outcome="denied" if reason.startswith("state_") else "failure",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type="connector_account" if target_id else None,
        target_id=target_id,
        details={"reason": reason, **extra},
    )


async def _discard_grant(ctx: Ctx, provider: OAuthProvider, tokens: TokenSet, subject: str) -> str:
    """Best-effort revocation of tokens received for an account that was not asked for.

    Not when that account is already linked and working: revoking ends the provider's
    whole grant, so it would cut that account too. Returns done, failed or not_needed.
    """
    linked = await ctx.db.scalar(
        select(ConnectorAccount.id).where(
            ConnectorAccount.user_id == ctx.principal.user_id,
            ConnectorAccount.provider == provider.name,
            ConnectorAccount.subject == subject,
            ConnectorAccount.status == "active",
            ConnectorAccount.refresh_token_enc.is_not(None),
        )
    )
    if linked is not None:
        return "not_needed"
    try:
        await provider.revoke(tokens.refresh_token or tokens.access_token)
    except ConnectorError:
        return "failed"
    return "done"


async def _claim(ctx: Ctx, state: str) -> OAuthState | None:
    """Marks the state used in its own transaction: single use, even if the exchange
    fails afterwards (a replayed code meets an already used state)."""
    async with ctx.database.sessionmaker() as session, session.begin():
        claimed = await session.scalar(
            update(OAuthState)
            .where(
                OAuthState.state_hash == hash_token(state),
                OAuthState.user_id == ctx.principal.user_id,
                OAuthState.used_at.is_(None),
                OAuthState.expires_at > utcnow(),
            )
            .values(used_at=utcnow())
            .returning(OAuthState)
        )
        if claimed is not None:
            session.expunge(claimed)
        return claimed


async def complete(
    ctx: Ctx,
    connectors: Connectors,
    *,
    state: str,
    code: str | None,
    error: str | None = None,
) -> ConnectResult:
    row = await _claim(ctx, state)
    if row is None:
        await _fail(ctx, "state_invalid")
        raise ProblemError(
            400,
            "oauth_state_invalid",
            "This connection request is unknown, expired or already used",
            "Start the connection again from the settings.",
        )
    if row.space_id not in ctx.principal.space_ids:
        await _fail(ctx, "state_invalid")
        raise ProblemError(400, "oauth_state_invalid", "This connection request is invalid")
    if error is not None or not code:
        # The owner refused on the provider's page (access_denied), or it failed there.
        reason = error if error and _ERROR_CODE.match(error) else "no_code"
        await _fail(ctx, f"provider_{reason}", row.space_id)
        raise ProblemError(
            400,
            "connector_consent_denied",
            "The provider did not grant access",
            extra={"reason": reason},
        )
    provider = provider_for(connectors, row.provider)
    ring = _keyring(ctx)
    try:
        verifier = open_token(ring, row.id, "pkce", row.verifier_enc)
        tokens = await provider.exchange_code(
            code, code_verifier=verifier, redirect_uri=ctx.settings.oauth_redirect_uri
        )
    except crypto.CryptoError as exc:
        await _fail(ctx, "verifier_unreadable", row.space_id)
        raise unavailable() from exc
    except ConnectorAuthError as exc:
        await _fail(ctx, "code_invalid", row.space_id)
        raise ProblemError(
            400, "oauth_code_invalid", "The provider refused the authorization code"
        ) from exc
    except ConnectorError as exc:
        await _fail(ctx, exc.reason, row.space_id)
        raise ProblemError(
            502, "connector_provider_error", "The provider could not be reached"
        ) from exc
    identity = tokens.identity
    if identity is None:
        await _fail(ctx, "identity_missing", row.space_id)
        raise ProblemError(502, "connector_provider_error", "The provider sent no identity")
    if row.account_id is not None:
        # Incremental consent or reconnection of a given account: the owner may have picked
        # another one on the provider's page (the login hint is only a hint).
        expected = await ctx.db.scalar(
            select(ConnectorAccount).where(
                ConnectorAccount.id == row.account_id,
                ConnectorAccount.user_id == ctx.principal.user_id,
            )
        )
        if (
            expected is None
            or expected.provider != provider.name
            or expected.subject != identity.subject
        ):
            revocation = await _discard_grant(ctx, provider, tokens, identity.subject)
            await _fail(
                ctx,
                "account_mismatch",
                row.space_id,
                target_id=row.account_id,
                remote_revocation=revocation,
            )
            raise ProblemError(
                409,
                "connector_account_mismatch",
                "The provider returned another account than the one to connect",
                "Start again and choose the same account on the provider's page.",
            )

    now = utcnow()
    account = await ctx.db.scalar(
        select(ConnectorAccount)
        .where(
            ConnectorAccount.user_id == ctx.principal.user_id,
            ConnectorAccount.provider == provider.name,
            ConnectorAccount.subject == identity.subject,
        )
        .with_for_update()
    )
    new_account = account is None
    if account is None:
        account = ConnectorAccount(
            id=uuid.uuid4(),
            user_id=ctx.principal.user_id,
            provider=provider.name,
            subject=identity.subject,
            connected_at=now,
        )
        ctx.db.add(account)
    elif account.status != "active":
        # Reconnected: a new connection (a new reauth notification may follow later).
        account.connected_at = now
    if tokens.refresh_token is None and (
        account.status != "active" or account.refresh_token_enc is None
    ):
        # No refresh token for an account that has none: unusable beyond one hour.
        await _fail(ctx, "no_refresh_token", row.space_id)
        raise ProblemError(
            502,
            "connector_provider_error",
            "The provider sent no refresh token",
            "Withdraw LifeOS's access in the account's security settings, then connect again.",
        )
    account.email = identity.email
    account.status = "active"
    account.revoked_at = None
    account.last_error = None
    account.scopes = sorted(tokens.scopes)
    store_tokens(ring, account, tokens)
    await ctx.db.flush()

    granted_now = provider.capabilities_in(tokens.scopes)
    requested = normalize_capabilities(row.capabilities)
    granted = tuple(c for c in requested if c in granted_now)
    missing = tuple(c for c in requested if c not in granted_now)
    # A reconnection gives no new consent: the consents per space are kept as they were
    # (those whose scope the owner unticked stay without effect, see require_capability).
    added = 0
    if not row.reconnect:
        added, _ = await apply_grants(ctx, account, row.space_id, add=granted)
    await audit.record(
        ctx.db,
        action="connector.connect",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=row.space_id,
        target_type="connector_account",
        target_id=account.id,
        details={
            "provider": provider.name,
            "new_account": new_account,
            "reconnect": row.reconnect,
            "requested": len(requested),
            "granted": len(granted),
            "missing": len(missing),
            "grants_added": added,
        },
    )
    return ConnectResult(
        account=account,
        space_id=row.space_id,
        requested=requested,
        granted=granted,
        missing=missing,
        reconnected=row.reconnect,
    )
