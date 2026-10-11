"""Minimal context packet for the assistant: hybrid, temporal, restricted to one space."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Text, cast, func, select
from sqlalchemy.dialects.postgresql import TSQUERY

from lifeos.core.security import utcnow
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.memory.models import Fact, Memory
from lifeos.modules.retrieval import temporal
from lifeos.modules.retrieval.hybrid import Ref, fuse, vector_refs


@dataclass(frozen=True)
class ContextItem:
    kind: str
    text: str
    origin: str
    date: datetime


def any_word_query(query: str) -> ColumnElement[Any]:
    """tsquery matching ANY significant word of `query` (better recall for context)."""
    plain = func.plainto_tsquery("lifeos_fr", query)
    return cast(func.replace(cast(plain, Text), "&", "|"), TSQUERY)


def _bounds(period: temporal.DateRange, tz: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz)
    return (
        datetime.combine(period.start, time.min, tzinfo=zone),
        datetime.combine(period.end + timedelta(days=1), time.min, tzinfo=zone),
    )


async def relevant_context(
    ctx: Ctx, *, space_id: uuid.UUID, query: str, limit: int = 8
) -> list[ContextItem]:
    """Active facts, memories and journal entries of ONE space relevant to the query.

    Full-text and vector rankings are fused; a French temporal expression in the query
    ("la semaine dernière", "en mars") restricts dated items to that period. Protected,
    important memories are always included. Respects the principal's read rights.
    """
    principal = ctx.principal
    principal.require_space(space_id)
    uid = principal.user_id
    tz = ctx.user.timezone
    today = utcnow().astimezone(ZoneInfo(tz)).date()
    period = temporal.parse(query, today)
    tsquery = any_word_query(query)
    readable = [
        t
        for t, resource in (("fact", "facts"), ("memory", "memories"), ("journal", "journal"))
        if principal.can(space_id, resource, "read")
    ]

    fts: list[Ref] = []
    if "fact" in readable:
        stmt = select(Fact.id).where(
            Fact.user_id == uid,
            Fact.space_id == space_id,
            Fact.status == "active",
            Fact.search_vector.op("@@")(tsquery),
        )
        if period:
            since, until = _bounds(period, tz)
            day = func.coalesce(Fact.occurred_at, Fact.created_at)
            stmt = stmt.where(day >= since, day < until)
        rows = await ctx.db.scalars(
            stmt.order_by(func.ts_rank(Fact.search_vector, tsquery).desc()).limit(limit)
        )
        fts += [Ref("fact", i) for i in rows]
    if "memory" in readable:
        rows = await ctx.db.scalars(
            select(Memory.id)
            .where(
                Memory.user_id == uid,
                Memory.space_id == space_id,
                Memory.search_vector.op("@@")(tsquery),
            )
            .order_by(func.ts_rank(Memory.search_vector, tsquery).desc())
            .limit(limit)
        )
        fts += [Ref("memory", i) for i in rows]
    if "journal" in readable:
        stmt = select(JournalEntry.id).where(
            JournalEntry.user_id == uid, JournalEntry.space_id == space_id
        )
        if period:
            # "What did I do last week?": the entries of that period, latest first.
            stmt = stmt.where(
                JournalEntry.entry_date >= period.start, JournalEntry.entry_date <= period.end
            ).order_by(JournalEntry.entry_date.desc())
        else:
            stmt = stmt.where(JournalEntry.search_vector.op("@@")(tsquery)).order_by(
                func.ts_rank(JournalEntry.search_vector, tsquery).desc()
            )
        fts += [Ref("journal", i) for i in await ctx.db.scalars(stmt.limit(5))]

    vectors = await vector_refs(
        ctx.db,
        ctx.embedder,
        query,
        user_id=uid,
        space_ids=[space_id],
        object_types=readable,
        limit=limit * 2,
    )
    ranked = fuse(fts, vectors)

    items = await _load(ctx, space_id, ranked, period, tz)
    items = items[: limit + 4]
    if "memory" in readable:
        # Important protected memories are part of every context.
        pinned = await ctx.db.scalars(
            select(Memory)
            .where(
                Memory.user_id == uid,
                Memory.space_id == space_id,
                Memory.protected.is_(True),
                Memory.importance >= 4,
            )
            .order_by(Memory.importance.desc(), Memory.updated_at.desc())
            .limit(3)
        )
        texts = {item.text for item in items}
        for memory in pinned:
            item = _memory_item(memory)
            if item.text not in texts:
                items.append(item)
    return items


def _memory_item(memory: Memory) -> ContextItem:
    return ContextItem(
        kind="memory",
        text=f"{memory.title} — {memory.content[:500]}",
        origin=memory.origin,
        date=memory.updated_at,
    )


async def _load(
    ctx: Ctx,
    space_id: uuid.UUID,
    refs: list[Ref],
    period: temporal.DateRange | None,
    tz: str,
) -> list[ContextItem]:
    uid = ctx.principal.user_id
    ids: dict[str, list[uuid.UUID]] = {}
    for ref in refs:
        ids.setdefault(ref.object_type, []).append(ref.object_id)
    loaded: dict[Ref, ContextItem] = {}
    zone = ZoneInfo(tz)

    def in_period(moment: datetime | date) -> bool:
        if period is None:
            return True
        day = moment.astimezone(zone).date() if isinstance(moment, datetime) else moment
        return period.start <= day <= period.end

    if ids.get("fact"):
        for fact in await ctx.db.scalars(
            select(Fact).where(
                Fact.id.in_(ids["fact"]),
                Fact.user_id == uid,
                Fact.space_id == space_id,
                Fact.status == "active",
            )
        ):
            when = fact.occurred_at or fact.created_at
            if in_period(when):
                loaded[Ref("fact", fact.id)] = ContextItem(
                    kind=fact.kind, text=fact.statement, origin=fact.origin, date=when
                )
    if ids.get("memory"):
        for memory in await ctx.db.scalars(
            select(Memory).where(
                Memory.id.in_(ids["memory"]), Memory.user_id == uid, Memory.space_id == space_id
            )
        ):
            loaded[Ref("memory", memory.id)] = _memory_item(memory)
    if ids.get("journal"):
        for entry in await ctx.db.scalars(
            select(JournalEntry).where(
                JournalEntry.id.in_(ids["journal"]),
                JournalEntry.user_id == uid,
                JournalEntry.space_id == space_id,
            )
        ):
            if in_period(entry.entry_date):
                loaded[Ref("journal", entry.id)] = ContextItem(
                    kind="journal",
                    text=entry.content[:500],
                    origin="user_declared",
                    date=datetime.combine(entry.entry_date, time(12), tzinfo=zone),
                )
    return [loaded[ref] for ref in refs if ref in loaded]
