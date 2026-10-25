"""Calendars (and later mail labels) of a connected account, mapped to a space (task 14).

Mapping a calendar needs the account's calendar.read consent in that space: the owner
chooses which calendar feeds which space, within what was consented. Removing a mapping
deletes its synced rows (cascade); withdrawing the consent only stops the sync.
"""

import uuid

from sqlalchemy import select

from lifeos.core.errors import ProblemError, not_found
from lifeos.modules.audit import service as audit
from lifeos.modules.connectors import Connectors, tokens
from lifeos.modules.connectors.base import (
    CalendarAdapter,
    ConnectorAuthError,
    ConnectorError,
    RemoteCollection,
)
from lifeos.modules.connectors.models import ConnectorAccount, ConnectorSource
from lifeos.modules.connectors.service import own_account, provider_for, require_capability
from lifeos.modules.guardian.context import Ctx
from lifeos.workers.queue import enqueue

SYNC_ACTOR = "sync_calendar_source"
SYNC_QUEUE = "connectors"


def _calendar_adapter(connectors: Connectors, account: ConnectorAccount) -> CalendarAdapter:
    provider = provider_for(connectors, account.provider)
    if not hasattr(provider, "list_events"):
        raise ProblemError(409, "connector_capability_missing", "No calendar for this account")
    adapter: CalendarAdapter = provider  # type: ignore[assignment]
    return adapter


async def remote_calendars(
    ctx: Ctx, connectors: Connectors, account_id: uuid.UUID
) -> list[RemoteCollection]:
    """The account's calendars, read live at the provider (names only)."""
    account = await own_account(ctx, account_id)
    if account.status != "active":
        raise ProblemError(409, "connector_reauth_required", "This account must be connected again")
    provider = provider_for(connectors, account.provider)
    if "calendar.read" not in provider.capabilities_in(account.scopes):
        raise ProblemError(
            403,
            "connector_capability_missing",
            "Calendar reading was not granted at the provider",
            extra={"capability": "calendar.read"},
        )
    adapter = _calendar_adapter(connectors, account)
    try:
        token = await tokens.access_token(ctx.database, ctx.settings, provider, account.id)
        return await adapter.list_calendars(token)
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
) -> ConnectorSource:
    """Feeds `space_id` with this calendar (one space per calendar); first sync queued."""
    account = await require_capability(ctx, connectors, account_id, space_id, "calendar.read")
    calendars = {c.provider_ref: c for c in await remote_calendars(ctx, connectors, account_id)}
    remote = calendars.get(provider_ref)
    if remote is None:
        raise not_found("calendar_not_found", "This calendar is not in the account")
    source = await ctx.db.scalar(
        select(ConnectorSource).where(
            ConnectorSource.account_id == account.id,
            ConnectorSource.kind == "calendar",
            ConnectorSource.provider_ref == provider_ref,
        )
    )
    if source is not None and source.space_id != space_id:
        raise ProblemError(
            409,
            "calendar_already_mapped",
            "This calendar already feeds another space: remove it there first",
        )
    if source is None:
        source = ConnectorSource(
            user_id=ctx.principal.user_id,
            space_id=space_id,
            account_id=account.id,
            kind="calendar",
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
        details={"kind": "calendar"},
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
    """Stops feeding the space and deletes what was synced from this calendar."""
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


def queue_sync(source_id: uuid.UUID) -> bool:
    """Best effort: the periodic sync catches up if the broker is down."""
    return enqueue(SYNC_ACTOR, str(source_id), queue=SYNC_QUEUE)
