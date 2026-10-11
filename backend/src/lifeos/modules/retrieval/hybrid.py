"""Hybrid retrieval: full-text ranks and vector ranks fused by Reciprocal Rank Fusion.

Robust by construction: if the embedder is unavailable, the vector list is empty and
the result degrades to plain full-text search.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.logging import get_logger
from lifeos.modules.retrieval.embedders import Embedder, EmbeddingError
from lifeos.modules.retrieval.models import Embedding

RRF_K = 60
# Cosine distance above which a vector match is considered unrelated.
MAX_DISTANCE = 0.65

log = get_logger(__name__)


@dataclass(frozen=True)
class Ref:
    object_type: str
    object_id: uuid.UUID


async def vector_refs(
    session: AsyncSession,
    embedder: Embedder | None,
    query: str,
    *,
    user_id: uuid.UUID,
    space_ids: Iterable[uuid.UUID],
    object_types: Sequence[str],
    limit: int = 30,
) -> list[Ref]:
    """Nearest objects (best chunk) for the query, closest first."""
    if embedder is None or not query.strip() or not object_types:
        return []
    try:
        [vector] = await embedder.embed([query], "query")
    except EmbeddingError as exc:
        log.warning("vector_search_unavailable", error=str(exc)[:200])
        return []
    await _tune_index_scan(session, limit * 4)
    distance = Embedding.embedding.cosine_distance(vector)
    nearest = (
        select(Embedding.object_type, Embedding.object_id, distance.label("distance"))
        .where(
            Embedding.user_id == user_id,
            Embedding.space_id.in_(list(space_ids)),
            Embedding.object_type.in_(list(object_types)),
            Embedding.model == embedder.model,
        )
        .order_by(distance)
        .limit(limit * 4)  # several chunks may belong to the same object
        .subquery()
    )
    stmt = (
        select(nearest.c.object_type, nearest.c.object_id, func.min(nearest.c.distance).label("d"))
        .group_by(nearest.c.object_type, nearest.c.object_id)
        .having(func.min(nearest.c.distance) <= MAX_DISTANCE)
        .order_by(func.min(nearest.c.distance))
        .limit(limit)
    )
    rows = await session.execute(stmt)
    return [Ref(row[0], row[1]) for row in rows]


async def _tune_index_scan(session: AsyncSession, candidates: int) -> None:
    """HNSW returns at most `hnsw.ef_search` rows (default 40) BEFORE the owner, space and
    type filters: widen it, and let pgvector >= 0.8 keep scanning until enough rows pass.
    Transaction-local settings."""
    ef_search = str(min(1000, max(100, candidates)))
    await session.execute(select(func.set_config("hnsw.ef_search", ef_search, True)))
    if await session.scalar(select(func.current_setting("hnsw.iterative_scan", True))):
        await session.execute(select(func.set_config("hnsw.iterative_scan", "relaxed_order", True)))


def fuse(*rankings: Sequence[Ref]) -> list[Ref]:
    """Reciprocal Rank Fusion: an object ranked well by either method rises to the top."""
    scores: dict[Ref, float] = {}
    for ranking in rankings:
        for rank, ref in enumerate(ranking):
            scores[ref] = scores.get(ref, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=lambda ref: scores[ref], reverse=True)
