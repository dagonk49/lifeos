"""Connected accounts: what is authorised where, consent per space, disconnection.

Rules (ADR 0012):
- giving access (connecting, consenting in a space) is reinforced: passkey login of less
  than 10 minutes; withdrawing it (one consent, or the whole account) is immediate;
- every use of an account (sync job, read, action) first calls `require_capability`:
  account active, consent of THIS space for THIS capability, scope granted by the
  provider, and for an agent its own permission on `calendar` or `email`;
- disconnecting revokes the grant at the provider (best effort), wipes the tokens,
  withdraws every consent, disables the sources, expires the pending approvals that would
  use the account, and on request deletes the synced data.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from sqlalchemy import ColumnElement, and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core import crypto
from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.connectors import Connectors
from lifeos.modules.connectors.base import (
    CAPABILITIES,
    CAPABILITY_RESOURCE,
    Capability,
    ConnectorError,
    OAuthProvider,
)
from lifeos.modules.connectors.models import (
    ConnectorAccount,
    ConnectorGrant,
    ConnectorSource,
    OAuthState,
)
from lifeos.modules.connectors.tokens import TokenKind, keyring, open_token, wipe_tokens
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval
from lifeos.modules.guardian.principal import Principal

# "manual_required": the tokens were already wiped locally (refused refresh, unreadable
# token) without revoking the grant, which may still be live at the provider: only the
# owner can withdraw it there.
RemoteRevocation = Literal["done", "failed", "manual_required", "not_needed"]


def unavailable() -> ProblemError:
    return ProblemError(
        503,
        "connectors_unavailable",
        "Connectors are not configured on this server",
        "Set LIFEOS_CONNECTORS_PROVIDER and the connectors key (docs/guide-google.md).",
    )


def provider_for(connectors: Connectors, name: str) -> OAuthProvider:
    provider = connectors.get(name)
    if provider is None:
        raise unavailable()
    return provider


def normalize_capabilities(values: Iterable[str]) -> tuple[Capability, ...]:
    """Known capabilities, in canonical order; writing the calendar implies reading it."""
    wanted = set(values)
    if "calendar.write" in wanted:
        wanted.add("calendar.read")
    return tuple(c for c in CAPABILITIES if c in wanted)


def _active_grant() -> tuple[ColumnElement[bool], ...]:
    """Consents in force: not withdrawn, not expired."""
    return (
        ConnectorGrant.revoked_at.is_(None),
        or_(ConnectorGrant.expires_at.is_(None), ConnectorGrant.expires_at > utcnow()),
    )


async def own_account(
    ctx: Ctx, account_id: uuid.UUID, *, for_update: bool = False
) -> ConnectorAccount:
    stmt = select(ConnectorAccount).where(
        ConnectorAccount.id == account_id, ConnectorAccount.user_id == ctx.principal.user_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    account = await ctx.db.scalar(stmt)
    if account is None:
        raise not_found("connector_not_found", "Connected account not found")
    return account


def _require_active(account: ConnectorAccount) -> None:
    if account.status == "revoked":
        raise ProblemError(409, "connector_disconnected", "This account is disconnected")
    if account.status != "active":
        raise ProblemError(
            409,
            "connector_reauth_required",
            "This account must be connected again",
            extra={"account_id": str(account.id)},
        )


# --- Listing --------------------------------------------------------------------------------


@dataclass
class AccountView:
    account: ConnectorAccount
    # Capabilities the provider granted (scopes), whatever the consents per space.
    capabilities: list[str]
    grants: list[ConnectorGrant] = field(default_factory=list)
    sources: list[ConnectorSource] = field(default_factory=list)


async def account_view(ctx: Ctx, connectors: Connectors, account: ConnectorAccount) -> AccountView:
    return (await list_accounts(ctx, connectors, only=account.id))[0]


async def list_accounts(
    ctx: Ctx, connectors: Connectors, *, only: uuid.UUID | None = None
) -> list[AccountView]:
    """Every account with what it may serve, where: the owner sees what is authorised."""
    stmt = select(ConnectorAccount).where(ConnectorAccount.user_id == ctx.principal.user_id)
    if only is not None:
        stmt = stmt.where(ConnectorAccount.id == only)
    accounts = list(await ctx.db.scalars(stmt.order_by(ConnectorAccount.connected_at)))
    ids = [a.id for a in accounts]
    spaces = ctx.principal.space_ids
    grants = await ctx.db.scalars(
        select(ConnectorGrant)
        .where(
            ConnectorGrant.account_id.in_(ids),
            ConnectorGrant.space_id.in_(spaces),
            *_active_grant(),
        )
        .order_by(ConnectorGrant.created_at)
    )
    sources = await ctx.db.scalars(
        select(ConnectorSource)
        .where(ConnectorSource.account_id.in_(ids), ConnectorSource.space_id.in_(spaces))
        .order_by(ConnectorSource.name)
    )
    views: dict[uuid.UUID, AccountView] = {}
    for account in accounts:
        provider = connectors.get(account.provider)
        granted = provider.capabilities_in(account.scopes) if provider else frozenset()
        views[account.id] = AccountView(
            account=account, capabilities=[c for c in CAPABILITIES if c in granted]
        )
    for grant in grants:
        views[grant.account_id].grants.append(grant)
    for source in sources:
        views[source.account_id].sources.append(source)
    return list(views.values())


# --- Consents per space ---------------------------------------------------------------------


async def apply_grants(
    ctx: Ctx,
    account: ConnectorAccount,
    space_id: uuid.UUID,
    *,
    add: Iterable[Capability] = (),
    remove: Iterable[Capability] = (),
) -> tuple[int, int]:
    """Adds and withdraws consents of one space; returns (added, removed)."""
    now = utcnow()
    current = list(
        await ctx.db.scalars(
            select(ConnectorGrant).where(
                ConnectorGrant.account_id == account.id,
                ConnectorGrant.space_id == space_id,
                ConnectorGrant.revoked_at.is_(None),
            )
        )
    )
    active: dict[str, ConnectorGrant] = {}
    for grant in current:
        if grant.expires_at is not None and grant.expires_at <= now:
            grant.revoked_at = now  # expired: closed, so that it may be granted again
        else:
            active[grant.capability] = grant
    removed = 0
    for capability in set(remove):
        if capability in active:
            active.pop(capability).revoked_at = now
            removed += 1
    added = 0
    for capability in normalize_capabilities(add):
        if capability not in active:
            ctx.db.add(
                ConnectorGrant(
                    user_id=ctx.principal.user_id,
                    account_id=account.id,
                    space_id=space_id,
                    capability=capability,
                    created_at=now,
                )
            )
            added += 1
    await ctx.db.flush()
    return added, removed


async def set_grants(
    ctx: Ctx,
    connectors: Connectors,
    account_id: uuid.UUID,
    space_id: uuid.UUID,
    capabilities: Iterable[str],
) -> ConnectorAccount:
    """The capabilities this account serves in this space, exactly. Adding is reinforced
    and needs the scopes already granted by the provider (otherwise: a new consent)."""
    ctx.principal.require_space(space_id)
    wanted = set(normalize_capabilities(capabilities))
    account = await own_account(ctx, account_id, for_update=True)
    current = set(
        await ctx.db.scalars(
            select(ConnectorGrant.capability).where(
                ConnectorGrant.account_id == account.id,
                ConnectorGrant.space_id == space_id,
                *_active_grant(),
            )
        )
    )
    to_add = wanted - current
    to_remove = current - wanted
    if to_add:
        _require_active(account)
        await authorize(ctx, "connector.grant", space_id)
        provider = provider_for(connectors, account.provider)
        missing = to_add - provider.capabilities_in(account.scopes)
        if missing:
            raise ProblemError(
                409,
                "connector_scope_missing",
                "The provider has not granted these capabilities",
                "Connect the account again and accept these accesses.",
                extra={"capabilities": [c for c in CAPABILITIES if c in missing]},
            )
    added, removed = await apply_grants(
        ctx,
        account,
        space_id,
        add=[c for c in CAPABILITIES if c in to_add],
        remove=[c for c in CAPABILITIES if c in to_remove],
    )
    if added or removed:
        await audit.record(
            ctx.db,
            action="connector.grant" if added else "connector.grant.revoke",
            actor_type="user",
            actor_id=ctx.principal.actor_id,
            user_id=ctx.principal.user_id,
            space_id=space_id,
            target_type="connector_account",
            target_id=account.id,
            details={"provider": account.provider, "added": added, "removed": removed},
        )
    return account


async def revoke_grant(ctx: Ctx, grant_id: uuid.UUID) -> None:
    """Immediate withdrawal of one consent; no re-authentication to reduce access."""
    grant = await ctx.db.scalar(
        select(ConnectorGrant)
        .where(
            ConnectorGrant.id == grant_id,
            ConnectorGrant.user_id == ctx.principal.user_id,
            ConnectorGrant.space_id.in_(ctx.principal.space_ids),
            ConnectorGrant.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if grant is None:
        raise not_found("connector_grant_not_found", "Consent not found")
    grant.revoked_at = utcnow()
    await audit.record(
        ctx.db,
        action="connector.grant.revoke",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=grant.space_id,
        target_type="connector_grant",
        target_id=grant.id,
        details={"capability": grant.capability},
    )


# --- Use --------------------------------------------------------------------------------------


async def _deny(ctx: Ctx, account_id: uuid.UUID, space_id: uuid.UUID, reason: str) -> None:
    await audit.record_detached(
        ctx.database,
        action="connector.use",
        outcome="denied",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type="connector_account",
        target_id=account_id,
        details={"reason": reason},
    )


def agent_may(principal: Principal, space_id: uuid.UUID, capability: Capability) -> bool:
    resource, access = CAPABILITY_RESOURCE[capability]
    return principal.can(space_id, resource, access)


async def require_capability(
    ctx: Ctx,
    connectors: Connectors,
    account_id: uuid.UUID,
    space_id: uuid.UUID,
    capability: Capability,
) -> ConnectorAccount:
    """The account, if it may serve `capability` in `space_id` for this principal.

    Called before every sync and every action through a connected account. Out of the
    principal's spaces: 404, as everywhere.
    """
    ctx.principal.require_space(space_id)
    if ctx.principal.kind == "agent" and not agent_may(ctx.principal, space_id, capability):
        await _deny(ctx, account_id, space_id, "agent_permission_required")
        raise ProblemError(
            403,
            "agent_permission_required",
            "The agent has no permission on this data",
            extra={"resource": CAPABILITY_RESOURCE[capability][0]},
        )
    account = await own_account(ctx, account_id)
    _require_active(account)
    grant = await ctx.db.scalar(
        select(ConnectorGrant.id).where(
            ConnectorGrant.account_id == account.id,
            ConnectorGrant.space_id == space_id,
            ConnectorGrant.capability == capability,
            *_active_grant(),
        )
    )
    provider = connectors.get(account.provider)
    if (
        grant is None
        or provider is None
        or capability not in provider.capabilities_in(account.scopes)
    ):
        await _deny(ctx, account.id, space_id, "capability_missing")
        raise ProblemError(
            403,
            "connector_capability_missing",
            "This account is not authorised for this use in this space",
            extra={"capability": capability},
        )
    return account


# --- Disconnection ----------------------------------------------------------------------------


@dataclass(frozen=True)
class DisconnectResult:
    account_id: uuid.UUID
    remote_revocation: RemoteRevocation
    purged: bool


async def _revoke_remotely(
    ctx: Ctx, connectors: Connectors, account: ConnectorAccount
) -> RemoteRevocation:
    # Revoking the refresh token ends the whole grant; the access token is enough when
    # it is all that is left.
    candidates: tuple[tuple[TokenKind, bytes | None], ...] = (
        ("refresh", account.refresh_token_enc),
        ("access", account.access_token_enc),
    )
    sealed = [(kind, blob) for kind, blob in candidates if blob]
    if not sealed and account.status != "reauth_required":
        return "not_needed"  # already disconnected: revoked (or reported) back then
    provider = connectors.get(account.provider)
    reason = "provider_unavailable"
    if not sealed:
        reason = "tokens_wiped"
    elif provider is not None:
        kind, blob = sealed[0]
        try:
            token = open_token(keyring(ctx.settings), account.id, kind, blob)
            await provider.revoke(token)
            return "done"
        except crypto.CryptoError:
            reason = "token_unreadable"
        except ConnectorError as exc:
            reason = exc.reason
    # The local wipe happens anyway; the owner is told to withdraw the access at the
    # provider (the failure is audited, with a reason code only).
    await audit.record_detached(
        ctx.database,
        action="connector.revoke",
        outcome="failure",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        target_type="connector_account",
        target_id=account.id,
        details={"provider": account.provider, "reason": reason},
    )
    return "manual_required" if reason == "tokens_wiped" else "failed"


async def disconnect(
    ctx: Ctx, connectors: Connectors, account_id: uuid.UUID, *, purge: bool
) -> DisconnectResult:
    """Cuts every access of the account now. `purge`: also deletes the account row and,
    through the cascade of its sources, every synced row; otherwise they are kept."""
    await authorize(ctx, "connector.disconnect", None)
    account = await own_account(ctx, account_id, for_update=True)
    remote = await _revoke_remotely(ctx, connectors, account)
    now = utcnow()
    wipe_tokens(account)
    account.status = "revoked"
    account.revoked_at = account.revoked_at or now
    account.last_error = None
    grants = await ctx.db.execute(
        update(ConnectorGrant)
        .where(ConnectorGrant.account_id == account.id, ConnectorGrant.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    sources = await ctx.db.execute(
        update(ConnectorSource)
        .where(ConnectorSource.account_id == account.id, ConnectorSource.enabled.is_(True))
        .values(enabled=False)
    )
    # Proposals that would act through this account (they carry its id in their exact
    # parameters) can no longer be approved.
    approvals = await ctx.db.execute(
        update(Approval)
        .where(
            Approval.user_id == ctx.principal.user_id,
            Approval.status == "pending",
            Approval.params["account_id"].astext == str(account.id),
        )
        .values(status="expired")
    )
    # Consents in progress can no longer reconnect it: those of this account, and every
    # pending one of this provider (a new connection may return this same account).
    await ctx.db.execute(
        delete(OAuthState).where(
            or_(
                OAuthState.account_id == account.id,
                and_(
                    OAuthState.user_id == ctx.principal.user_id,
                    OAuthState.provider == account.provider,
                    OAuthState.used_at.is_(None),
                ),
            )
        )
    )
    if purge:
        await ctx.db.delete(account)
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="connector.disconnect",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        target_type="connector_account",
        target_id=account_id,
        details={
            "provider": account.provider,
            "purge": purge,
            "remote_revocation": remote,
            "grants": int(grants.rowcount or 0),  # type: ignore[attr-defined]
            "sources": int(sources.rowcount or 0),  # type: ignore[attr-defined]
            "approvals": int(approvals.rowcount or 0),  # type: ignore[attr-defined]
        },
    )
    return DisconnectResult(account_id=account_id, remote_revocation=remote, purged=purge)


# --- Retention --------------------------------------------------------------------------------


async def purge_oauth_states(session: AsyncSession) -> int:
    """Consents never completed, or completed: nothing to keep after a day."""
    cutoff = utcnow() - timedelta(days=1)
    result = await session.execute(
        delete(OAuthState).where(or_(OAuthState.expires_at < cutoff, OAuthState.used_at < cutoff))
    )
    count = int(result.rowcount or 0)  # type: ignore[attr-defined]
    await audit.record(
        session,
        action="connector.purge_states",
        actor_type="system",
        actor_id="purge_oauth_states",
        details={"oauth_states": count},
    )
    return count
