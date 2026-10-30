"""Mail sync (task 17): the labels the owner mapped, each into one space.

A first pass (full) lists the label's messages of the last `mail_retention_days` days
and copies the ones not copied yet, page by page. The provider's change cursor is taken
BEFORE that pass, so what changes meanwhile is replayed after it. The next passes only
read the changes since the cursor (Gmail history): new messages, deletions, labels added
or removed, moves to the spam or the bin. A cursor the provider no longer keeps starts a
bounded full pass again, which removes, once complete, what is no longer in the label.

Same guarantees as the calendar (task 14): one pass at a time per source (lease), each
page committed with where to resume, nothing without the owner's consent (active account,
`mail.read` consent in the source's space, scope granted), no content logged or audited.

A message is copied into its source's space only: the same message in two labels mapped
to two spaces is two copies. Attachments are listed, never downloaded here (ADR 0011).
The sensitivity of each message (identity, health, bank) is detected locally at once.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Literal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.connectors import Connectors, tokens
from lifeos.modules.connectors.base import (
    EXCLUDED_LABELS,
    ConnectorAuthError,
    ConnectorError,
    ConnectorGoneError,
    CursorExpiredError,
    MailAdapter,
    RemoteMail,
)
from lifeos.modules.connectors.models import ConnectorAccount, ConnectorGrant, ConnectorSource
from lifeos.modules.documents.sensitivity import classify
from lifeos.modules.mail.models import MailMessage

log = get_logger(__name__)

LEASE = timedelta(minutes=10)
# Pages per pass at most (100 messages per page for a full pass, 500 changes otherwise).
MAX_PAGES = 50
# Messages read at the same time from the provider.
CONCURRENCY = 5
# Page token prefixes: which kind of pass is in progress.
_FULL, _HISTORY = "full:", "history:"

Outcome = Literal["synced", "skipped", "failed", "partial"]


@dataclass
class SyncResult:
    outcome: Outcome
    mode: str = "history"
    added: int = 0
    removed: int = 0
    pages: int = 0
    resynced: bool = False
    error: str | None = None
    details: dict[str, int] = field(default_factory=dict)


@dataclass
class _Claim:
    source_id: uuid.UUID
    user_id: uuid.UUID
    space_id: uuid.UUID
    account_id: uuid.UUID
    label_ref: str
    page_token: str | None
    cursor: str | None
    generation: int


async def _claim(database: Database, source_id: uuid.UUID, now: datetime) -> _Claim | None:
    async with database.sessionmaker() as session, session.begin():
        row = (
            await session.execute(
                update(ConnectorSource)
                .where(
                    ConnectorSource.id == source_id,
                    ConnectorSource.kind == "mail_label",
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
                    ConnectorSource.sync_cursor,
                    ConnectorSource.sync_generation,
                )
            )
        ).one_or_none()
    if row is None:
        return None
    user_id, space_id, account_id, ref, page_token, cursor, generation = row
    return _Claim(source_id, user_id, space_id, account_id, ref, page_token, cursor, generation)


async def _set(database: Database, source_id: uuid.UUID, **values: object) -> None:
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(ConnectorSource).where(ConnectorSource.id == source_id).values(**values)
        )


async def _consent(
    session: AsyncSession, connectors: Connectors, claim: _Claim, now: datetime
) -> tuple[ConnectorAccount, MailAdapter] | str:
    """The account and its mail adapter, or why the label may not be read."""
    account = await session.get(ConnectorAccount, claim.account_id)
    if account is None or account.status != "active":
        return "account_inactive"
    provider = connectors.get(account.provider)
    if provider is None or not hasattr(provider, "list_mail_changes"):
        return "provider_unavailable"
    if "mail.read" not in provider.capabilities_in(account.scopes):
        return "scope_missing"
    grant = await session.scalar(
        select(ConnectorGrant.id).where(
            ConnectorGrant.account_id == account.id,
            ConnectorGrant.space_id == claim.space_id,
            ConnectorGrant.capability == "mail.read",
            ConnectorGrant.revoked_at.is_(None),
            or_(ConnectorGrant.expires_at.is_(None), ConnectorGrant.expires_at > now),
        )
    )
    if grant is None:
        return "consent_missing"
    adapter: MailAdapter = provider  # type: ignore[assignment]
    return account, adapter


def _row(settings: Settings, claim: _Claim, mail: RemoteMail, generation: int) -> dict[str, object]:
    received = mail.received_at.astimezone(UTC)
    found = classify(f"{mail.subject}\n{mail.body_text}")
    return {
        "user_id": claim.user_id,
        "space_id": claim.space_id,
        "source_id": claim.source_id,
        "provider_message_id": mail.provider_id,
        "thread_id": mail.thread_id,
        "from_address": mail.from_address,
        "from_name": mail.from_name,
        "to_addresses": list(mail.to),
        "cc_addresses": list(mail.cc),
        "subject": mail.subject,
        "body_text": mail.body_text,
        "body_truncated": mail.body_truncated,
        "received_at": received,
        "attachments": [
            {"part_id": a.part_id, "filename": a.filename, "mime": a.mime, "size": a.size}
            for a in mail.attachments
        ],
        "sensitivity": found.categories,
        "purge_after": received + timedelta(days=settings.mail_retention_days),
        "seen_generation": generation,
        "message_id_header": mail.message_id_header,
    }


class _Pass:
    """One pass over one label: reads, then stores page by page."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        connectors: Connectors,
        claim: _Claim,
        account: ConnectorAccount,
        adapter: MailAdapter,
        now: datetime,
    ) -> None:
        self.database = database
        self.settings = settings
        self.provider = connectors[account.provider]
        self.account = account
        self.adapter = adapter
        self.claim = claim
        self.now = now
        self.oldest = now - timedelta(days=settings.mail_retention_days)
        self.retried_token = False

    async def token(self) -> str:
        return await tokens.access_token(
            self.database, self.settings, self.provider, self.account.id
        )

    async def call[T](self, run: Callable[[str], Awaitable[T]]) -> T:
        """A provider call; an access token refused before its expiry is renewed once."""
        while True:
            try:
                return await run(await self.token())
            except ConnectorAuthError as exc:
                if exc.reason != "access_token_rejected" or self.retried_token:
                    raise
                self.retried_token = True
                await tokens.forget_access_token(self.database, self.account.id)

    async def stored(self, refs: list[str]) -> set[str]:
        if not refs:
            return set()
        async with self.database.sessionmaker() as session:
            return set(
                await session.scalars(
                    select(MailMessage.provider_message_id).where(
                        MailMessage.source_id == self.claim.source_id,
                        MailMessage.provider_message_id.in_(refs),
                    )
                )
            )

    async def read(self, refs: list[str]) -> dict[str, RemoteMail | None]:
        """The messages, a few at a time (None: gone at the provider)."""
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(ref: str) -> tuple[str, RemoteMail | None]:
            async with gate:
                return ref, await self.call(partial(self.adapter.get_mail, message_id=ref))

        return dict(await asyncio.gather(*(one(ref) for ref in refs)))

    def keeps(self, mail: RemoteMail | None) -> bool:
        return (
            mail is not None
            and self.claim.label_ref in mail.label_ids
            and not mail.label_ids & EXCLUDED_LABELS
            and mail.received_at >= self.oldest
        )

    async def store(
        self,
        mails: list[RemoteMail],
        *,
        seen: list[str],
        removed: list[str],
        generation: int,
        source_values: dict[str, object],
    ) -> tuple[int, int]:
        """One page in one transaction, with where to resume. Content never changes at
        the provider: a message already copied only gets its pass number."""
        rows = [_row(self.settings, self.claim, m, generation) for m in mails]
        source_id = self.claim.source_id
        async with self.database.sessionmaker() as session, session.begin():
            if rows:
                stmt = insert(MailMessage).values(rows)
                await session.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_mail_messages_provider",
                        set_={"seen_generation": stmt.excluded.seen_generation},
                    )
                )
            if seen:
                await session.execute(
                    update(MailMessage)
                    .where(
                        MailMessage.source_id == source_id,
                        MailMessage.provider_message_id.in_(seen),
                    )
                    .values(seen_generation=generation, updated_at=func.now())
                )
            gone = 0
            if removed:
                result = await session.execute(
                    delete(MailMessage).where(
                        MailMessage.source_id == source_id,
                        MailMessage.provider_message_id.in_(removed),
                    )
                )
                gone = int(result.rowcount or 0)  # type: ignore[attr-defined]
            await session.execute(
                update(ConnectorSource)
                .where(ConnectorSource.id == source_id)
                .values(lease_until=self.now + LEASE, **source_values)
            )
        return len(rows), gone

    async def full(self, result: SyncResult, page: str | None, restart: bool) -> bool:
        """A full pass (or its end); True when complete. `restart`: a new pass, with a
        new cursor and a new pass number."""
        claim = self.claim
        generation = claim.generation
        if restart:
            cursor = await self.call(self.adapter.mail_cursor)
            generation += 1
            page = None
            await _set(
                self.database,
                claim.source_id,
                sync_cursor=cursor,
                sync_generation=generation,
                page_token=_FULL,
                lease_until=self.now + LEASE,
            )
            claim.cursor, claim.generation = cursor, generation
        result.mode = "full"
        for _ in range(MAX_PAGES):
            listing = await self.call(
                partial(
                    self.adapter.list_mail_ids,
                    label_ref=claim.label_ref,
                    newer_than_days=self.settings.mail_retention_days,
                    page=page,
                )
            )
            known = await self.stored(listing.ids)
            fetched = await self.read([ref for ref in listing.ids if ref not in known])
            kept = [m for m in fetched.values() if m is not None and self.keeps(m)]
            added, _ = await self.store(
                kept,
                seen=sorted(known),
                removed=[],
                generation=generation,
                source_values={"page_token": _FULL + (listing.next_page or "")},
            )
            result.added += added
            result.pages += 1
            page = listing.next_page
            if page is None:
                return True
        return False

    async def sweep(self, result: SyncResult) -> None:
        """After a complete full pass: copies no longer in the label (or too old)."""
        async with self.database.sessionmaker() as session, session.begin():
            gone = await session.execute(
                delete(MailMessage).where(
                    MailMessage.source_id == self.claim.source_id,
                    MailMessage.seen_generation < self.claim.generation,
                )
            )
            result.removed += int(gone.rowcount or 0)  # type: ignore[attr-defined]

    async def history(self, result: SyncResult, page: str | None) -> bool:
        """The changes since the cursor; True when the last page is stored."""
        claim = self.claim
        assert claim.cursor is not None
        for _ in range(MAX_PAGES):
            changes = await self.call(
                partial(
                    self.adapter.list_mail_changes,
                    cursor=claim.cursor,
                    label_ref=claim.label_ref,
                    page=page,
                )
            )
            known = await self.stored(changes.added)
            fetched = await self.read([ref for ref in changes.added if ref not in known])
            kept = [m for m in fetched.values() if m is not None and self.keeps(m)]
            dropped = [ref for ref, m in fetched.items() if not self.keeps(m)]
            values: dict[str, object] = {"page_token": None}
            if changes.next_page is not None:
                values["page_token"] = _HISTORY + changes.next_page
            elif changes.cursor is not None:
                values["sync_cursor"] = changes.cursor
            added, removed = await self.store(
                kept,
                seen=[],
                removed=sorted(set(changes.removed) | set(dropped)),
                generation=claim.generation,
                source_values=values,
            )
            result.added += added
            result.removed += removed
            result.pages += 1
            page = changes.next_page
            if page is None:
                return True
        return False


async def _audit(database: Database, claim: _Claim, result: SyncResult) -> None:
    details: dict[str, audit.DetailValue] = {
        "mode": result.mode,
        "added": result.added,
        "removed": result.removed,
        "pages": result.pages,
        "resynced": result.resynced,
    }
    if result.error:
        details["error"] = result.error
    await audit.record_detached(
        database,
        action="mail.sync",
        outcome="success" if result.outcome == "synced" else "failure",
        actor_type="system",
        actor_id="mail_sync",
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
    """One pass over one mapped label (or the end of an interrupted one)."""
    now = now or utcnow()
    claim = await _claim(database, source_id, now)
    if claim is None:
        return SyncResult("skipped", error="busy_or_disabled")
    async with database.sessionmaker() as session:
        consent = await _consent(session, connectors, claim, now)
    if isinstance(consent, str):
        await _set(database, source_id, lease_until=None, last_error=consent)
        log.info("mail_sync_skipped", source=str(source_id), reason=consent)
        return SyncResult("skipped", error=consent)
    account, adapter = consent
    run = _Pass(database, settings, connectors, claim, account, adapter, now)
    result = SyncResult("synced")
    token = claim.page_token or ""
    try:
        if claim.cursor is None:
            complete = await run.full(result, None, restart=True)
        elif token.startswith(_FULL):
            complete = await run.full(result, token.removeprefix(_FULL) or None, restart=False)
        else:
            try:
                page = token.removeprefix(_HISTORY) if token.startswith(_HISTORY) else None
                complete = await run.history(result, page or None)
            except CursorExpiredError:
                # The provider no longer keeps that far back: a full pass, bounded.
                result.resynced = True
                complete = await run.full(result, None, restart=True)
        if not complete:
            result.outcome, result.error = "partial", "too_many_pages"
            await _set(database, source_id, lease_until=None, last_error="too_many_pages")
        elif result.mode == "full":
            await run.sweep(result)
    except ConnectorGoneError as exc:
        # The label no longer exists: the next pass starts over.
        result.outcome, result.error = "failed", "label_gone"
        if isinstance(exc, CursorExpiredError):
            result.error = "history_expired"
        await _set(
            database,
            source_id,
            lease_until=None,
            last_error=result.error,
            page_token=None,
            sync_cursor=None,
        )
    except ConnectorError as exc:
        # Unavailable, quota, account to reconnect: resumes from the last page stored.
        result.outcome, result.error = "failed", exc.reason[:64]
        await _set(database, source_id, lease_until=None, last_error=result.error)
    if result.outcome == "synced":
        await _set(
            database,
            source_id,
            lease_until=None,
            last_error=None,
            last_synced_at=now,
            page_token=None,
        )
    log.info(
        "mail_sync",
        source=str(source_id),
        outcome=result.outcome,
        mode=result.mode,
        added=result.added,
        removed=result.removed,
        pages=result.pages,
        resynced=result.resynced,
        error=result.error,
    )
    await _audit(database, claim, result)
    return result


async def due_sources(database: Database, settings: Settings, now: datetime) -> list[uuid.UUID]:
    """Mapped labels not read for `mail_sync_minutes` (or never, or interrupted)."""
    stale = now - timedelta(minutes=settings.mail_sync_minutes)
    async with database.sessionmaker() as session:
        return list(
            await session.scalars(
                select(ConnectorSource.id)
                .where(
                    ConnectorSource.kind == "mail_label",
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
    now = now or utcnow()
    counts: dict[str, int] = {}
    for source_id in await due_sources(database, settings, now):
        result = await sync_source(database, settings, connectors, source_id, now=now)
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
    return counts
