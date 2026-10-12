"""Hybrid search: French full-text (accent-insensitive) + local vector similarity.

Both rankings are fused (Reciprocal Rank Fusion). The same filters (owner, allowed
spaces, period, active facts only) apply to both, through the same SQL branches. Without
an embedder, this is plain full-text search.
"""

import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, String, cast, func, literal, select, union_all
from sqlalchemy.orm import InstrumentedAttribute

from lifeos.api.deps import CtxDep
from lifeos.modules.chat.models import Message
from lifeos.modules.documents.models import Document
from lifeos.modules.goals.models import Goal
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.memory.models import Fact, Memory
from lifeos.modules.retrieval.hybrid import Ref, fuse, vector_refs
from lifeos.modules.retrieval.indexer import coverage
from lifeos.modules.tasks.models import Task

router = APIRouter(prefix="/search", tags=["search"])

SearchType = Literal["tasks", "journal", "facts", "memories", "messages", "goals", "documents"]
ALL_TYPES: tuple[SearchType, ...] = (
    "tasks",
    "journal",
    "facts",
    "memories",
    "messages",
    "goals",
    "documents",
)
OBJECT_TYPE = {
    "tasks": "task",
    "journal": "journal",
    "facts": "fact",
    "memories": "memory",
    "messages": "message",
    "goals": "goal",
    "documents": "document",
}
_Column = ColumnElement[Any] | InstrumentedAttribute[Any]
HEADLINE = "StartSel=«, StopSel=», MaxWords=30, MinWords=10, MaxFragments=2"
SNIPPET_CHARS = 240
DOCUMENT_SNIPPET_SOURCE_CHARS = 20_000


class SearchHit(BaseModel):
    type: str
    id: uuid.UUID
    space_id: uuid.UUID
    title: str
    snippet: str
    date: datetime
    rank: float
    parent_id: uuid.UUID | None = None
    # "text": full-text match, "semantic": similar meaning only, "both".
    match: str = "text"


class SearchResult(BaseModel):
    query: str
    hits: list[SearchHit]
    semantic: bool


class IndexStatus(BaseModel):
    """Progress of the local semantic index (owner's information)."""

    semantic: bool
    model: str | None
    indexed: int
    total: int


def _branch(
    kind: str,
    model: Any,
    title: _Column,
    body: _Column,
    when: _Column,
    tsquery: ColumnElement[Any] | None,
    ids: list[uuid.UUID] | None,
    parent: _Column | None = None,
) -> Select[Any]:
    if tsquery is not None:
        snippet: Any = func.ts_headline("lifeos_fr", body, tsquery, HEADLINE)
        rank: Any = func.ts_rank(model.search_vector, tsquery)
        condition = model.search_vector.op("@@")(tsquery)
    else:
        snippet = func.left(body, SNIPPET_CHARS)
        rank = literal(0.0)
        condition = model.id.in_(ids or [])
    return select(
        literal(kind).label("type"),
        model.id.label("id"),
        model.space_id.label("space_id"),
        cast(title, String).label("title"),
        snippet.label("snippet"),
        when.label("date"),
        rank.label("rank"),
        (parent if parent is not None else literal(None, type_=model.id.type)).label("parent_id"),
    ).where(condition)


def _branches(
    ctx: Ctx,
    wanted: set[str],
    spaces: frozenset[uuid.UUID],
    tsquery: ColumnElement[Any] | None,
    ids: dict[str, list[uuid.UUID]] | None,
) -> list[Select[Any]]:
    uid = ctx.principal.user_id
    branches: list[Select[Any]] = []

    def want(search_type: str) -> bool:
        if search_type not in wanted:
            return False
        return ids is None or bool(ids.get(OBJECT_TYPE[search_type]))

    def object_ids(search_type: str) -> list[uuid.UUID] | None:
        return None if ids is None else ids.get(OBJECT_TYPE[search_type])

    if want("tasks"):
        branches.append(
            _branch(
                "task",
                Task,
                Task.title,
                Task.title + " " + func.coalesce(Task.notes, ""),
                func.coalesce(Task.due_at, Task.created_at),
                tsquery,
                object_ids("tasks"),
            ).where(Task.user_id == uid, Task.space_id.in_(spaces))
        )
    if want("journal"):
        branches.append(
            _branch(
                "journal",
                JournalEntry,
                cast(JournalEntry.entry_date, String),
                JournalEntry.content,
                JournalEntry.created_at,
                tsquery,
                object_ids("journal"),
            ).where(JournalEntry.user_id == uid, JournalEntry.space_id.in_(spaces))
        )
    if want("facts"):
        branches.append(
            _branch(
                "fact",
                Fact,
                Fact.kind,
                Fact.statement,
                func.coalesce(Fact.occurred_at, Fact.created_at),
                tsquery,
                object_ids("facts"),
            ).where(Fact.user_id == uid, Fact.space_id.in_(spaces), Fact.status == "active")
        )
    if want("memories"):
        branches.append(
            _branch(
                "memory",
                Memory,
                Memory.title,
                Memory.content,
                Memory.updated_at,
                tsquery,
                object_ids("memories"),
            ).where(Memory.user_id == uid, Memory.space_id.in_(spaces))
        )
    if want("messages"):
        branches.append(
            _branch(
                "message",
                Message,
                Message.role,
                Message.content,
                Message.created_at,
                tsquery,
                object_ids("messages"),
                parent=Message.conversation_id,
            ).where(Message.user_id == uid, Message.space_id.in_(spaces))
        )
    if want("goals"):
        branches.append(
            _branch(
                "goal",
                Goal,
                Goal.title,
                Goal.title
                + " "
                + func.coalesce(Goal.description, "")
                + " "
                + func.coalesce(Goal.motivation, ""),
                Goal.created_at,
                tsquery,
                object_ids("goals"),
            ).where(Goal.user_id == uid, Goal.space_id.in_(spaces))
        )
    if want("documents"):
        # Documents need the "documents" read right (agents); the owner has it everywhere.
        readable = [s for s in spaces if ctx.principal.can(s, "documents", "read")]
        branches.append(
            _branch(
                "document",
                Document,
                Document.title,
                # Bounded: highlighting runs on every matching row, documents can be long.
                func.left(
                    Document.title + " " + func.coalesce(Document.extracted_text, ""),
                    DOCUMENT_SNIPPET_SOURCE_CHARS,
                ),
                Document.created_at,
                tsquery,
                object_ids("documents"),
            ).where(Document.user_id == uid, Document.space_id.in_(readable))
        )
    return branches


async def _run(
    ctx: Ctx,
    branches: list[Select[Any]],
    date_from: date | None,
    date_to: date | None,
    limit: int,
) -> list[SearchHit]:
    if not branches:
        return []
    combined = union_all(*branches).subquery()
    stmt = select(combined)
    tz = ZoneInfo(ctx.user.timezone)
    if date_from:
        stmt = stmt.where(combined.c.date >= datetime.combine(date_from, time.min, tzinfo=tz))
    if date_to:
        end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
        stmt = stmt.where(combined.c.date < end)
    rows = await ctx.db.execute(
        stmt.order_by(combined.c.rank.desc(), combined.c.date.desc()).limit(limit)
    )
    return [SearchHit.model_validate(dict(row._mapping)) for row in rows]


@router.get("/status")
async def index_status(ctx: CtxDep) -> IndexStatus:
    if ctx.embedder is None:
        return IndexStatus(semantic=False, model=None, indexed=0, total=0)
    progress = await coverage(ctx.db, ctx.principal.user_id, ctx.embedder.model)
    return IndexStatus(semantic=True, **progress)


@router.get("")
async def search(
    ctx: CtxDep,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    space_id: uuid.UUID | None = None,
    types: Annotated[list[SearchType] | None, Query()] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: int = Query(default=30, ge=1, le=100),
) -> SearchResult:
    spaces = ctx.principal.spaces_or_all(space_id)
    wanted: set[str] = set(types or ALL_TYPES)
    tsquery = func.websearch_to_tsquery("lifeos_fr", q)
    text_hits = await _run(
        ctx, _branches(ctx, wanted, spaces, tsquery, None), date_from, date_to, limit * 2
    )
    by_ref = {Ref(h.type, h.id): h for h in text_hits}

    vector_hits = await vector_refs(
        ctx.db,
        ctx.embedder,
        q,
        user_id=ctx.principal.user_id,
        space_ids=spaces,
        object_types=[OBJECT_TYPE[t] for t in sorted(wanted)],
        limit=limit * 2,
    )
    semantic = vector_hits or []
    semantic_set = set(semantic)
    missing: dict[str, list[uuid.UUID]] = {}
    for ref in semantic:
        if ref not in by_ref:
            missing.setdefault(ref.object_type, []).append(ref.object_id)
    if missing:
        loaded = await _run(
            ctx, _branches(ctx, wanted, spaces, None, missing), date_from, date_to, len(semantic)
        )
        for hit in loaded:
            by_ref[Ref(hit.type, hit.id)] = hit.model_copy(update={"match": "semantic"})

    ordered = fuse([Ref(h.type, h.id) for h in text_hits], semantic)
    hits: list[SearchHit] = []
    for position, ref in enumerate(ordered):
        found = by_ref.get(ref)
        if found is None:  # filtered out (period, status, space)
            continue
        match = found.match
        if match == "text" and ref in semantic_set:
            match = "both"
        hits.append(found.model_copy(update={"match": match, "rank": 1.0 / (position + 1)}))
        if len(hits) == limit:
            break
    # True only if the meaning-based part actually ran for this query.
    return SearchResult(query=q, hits=hits, semantic=vector_hits is not None)
