"""Calendar sync (task 14): each mapped calendar is read again over a rolling window.

A pass reads every occurrence of the window, page by page, and upserts each one by its
provider id (unique per source): a resync updates an occurrence in place, never duplicates
it. Each page is committed with the provider's token of the next page, so an interrupted
pass (worker restart, provider outage) resumes where it stopped. Only a complete pass
removes what the provider no longer returned (moved out of the window, cancelled,
deleted): mark and sweep by pass number. A pass that cannot finish removes nothing.

The provider expands recurring series in their own time zone: an occurrence keeps its
local time across daylight saving changes, and is stored as UTC instants. All-day
occurrences stay dates.

Nothing runs without the owner's consent: an active account, a calendar.read consent in
the source's space, and the scope granted at the provider. No content is logged nor
audited (counts only).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.calendar.models import CalendarEvent
from lifeos.modules.connectors import Connectors, tokens
from lifeos.modules.connectors.base import (
    CalendarAdapter,
    ConnectorAuthError,
    ConnectorError,
    ConnectorGoneError,
    RemoteEvent,
)
from lifeos.modules.connectors.models import ConnectorAccount, ConnectorGrant, ConnectorSource

log = get_logger(__name__)

# A pass holds its source this long at most between two pages (a crashed worker frees it).
LEASE = timedelta(minutes=10)
# Pages per pass at most: 250 occurrences each, a year of a busy calendar fits largely.
MAX_PAGES = 200

Outcome = Literal["synced", "skipped", "failed", "partial"]


@dataclass
class SyncResult:
    outcome: Outcome
    upserted: int = 0
    removed: int = 0
    pages: int = 0
    error: str | None = None
    details: dict[str, int] = field(default_factory=dict)


def window(settings: Settings, now: datetime) -> tuple[datetime, datetime]:
    """[start, end) of the synced window, on whole UTC days: the same all day long, so a
    pass interrupted and resumed the same day keeps valid page tokens."""
    today = datetime.combine(now.astimezone(UTC).date(), time(), tzinfo=UTC)
    return (
        today - timedelta(days=settings.calendar_past_days),
        today + timedelta(days=settings.calendar_future_days + 1),
    )


@dataclass(frozen=True)
class _Claim:
    source_id: uuid.UUID
    user_id: uuid.UUID
    space_id: uuid.UUID
    account_id: uuid.UUID
    calendar_ref: str
    page_token: str | None
    generation: int


async def _claim(database: Database, source_id: uuid.UUID, now: datetime) -> _Claim | None:
    """Takes the source for one pass, unless another pass holds it."""
    async with database.sessionmaker() as session, session.begin():
        row = (
            await session.execute(
                update(ConnectorSource)
                .where(
                    ConnectorSource.id == source_id,
                    ConnectorSource.kind == "calendar",
                    ConnectorSource.enabled.is_(True),
                    or_(ConnectorSource.lease_until.is_(None), ConnectorSource.lease_until < now),
                )
                .values(lease_until=now + LEASE)
                .returning(
                    ConnectorSource.user_id,
                    ConnectorSource.space_id,
                    ConnectorSource.account_id,
                    ConnectorSource.provider_ref,
                    ConnectorSource.page_token,
                    ConnectorSource.sync_generation,
                )
            )
        ).one_or_none()
    if row is None:
        return None
    user_id, space_id, account_id, ref, page_token, generation = row
    # A new pass, or the one in progress (a page token was kept).
    return _Claim(
        source_id=source_id,
        user_id=user_id,
        space_id=space_id,
        account_id=account_id,
        calendar_ref=ref,
        page_token=page_token,
        generation=generation if page_token else generation + 1,
    )


async def _release(
    database: Database,
    claim: _Claim,
    *,
    error: str | None,
    restart: bool = False,
) -> None:
    """Frees the source; `restart`: the next pass starts over (the page is useless)."""
    values: dict[str, object] = {"lease_until": None, "last_error": error}
    if restart:
        values["page_token"] = None
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(ConnectorSource).where(ConnectorSource.id == claim.source_id).values(**values)
        )


async def _consent(
    session: AsyncSession, connectors: Connectors, claim: _Claim, now: datetime
) -> tuple[ConnectorAccount, CalendarAdapter] | str:
    """The account and its calendar adapter, or why the source may not be read."""
    account = await session.get(ConnectorAccount, claim.account_id)
    if account is None or account.status != "active":
        return "account_inactive"
    provider = connectors.get(account.provider)
    if provider is None or not hasattr(provider, "list_events"):
        return "provider_unavailable"
    if "calendar.read" not in provider.capabilities_in(account.scopes):
        return "scope_missing"
    grant = await session.scalar(
        select(ConnectorGrant.id).where(
            ConnectorGrant.account_id == account.id,
            ConnectorGrant.space_id == claim.space_id,
            ConnectorGrant.capability == "calendar.read",
            ConnectorGrant.revoked_at.is_(None),
            or_(ConnectorGrant.expires_at.is_(None), ConnectorGrant.expires_at > now),
        )
    )
    if grant is None:
        return "consent_missing"
    adapter: CalendarAdapter = provider  # type: ignore[assignment]
    return account, adapter


def _row(claim: _Claim, event: RemoteEvent, generation: int) -> dict[str, object]:
    return {
        "user_id": claim.user_id,
        "space_id": claim.space_id,
        "source_id": claim.source_id,
        "provider_event_id": event.provider_id,
        "series_id": event.series_id,
        "status": event.status,
        "summary": event.summary,
        "description": event.description,
        "location": event.location,
        "starts_at": event.starts_at.astimezone(UTC) if event.starts_at else None,
        "ends_at": event.ends_at.astimezone(UTC) if event.ends_at else None,
        "start_date": event.start_date,
        "end_date": event.end_date,
        "timezone": event.timezone,
        "busy": event.busy,
        "organizer_self": event.organizer_self,
        "response": event.response,
        "attendees": event.attendees,
        "etag": event.etag,
        "provider_updated_at": event.updated_at,
        "seen_generation": generation,
    }


async def _store_page(
    database: Database,
    claim: _Claim,
    events: list[RemoteEvent],
    generation: int,
    next_page: str | None,
    now: datetime,
) -> int:
    """One page in one transaction, with where to resume."""
    rows = [_row(claim, e, generation) for e in events]
    async with database.sessionmaker() as session, session.begin():
        if rows:
            stmt = insert(CalendarEvent).values(rows)
            updated: dict[str, Any] = {
                column: stmt.excluded[column]
                for column in rows[0]
                if column not in ("user_id", "source_id", "provider_event_id")
            }
            updated["updated_at"] = func.now()
            await session.execute(
                stmt.on_conflict_do_update(constraint="uq_calendar_events_provider", set_=updated)
            )
        await session.execute(
            update(ConnectorSource)
            .where(ConnectorSource.id == claim.source_id)
            .values(
                page_token=next_page,
                sync_generation=generation,
                lease_until=now + LEASE,
            )
        )
    return len(rows)


async def _sweep(database: Database, claim: _Claim, generation: int, now: datetime) -> int:
    """After a complete pass: what the provider no longer returned is gone."""
    async with database.sessionmaker() as session, session.begin():
        result = await session.execute(
            delete(CalendarEvent).where(
                CalendarEvent.source_id == claim.source_id,
                CalendarEvent.seen_generation < generation,
            )
        )
        await session.execute(
            update(ConnectorSource)
            .where(ConnectorSource.id == claim.source_id)
            .values(page_token=None, last_synced_at=now, last_error=None, lease_until=None)
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _audit(database: Database, claim: _Claim, result: SyncResult) -> None:
    details: dict[str, audit.DetailValue] = {
        "upserted": result.upserted,
        "removed": result.removed,
        "pages": result.pages,
    }
    if result.error:
        details["error"] = result.error
    await audit.record_detached(
        database,
        action="calendar.sync",
        outcome="success" if result.outcome == "synced" else "failure",
        actor_type="system",
        actor_id="calendar_sync",
        user_id=claim.user_id,
        space_id=claim.space_id,
        target_type="connector_source",
        target_id=claim.source_id,
        details=details,
    )


async def sync_source(
    database: Database,
    settings: Settings,
    connectors: Connectors,
    source_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> SyncResult:
    """One pass over one mapped calendar (or the end of an interrupted one)."""
    now = now or utcnow()
    claim = await _claim(database, source_id, now)
    if claim is None:
        return SyncResult("skipped", error="busy_or_disabled")
    async with database.sessionmaker() as session:
        consent = await _consent(session, connectors, claim, now)
    if isinstance(consent, str):
        await _release(database, claim, error=consent)
        log.info("calendar_sync_skipped", source=str(source_id), reason=consent)
        return SyncResult("skipped", error=consent)
    account, adapter = consent
    provider = connectors[account.provider]
    time_min, time_max = window(settings, now)
    result = SyncResult("synced")
    page = claim.page_token
    generation = claim.generation
    retried_token = restarted = False
    try:
        for _ in range(MAX_PAGES):
            access_token = await tokens.access_token(database, settings, provider, account.id)
            try:
                events = await adapter.list_events(
                    access_token,
                    claim.calendar_ref,
                    time_min=time_min,
                    time_max=time_max,
                    page=page,
                )
            except ConnectorAuthError as exc:
                # The access token was refused before its expiry: refreshed once.
                if exc.reason != "access_token_rejected" or retried_token:
                    raise
                retried_token = True
                await tokens.forget_access_token(database, account.id)
                continue
            except ConnectorError as exc:
                if page is not None and exc.reason == "http_400" and not restarted:
                    # A page token the provider no longer takes (the interrupted pass
                    # started another day, with another window): a new pass, from the start.
                    restarted = True
                    page = None
                    generation = claim.generation + 1 if claim.page_token else claim.generation
                    continue
                raise
            result.pages += 1
            result.upserted += await _store_page(
                database, claim, events.events, generation, events.next_page, now
            )
            page = events.next_page
            if page is None:
                break
        else:
            # Too many pages: the pass stops here and resumes at the next run.
            result.outcome, result.error = "partial", "too_many_pages"
            await _release(database, claim, error="too_many_pages")
            await _audit(database, claim, result)
            return result
    except ConnectorGoneError:
        result.outcome, result.error = "failed", "calendar_gone"
        await _release(database, claim, error="calendar_gone", restart=True)
    except ConnectorError as exc:
        # Unavailable, quota, account to reconnect: the pass resumes at the next run from
        # the last page stored; nothing is removed meanwhile.
        result.outcome, result.error = "failed", exc.reason[:64]
        await _release(database, claim, error=result.error)
    else:
        result.removed = await _sweep(database, claim, generation, now)
    log.info(
        "calendar_sync",
        source=str(source_id),
        outcome=result.outcome,
        upserted=result.upserted,
        removed=result.removed,
        pages=result.pages,
        error=result.error,
    )
    await _audit(database, claim, result)
    return result


async def due_sources(database: Database, settings: Settings, now: datetime) -> list[uuid.UUID]:
    """Mapped calendars not synced for `calendar_sync_minutes` (or never)."""
    stale = now - timedelta(minutes=settings.calendar_sync_minutes)
    async with database.sessionmaker() as session:
        return list(
            await session.scalars(
                select(ConnectorSource.id)
                .where(
                    ConnectorSource.kind == "calendar",
                    ConnectorSource.enabled.is_(True),
                    or_(
                        ConnectorSource.last_synced_at.is_(None),
                        ConnectorSource.last_synced_at < stale,
                        ConnectorSource.page_token.is_not(None),
                    ),
                )
                .order_by(ConnectorSource.last_synced_at.asc().nulls_first())
            )
        )


async def sync_due(
    database: Database,
    settings: Settings,
    connectors: Connectors,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Every calendar due, one after the other (a few per account: no burst)."""
    now = now or utcnow()
    counts: dict[str, int] = {}
    for source_id in await due_sources(database, settings, now):
        result = await sync_source(database, settings, connectors, source_id, now=now)
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
    return counts
