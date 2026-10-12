"""Memory quality (F07): duplicates, possible contradictions and ageing preferences.

Everything here is local and deterministic: vector neighbours from the local embedding
model, normalised text, dates. No cloud call, so no quota is consumed. The owner decides:
keep both (dismiss), retract or correct one fact, or confirm an ageing preference.
"""

import re
import unicodedata
import uuid
from datetime import timedelta

from sqlalchemy import case, cast, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.types import Date

from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.memory.models import Fact, FactPairDismissal
from lifeos.modules.memory.schemas import FactOut, ReviewItem, ReviewQueue
from lifeos.modules.memory.service import _audit, get_fact
from lifeos.modules.retrieval.hybrid import tune_index_scan

# Only the most recently changed facts are compared with the whole space: an old pair was
# already compared when one of its facts was recent.
RECENT_FACTS = 200
NEIGHBOURS = 8
MAX_ITEMS = 50
_NEGATIONS = {"ne", "n", "pas", "plus", "jamais", "aucun", "aucune", "rien", "sans", "non"}
_NEGATIVE_VERBS = ("deteste", "evite", "refuse", "arrete")
_WORD = re.compile(r"[a-z]+")

_NEIGHBOURS_SQL = text(
    """
    WITH recent AS (
        SELECT id FROM facts
        WHERE user_id = :user_id AND space_id = :space_id AND status = 'active'
        ORDER BY updated_at DESC
        LIMIT :recent
    )
    SELECT e.object_id AS a, n.object_id AS b, n.distance
    FROM recent r
    JOIN embeddings e
      ON e.object_type = 'fact' AND e.object_id = r.id AND e.chunk = 0 AND e.model = :model
    CROSS JOIN LATERAL (
        SELECT e2.object_id, e2.embedding <=> e.embedding AS distance
        FROM embeddings e2
        WHERE e2.object_type = 'fact' AND e2.user_id = :user_id AND e2.space_id = :space_id
          AND e2.model = :model AND e2.chunk = 0 AND e2.object_id <> e.object_id
        ORDER BY e2.embedding <=> e.embedding
        LIMIT :neighbours
    ) n
    WHERE n.distance <= :max_distance
    """
)


def _negative(statement: str) -> bool:
    folded = unicodedata.normalize("NFKD", statement.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    words = set(_WORD.findall(folded))
    return bool(words & _NEGATIONS) or any(w.startswith(_NEGATIVE_VERBS) for w in words)


def _pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if a < b else (b, a)


def _same_day(a: Fact, b: Fact) -> bool:
    if a.occurred_at is None or b.occurred_at is None:
        return a.occurred_at is b.occurred_at
    return a.occurred_at.date() == b.occurred_at.date()


def _classify(a: Fact, b: Fact, distance: float, duplicate: float) -> tuple[str, str] | None:
    """(kind, reason) for a close pair of active facts, or None if it is normal."""
    if a.kind != b.kind:
        return None
    if a.kind == "event":
        # Similar events on different days are a routine, not a duplicate.
        if distance <= duplicate and _same_day(a, b):
            return "duplicate", "Même événement enregistré deux fois"
        return None
    if distance <= duplicate and _negative(a.statement) == _negative(b.statement):
        return "duplicate", "Ces deux éléments disent la même chose"
    if _negative(a.statement) != _negative(b.statement):
        return "conflict", "Formulations opposées : laquelle est vraie aujourd'hui ?"
    return "conflict", "Éléments proches : mise à jour ou contradiction ?"


async def _dismissed(ctx: Ctx, space_id: uuid.UUID) -> set[tuple[uuid.UUID, uuid.UUID]]:
    rows = await ctx.db.execute(
        select(FactPairDismissal.fact_a, FactPairDismissal.fact_b).where(
            FactPairDismissal.user_id == ctx.principal.user_id,
            FactPairDismissal.space_id == space_id,
        )
    )
    return {(row[0], row[1]) for row in rows}


async def _exact_duplicates(ctx: Ctx, space_id: uuid.UUID) -> list[list[uuid.UUID]]:
    normalized = func.trim(
        func.regexp_replace(func.lower(func.unaccent(Fact.statement)), "[^a-z0-9]+", " ", "g")
    )
    day = case((Fact.kind == "event", cast(Fact.occurred_at, Date)), else_=None)
    rows = await ctx.db.scalars(
        select(func.array_agg(Fact.id))
        .where(
            Fact.user_id == ctx.principal.user_id,
            Fact.space_id == space_id,
            Fact.status == "active",
        )
        .group_by(Fact.kind, normalized, day)
        .having(func.count() > 1)
    )
    return [list(ids) for ids in rows]


async def review_queue(ctx: Ctx, space_id: uuid.UUID) -> ReviewQueue:
    """The « À vérifier » list of one space: conflicts first, then duplicates, then ageing
    preferences."""
    ctx.principal.require_space(space_id)
    if not ctx.principal.can(space_id, "facts", "read"):
        raise ProblemError(403, "permission_denied", "No read access to facts in this space")
    settings = ctx.settings
    uid = ctx.principal.user_id
    dismissed = await _dismissed(ctx, space_id)
    pairs: dict[tuple[uuid.UUID, uuid.UUID], tuple[str, str, float | None]] = {}

    for group in await _exact_duplicates(ctx, space_id):
        first = group[0]
        for other in group[1:]:
            key = _pair(first, other)
            if key not in dismissed:
                pairs[key] = ("duplicate", "Même texte enregistré plusieurs fois", None)

    embedder = ctx.embedder
    if embedder is not None:
        await tune_index_scan(ctx.db, NEIGHBOURS)
        rows = (
            await ctx.db.execute(
                _NEIGHBOURS_SQL,
                {
                    "user_id": uid,
                    "space_id": space_id,
                    "model": embedder.model,
                    "recent": RECENT_FACTS,
                    "neighbours": NEIGHBOURS,
                    "max_distance": settings.memory_related_distance,
                },
            )
        ).all()
        close = {
            _pair(row.a, row.b): float(row.distance)
            for row in rows
            if _pair(row.a, row.b) not in dismissed and _pair(row.a, row.b) not in pairs
        }
        if close:
            ids = {i for key in close for i in key}
            facts = {
                f.id: f
                for f in await ctx.db.scalars(
                    select(Fact).where(Fact.id.in_(ids), Fact.status == "active")
                )
            }
            for (a, b), distance in close.items():
                if a not in facts or b not in facts:
                    continue
                verdict = _classify(
                    facts[a], facts[b], distance, settings.memory_duplicate_distance
                )
                if verdict:
                    pairs[(a, b)] = (*verdict, round(distance, 4))

    items: list[ReviewItem] = []
    if pairs:
        ids = {i for key in pairs for i in key}
        facts = {f.id: f for f in await ctx.db.scalars(select(Fact).where(Fact.id.in_(ids)))}
        for (a, b), (kind, reason, gap) in pairs.items():
            items.append(
                ReviewItem(
                    kind=kind,
                    reason=reason,
                    facts=[FactOut.model_validate(facts[a]), FactOut.model_validate(facts[b])],
                    distance=gap,
                )
            )
    order = {"conflict": 0, "duplicate": 1}
    items.sort(key=lambda item: (order[item.kind], item.distance or 0.0))

    cutoff = utcnow() - timedelta(days=settings.memory_preference_max_age_days)
    stale = await ctx.db.scalars(
        select(Fact)
        .where(
            Fact.user_id == uid,
            Fact.space_id == space_id,
            Fact.status == "active",
            Fact.kind == "preference",
            func.coalesce(Fact.last_confirmed_at, Fact.created_at) < cutoff,
        )
        .order_by(func.coalesce(Fact.last_confirmed_at, Fact.created_at))
        .limit(MAX_ITEMS)
    )
    for fact in stale:
        items.append(
            ReviewItem(
                kind="stale",
                reason="Préférence ancienne : est-elle toujours vraie ?",
                facts=[FactOut.model_validate(fact)],
            )
        )
    return ReviewQueue(items=items[:MAX_ITEMS], semantic=embedder is not None)


async def dismiss_pair(ctx: Ctx, fact_ids: list[uuid.UUID]) -> None:
    """« Keep both »: the pair is no longer flagged."""
    low, high = _pair(fact_ids[0], fact_ids[1])
    first, second = await get_fact(ctx, low), await get_fact(ctx, high)
    if first.id == second.id or first.space_id != second.space_id:
        raise ProblemError(422, "invalid_pair", "Two distinct facts of the same space expected")
    await authorize(ctx, "fact.write", first.space_id)
    await ctx.db.execute(
        insert(FactPairDismissal)
        .values(
            user_id=ctx.principal.user_id,
            space_id=first.space_id,
            fact_a=first.id,
            fact_b=second.id,
            created_at=utcnow(),
        )
        .on_conflict_do_nothing()
    )
    await _audit(ctx, "fact.review_dismiss", first.space_id, "fact", first.id, other=str(second.id))


async def confirm_fact(ctx: Ctx, fact_id: uuid.UUID) -> Fact:
    """The owner confirms a fact is still true: it stops ageing, inferences become
    confirmed."""
    fact = await get_fact(ctx, fact_id)
    await authorize(ctx, "fact.write", fact.space_id)
    if fact.status != "active":
        raise ProblemError(409, "fact_not_active", "Only an active fact can be confirmed")
    fact.last_confirmed_at = utcnow()
    if fact.origin in ("extracted", "inferred"):
        fact.origin = "user_confirmed"
    await _audit(ctx, "fact.confirm", fact.space_id, "fact", fact.id)
    return fact
