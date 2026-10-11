"""Background indexing: embeds new or changed objects, idempotently (ADR 0004/0006)."""

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, func, literal, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.core.security import utcnow
from lifeos.modules.chat.models import Message
from lifeos.modules.goals.models import Goal
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.memory.models import Fact, Memory
from lifeos.modules.retrieval.embedders import Embedder
from lifeos.modules.retrieval.models import Embedding
from lifeos.modules.tasks.models import Task

CHUNK_CHARS = 1200
MAX_CHUNKS = 8
_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")


@dataclass(frozen=True)
class Source:
    object_type: str
    model: Any
    text: ColumnElement[str]
    condition: ColumnElement[bool] | None = None


def _join(*parts: Any) -> ColumnElement[str]:
    expression: Any = func.coalesce(parts[0], "")
    for part in parts[1:]:
        expression = expression + literal(". ") + func.coalesce(part, "")
    return expression  # type: ignore[no-any-return]


SOURCES: tuple[Source, ...] = (
    # Retracted or superseded facts never surface: no need to embed them.
    Source("fact", Fact, _join(Fact.statement), Fact.status == "active"),
    Source("memory", Memory, _join(Memory.title, Memory.content)),
    Source("journal", JournalEntry, _join(JournalEntry.content)),
    Source("task", Task, _join(Task.title, Task.notes)),
    Source("goal", Goal, _join(Goal.title, Goal.description, Goal.motivation)),
    # Very short messages ("ok", "merci") carry no retrievable meaning.
    Source("message", Message, _join(Message.content), func.length(Message.content) >= 15),
)
SOURCE_BY_TYPE = {s.object_type: s for s in SOURCES}


def chunk_text(text: str) -> list[str]:
    """Greedy packing of sentences into chunks of at most CHUNK_CHARS characters."""
    text = text.strip()
    if len(text) <= CHUNK_CHARS:
        return [text]
    chunks: list[str] = []
    current = ""
    for raw in _SENTENCE.split(text):
        sentence = raw.strip()
        while len(sentence) > CHUNK_CHARS:  # a single very long sentence
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:CHUNK_CHARS])
            sentence = sentence[CHUNK_CHARS:].strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > CHUNK_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks[:MAX_CHUNKS]


# Arbitrary constant: one indexing run at a time (transaction-scoped advisory lock).
INDEX_LOCK = 0x4C49_4645_4F53


async def index_pending(
    session: AsyncSession, embedder: Embedder, *, batch: int = 32
) -> dict[str, int]:
    """Embeds up to `batch` stale objects per type. Safe to run concurrently with writes;
    a second concurrent run returns immediately."""
    counts: dict[str, int] = {}
    if not await session.scalar(select(func.pg_try_advisory_xact_lock(INDEX_LOCK))):
        return counts
    pruned = await _prune(session)
    if pruned:
        counts["pruned"] = pruned
    for source in SOURCES:
        model = source.model
        current = func.md5(source.text)
        stmt = (
            select(model.id, model.user_id, model.space_id, source.text.label("text"), current)
            .outerjoin(
                Embedding,
                and_(
                    Embedding.object_type == source.object_type,
                    Embedding.object_id == model.id,
                    Embedding.chunk == 0,
                    Embedding.model == embedder.model,
                ),
            )
            .where(
                or_(
                    Embedding.id.is_(None),
                    Embedding.content_hash != current,
                    # Moved to another space: vectors are filtered by space.
                    Embedding.space_id != model.space_id,
                )
            )
            .limit(batch)
        )
        if source.condition is not None:
            stmt = stmt.where(source.condition)
        rows = (await session.execute(stmt)).all()
        if not rows:
            continue
        chunks = [(row, i, text) for row in rows for i, text in enumerate(chunk_text(row[3]))]
        vectors = await embedder.embed([c[2] for c in chunks], "passage")
        ids: list[uuid.UUID] = [row[0] for row in rows]
        await session.execute(
            delete(Embedding).where(
                Embedding.object_type == source.object_type,
                Embedding.object_id.in_(ids),
                Embedding.model == embedder.model,
            )
        )
        now = utcnow()
        session.add_all(
            Embedding(
                user_id=row[1],
                space_id=row[2],
                object_type=source.object_type,
                object_id=row[0],
                chunk=index,
                content_hash=row[4],
                model=embedder.model,
                embedding=vector,
                created_at=now,
            )
            for (row, index, _), vector in zip(chunks, vectors, strict=True)
        )
        await session.flush()
        counts[source.object_type] = len(rows)
    return counts


async def _prune(session: AsyncSession) -> int:
    """Vectors of objects that no longer qualify (e.g. a retracted fact)."""
    removed = 0
    for source in SOURCES:
        if source.condition is None:
            continue
        result = await session.execute(
            delete(Embedding).where(
                Embedding.object_type == source.object_type,
                Embedding.object_id.in_(select(source.model.id).where(not_(source.condition))),
            )
        )
        removed += int(result.rowcount or 0)  # type: ignore[attr-defined]
    return removed


async def drop_other_models(session: AsyncSession, model: str) -> int:
    """After a model change, old vectors are useless (and not comparable): remove them."""
    result = await session.execute(delete(Embedding).where(Embedding.model != model))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def coverage(session: AsyncSession, user_id: uuid.UUID, model: str) -> dict[str, Any]:
    """Indexing progress, for the owner's information."""
    done = await session.scalar(
        select(func.count(func.distinct(Embedding.object_id))).where(
            Embedding.user_id == user_id, Embedding.model == model
        )
    )
    total = 0
    for source in SOURCES:
        stmt = select(func.count()).select_from(source.model).where(source.model.user_id == user_id)
        if source.condition is not None:
            stmt = stmt.where(source.condition)
        total += int(await session.scalar(stmt) or 0)
    return {"model": model, "indexed": int(done or 0), "total": total}
