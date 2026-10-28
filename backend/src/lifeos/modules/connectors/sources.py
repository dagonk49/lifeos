"""Calendars (task 14) and mail labels (task 17) of a connected account, mapped to a space.

Mapping a calendar needs the account's calendar.read consent in that space, a label its
mail.read consent: the owner chooses which calendar or label feeds which space, within
what was consented, one space per calendar or label. Removing a mapping deletes its
synced rows (cascade); withdrawing the consent only stops the sync.
"""

import uuid
from typing import Literal

from sqlalchemy import select

from lifeos.core.errors import ProblemError, not_found
from lifeos.modules.audit import service as audit
from lifeos.modules.connectors import Connectors, tokens
from lifeos.modules.connectors.base import (
    CalendarAdapter,
    Capability,
    ConnectorAuthError,
    ConnectorError,
    MailAdapter,
    RemoteCollection,
)
from lifeos.modules.connectors.models import ConnectorAccount, ConnectorSource
from lifeos.modules.connectors.service import own_account, provider_for, require_capability
from lifeos.modules.guardian.context import Ctx
from lifeos.workers.queue import enqueue

SYNC_ACTORS = {"calendar": "sync_calendar_source", "mail_label": "sync_mail_source"}
SYNC_QUEUE = "connectors"
SourceKind = Literal["calendar", "mail_label"]
CAPABILITY: dict[str, Capability] = {"calendar": "calendar.read", "mail_label": "mail.read"}


def _calendar_adapter(connectors: Connectors, account: ConnectorAccount) -> CalendarAdapter:
    provider = provider_for(connectors, account.provider)
    if not hasattr(provider, "list_events"):
        raise ProblemError(409, "connector_capability_missing", "No calendar for this account")
    adapter: CalendarAdapter = provider  # type: ignore[assignment]
    return adapter


def _mail_adapter(connectors: Connectors, account: ConnectorAccount) -> MailAdapter:
    provider = provider_for(connectors, account.provider)
    if not hasattr(provider, "list_labels"):
        raise ProblemError(409, "connector_capability_missing", "No mail for this account")
    adapter: MailAdapter = provider  # type: ignore[assignment]
    return adapter


async def remote_calendars(
    ctx: Ctx, connectors: Connectors, account_id: uuid.UUID
) -> list[RemoteCollection]:
    """The account's calendars, read live at the provider (names only)."""
    return await remote_collections(ctx, connectors, account_id, "calendar")


async def remote_labels(
    ctx: Ctx, connectors: Connectors, account_id: uuid.UUID
) -> list[RemoteCollection]:
    """The account's mail labels, read live at the provider (names only)."""
    return await remote_collections(ctx, connectors, account_id, "mail_label")


async def remote_collections(
    ctx: Ctx, connectors: Connectors, account_id: uuid.UUID, kind: SourceKind
) -> list[RemoteCollection]:
    account = await own_account(ctx, account_id)
    if account.status != "active":
        raise ProblemError(409, "connector_reauth_required", "This account must be connected again")
    provider = provider_for(connectors, account.provider)
    capability = CAPABILITY[kind]
    if capability not in provider.capabilities_in(account.scopes):
        raise ProblemError(
            403,
            "connector_capability_missing",
            "This reading was not granted at the provider",
            extra={"capability": capability},
        )
    try:
        token = await tokens.access_token(ctx.database, ctx.settings, provider, account.id)
        if kind == "calendar":
            return await _calendar_adapter(connectors, account).list_calendars(token)
        return await _mail_adapter(connectors, account).list_labels(token)
    except ConnectorAuthError as exc:
        raise ProblemError(
            409, "connector_reauth_required", "This account must be connected again"
        ) from exc
    except ConnectorError as exc:
        raise ProblemError(
            503,
            "connector_unavailable",
            "The provider did not answer",
            extra={"reason": exc.reason},
        ) from exc


async def map_calendar(
    ctx: Ctx,
    connectors: Connectors,
    *,
    account_id: uuid.UUID,
    provider_ref: str,
    space_id: uuid.UUID,
    kind: SourceKind = "calendar",
) -> ConnectorSource:
    """Feeds `space_id` with this calendar or label (one space each); first sync queued."""
    account = await require_capability(ctx, connectors, account_id, space_id, CAPABILITY[kind])
    remotes = {
        c.provider_ref: c for c in await remote_collections(ctx, connectors, account_id, kind)
    }
    remote = remotes.get(provider_ref)
    if remote is None:
        if kind == "calendar":
            raise not_found("calendar_not_found", "This calendar is not in the account")
        raise not_found("label_not_found", "This label is not in the account")
    source = await ctx.db.scalar(
        select(ConnectorSource).where(
            ConnectorSource.account_id == account.id,
            ConnectorSource.kind == kind,
            ConnectorSource.provider_ref == provider_ref,
        )
    )
    if source is not None and source.space_id != space_id:
        raise ProblemError(
            409,
            "calendar_already_mapped" if kind == "calendar" else "label_already_mapped",
            "This source already feeds another space: remove it there first",
        )
    if source is None:
        source = ConnectorSource(
            user_id=ctx.principal.user_id,
            space_id=space_id,
            account_id=account.id,
            kind=kind,
            provider_ref=provider_ref,
            name=remote.name,
        )
        ctx.db.add(source)
    source.name = remote.name
    source.enabled = True
    source.last_error = None
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="connector.source.add",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type="connector_source",
        target_id=source.id,
        details={"kind": kind},
    )
    return source


async def own_source(ctx: Ctx, source_id: uuid.UUID) -> ConnectorSource:
    source = await ctx.db.scalar(
        select(ConnectorSource).where(
            ConnectorSource.id == source_id,
            ConnectorSource.user_id == ctx.principal.user_id,
            ConnectorSource.space_id.in_(ctx.principal.space_ids),
        )
    )
    if source is None:
        raise not_found("source_not_found", "Source not found")
    return source


async def remove_source(ctx: Ctx, source_id: uuid.UUID) -> None:
    """Stops feeding the space and deletes what was synced from this calendar or label
    (documents imported from its e-mails stay)."""
    source = await own_source(ctx, source_id)
    await ctx.db.delete(source)
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="connector.source.remove",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=source.space_id,
        target_type="connector_source",
        target_id=source_id,
        details={"kind": source.kind},
    )


def queue_sync(source_id: uuid.UUID, kind: str = "calendar") -> bool:
    """Best effort: the periodic sync catches up if the broker is down."""
    return enqueue(SYNC_ACTORS.get(kind, SYNC_ACTORS["calendar"]), str(source_id), queue=SYNC_QUEUE)
