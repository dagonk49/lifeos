"""Full-text search (French, accent-insensitive) across the allowed spaces."""

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
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.memory.models import Fact, Memory
from lifeos.modules.tasks.models import Task

router = APIRouter(prefix="/search", tags=["search"])

SearchType = Literal["tasks", "journal", "facts", "memories", "messages"]
ALL_TYPES: tuple[SearchType, ...] = ("tasks", "journal", "facts", "memories", "messages")
_Column = ColumnElement[Any] | InstrumentedAttribute[Any]
HEADLINE = "StartSel=«, StopSel=», MaxWords=30, MinWords=10, MaxFragments=2"


class SearchHit(BaseModel):
    type: str
    id: uuid.UUID
    space_id: uuid.UUID
    title: str
    snippet: str
    date: datetime
    rank: float
    parent_id: uuid.UUID | None = None


class SearchResult(BaseModel):
    query: str
    hits: list[SearchHit]


def _branch(
    kind: str,
    model: Any,
    title: _Column,
    body: _Column,
    when: _Column,
    query: ColumnElement[Any],
    parent: _Column | None = None,
) -> Select[Any]:
    return select(
        literal(kind).label("type"),
        model.id.label("id"),
        model.space_id.label("space_id"),
        cast(title, String).label("title"),
        func.ts_headline("lifeos_fr", body, query, HEADLINE).label("snippet"),
        when.label("date"),
        func.ts_rank(model.search_vector, query).label("rank"),
        (parent if parent is not None else literal(None, type_=model.id.type)).label("parent_id"),
    ).where(model.search_vector.op("@@")(query))


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
    wanted = set(types or ALL_TYPES)
    query = func.websearch_to_tsquery("lifeos_fr", q)
    branches: list[Select[Any]] = []
    if "tasks" in wanted:
        branches.append(
            _branch(
                "task",
                Task,
                Task.title,
                Task.title + " " + func.coalesce(Task.notes, ""),
                func.coalesce(Task.due_at, Task.created_at),
                query,
            ).where(Task.user_id == ctx.principal.user_id, Task.space_id.in_(spaces))
        )
    if "journal" in wanted:
        branches.append(
            _branch(
                "journal",
                JournalEntry,
                cast(JournalEntry.entry_date, String),
                JournalEntry.content,
                JournalEntry.created_at,
                query,
            ).where(
                JournalEntry.user_id == ctx.principal.user_id, JournalEntry.space_id.in_(spaces)
            )
        )
    if "facts" in wanted:
        branches.append(
            _branch(
                "fact",
                Fact,
                Fact.kind,
                Fact.statement,
                func.coalesce(Fact.occurred_at, Fact.created_at),
                query,
            ).where(
                Fact.user_id == ctx.principal.user_id,
                Fact.space_id.in_(spaces),
                Fact.status == "active",
            )
        )
    if "memories" in wanted:
        branches.append(
            _branch("memory", Memory, Memory.title, Memory.content, Memory.updated_at, query).where(
                Memory.user_id == ctx.principal.user_id, Memory.space_id.in_(spaces)
            )
        )
    if "messages" in wanted:
        branches.append(
            _branch(
                "message",
                Message,
                Message.role,
                Message.content,
                Message.created_at,
                query,
                parent=Message.conversation_id,
            ).where(Message.user_id == ctx.principal.user_id, Message.space_id.in_(spaces))
        )
    if not branches:
        return SearchResult(query=q, hits=[])

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
    return SearchResult(
        query=q, hits=[SearchHit.model_validate(dict(row._mapping)) for row in rows]
    )
