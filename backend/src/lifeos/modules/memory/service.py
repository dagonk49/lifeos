"""Mémoire Premium: facts with provenance and correction history, protected memories."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.pagination import Page, keyset, page
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.memory.models import Fact, Memory, MemoryRevision
from lifeos.modules.memory.schemas import (
    FactCorrect,
    FactCreate,
    FactOut,
    MemoryCreate,
    MemoryOut,
    MemoryUpdate,
)
from lifeos.modules.privacy import erasure


async def _audit(
    ctx: Ctx,
    action: str,
    space_id: uuid.UUID,
    target: str,
    target_id: uuid.UUID,
    **details: str | int | bool | None,
) -> None:
    await audit.record(
        ctx.db,
        action=action,
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=space_id,
        target_type=target,
        target_id=target_id,
        details=dict(details),
    )


# --- Facts ----------------------------------------------------------------------------------


async def list_facts(
    ctx: Ctx,
    *,
    space_id: uuid.UUID | None,
    kind: str | None = None,
    include_inactive: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[FactOut]:
    spaces = ctx.principal.spaces_or_all(space_id)
    stmt = select(Fact).where(Fact.user_id == ctx.principal.user_id, Fact.space_id.in_(spaces))
    if kind:
        stmt = stmt.where(Fact.kind == kind)
    if not include_inactive:
        stmt = stmt.where(Fact.status == "active")
    rows = list(await ctx.db.scalars(keyset(stmt, Fact, cursor, limit)))
    return page(rows, limit, FactOut.model_validate)


async def get_fact(ctx: Ctx, fact_id: uuid.UUID) -> Fact:
    fact = await ctx.db.scalar(
        select(Fact).where(
            Fact.id == fact_id,
            Fact.user_id == ctx.principal.user_id,
            Fact.space_id.in_(ctx.principal.space_ids),
        )
    )
    if fact is None:
        raise not_found("fact_not_found", "Fact not found")
    return fact


async def add_fact(
    ctx: Ctx,
    *,
    space_id: uuid.UUID,
    kind: str,
    statement: str,
    origin: str,
    confidence: Decimal,
    source_type: str,
    source_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    supersedes_id: uuid.UUID | None = None,
) -> Fact:
    ctx.principal.require_space(space_id)
    await authorize(ctx, "fact.write", space_id)
    fact = Fact(
        user_id=ctx.principal.user_id,
        space_id=space_id,
        kind=kind,
        statement=statement.strip(),
        origin=origin,
        confidence=confidence,
        occurred_at=occurred_at,
        source_type=source_type,
        source_id=source_id,
        supersedes_id=supersedes_id,
    )
    ctx.db.add(fact)
    await ctx.db.flush()
    await _audit(ctx, "fact.create", space_id, "fact", fact.id, origin=origin, source=source_type)
    return fact


async def create_fact(ctx: Ctx, data: FactCreate) -> Fact:
    return await add_fact(
        ctx,
        space_id=data.space_id,
        kind=data.kind,
        statement=data.statement,
        origin="user_declared",
        confidence=Decimal("1.00"),
        source_type="manual",
        occurred_at=data.occurred_at,
    )


async def correct_fact(ctx: Ctx, fact_id: uuid.UUID, data: FactCorrect) -> Fact:
    """User corrections take priority: the old version is kept, marked as superseded."""
    old = await get_fact(ctx, fact_id)
    if old.status != "active":
        raise ProblemError(409, "fact_not_active", "Only an active fact can be corrected")
    old.status = "superseded"
    new = await add_fact(
        ctx,
        space_id=old.space_id,
        kind=data.kind or old.kind,
        statement=data.statement,
        origin="user_declared",
        confidence=Decimal("1.00"),
        source_type="correction",
        source_id=old.id,
        occurred_at=data.occurred_at if "occurred_at" in data.model_fields_set else old.occurred_at,
        supersedes_id=old.id,
    )
    await _audit(ctx, "fact.correct", old.space_id, "fact", old.id, new_fact=str(new.id))
    return new


async def retract_fact(ctx: Ctx, fact_id: uuid.UUID) -> Fact:
    fact = await get_fact(ctx, fact_id)
    await authorize(ctx, "fact.write", fact.space_id)
    fact.status = "retracted"
    await _audit(ctx, "fact.retract", fact.space_id, "fact", fact.id)
    return fact


async def delete_fact(ctx: Ctx, fact_id: uuid.UUID) -> int:
    """Erasure of the fact and of its whole correction history (every version, earlier or
    later): no older statement survives it. The audit keeps only identifiers, one event
    per version (the replay after a restore erases each one). Returns the version count."""
    fact = await get_fact(ctx, fact_id)
    await authorize(ctx, "fact.write", fact.space_id)
    chain = await erasure.fact_chain(ctx.db, fact)
    for version in chain:
        details: dict[str, str | int | bool | None] = {"versions": len(chain)}
        details.update(await erasure.erase(ctx.db, version))
        await _audit(ctx, "fact.delete", version.space_id, "fact", version.id, **details)
    return len(chain)


async def fact_history(ctx: Ctx, fact_id: uuid.UUID) -> list[Fact]:
    """The fact and the versions it superseded, newest first."""
    chain = [await get_fact(ctx, fact_id)]
    while chain[-1].supersedes_id is not None and len(chain) < 50:
        previous = await ctx.db.get(Fact, chain[-1].supersedes_id)
        if previous is None or previous.user_id != ctx.principal.user_id:
            break
        chain.append(previous)
    return chain


# --- Memories -------------------------------------------------------------------------------


async def list_memories(
    ctx: Ctx,
    *,
    space_id: uuid.UUID | None,
    protected: bool | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[MemoryOut]:
    spaces = ctx.principal.spaces_or_all(space_id)
    stmt = select(Memory).where(
        Memory.user_id == ctx.principal.user_id, Memory.space_id.in_(spaces)
    )
    if protected is not None:
        stmt = stmt.where(Memory.protected.is_(protected))
    rows = list(await ctx.db.scalars(keyset(stmt, Memory, cursor, limit)))
    return page(rows, limit, MemoryOut.model_validate)


async def get_memory(ctx: Ctx, memory_id: uuid.UUID) -> Memory:
    memory = await ctx.db.scalar(
        select(Memory).where(
            Memory.id == memory_id,
            Memory.user_id == ctx.principal.user_id,
            Memory.space_id.in_(ctx.principal.space_ids),
        )
    )
    if memory is None:
        raise not_found("memory_not_found", "Memory not found")
    return memory


async def create_memory(
    ctx: Ctx,
    data: MemoryCreate,
    *,
    origin: str = "user_declared",
    source_type: str = "manual",
    source_id: uuid.UUID | None = None,
) -> Memory:
    ctx.principal.require_space(data.space_id)
    await authorize(ctx, "memory.write", data.space_id)
    memory = Memory(
        user_id=ctx.principal.user_id,
        space_id=data.space_id,
        title=data.title.strip(),
        content=data.content,
        importance=data.importance,
        protected=data.protected,
        origin=origin,
        source_type=source_type,
        source_id=source_id,
    )
    ctx.db.add(memory)
    await ctx.db.flush()
    await _audit(ctx, "memory.create", memory.space_id, "memory", memory.id, source=source_type)
    return memory


async def update_memory(ctx: Ctx, memory_id: uuid.UUID, data: MemoryUpdate) -> Memory:
    memory = await get_memory(ctx, memory_id)
    await authorize(ctx, "memory.write", memory.space_id)
    changes = data.model_dump(exclude_unset=True, exclude={"change_note"})
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes:
        return memory
    ctx.db.add(
        MemoryRevision(
            memory_id=memory.id,
            revision=memory.revision,
            title=memory.title,
            content=memory.content,
            importance=memory.importance,
            protected=memory.protected,
            change_note=data.change_note,
            changed_at=utcnow(),
        )
    )
    for key, value in changes.items():
        setattr(memory, key, value)
    memory.revision += 1
    await ctx.db.flush()
    await _audit(
        ctx,
        "memory.update",
        memory.space_id,
        "memory",
        memory.id,
        fields=",".join(sorted(changes)),
        revision=memory.revision,
    )
    return memory


async def list_revisions(ctx: Ctx, memory_id: uuid.UUID) -> list[MemoryRevision]:
    memory = await get_memory(ctx, memory_id)
    rows = await ctx.db.scalars(
        select(MemoryRevision)
        .where(MemoryRevision.memory_id == memory.id)
        .order_by(MemoryRevision.revision.desc())
    )
    return list(rows)


async def delete_memory(ctx: Ctx, memory_id: uuid.UUID, *, confirm: bool) -> None:
    memory = await get_memory(ctx, memory_id)
    await authorize(ctx, "memory.delete", memory.space_id)
    if memory.protected and not confirm:
        raise ProblemError(
            409,
            "memory_protected",
            "This memory is protected",
            "Confirm explicitly to delete a protected memory.",
        )
    erased = await erasure.erase(ctx.db, memory)
    await _audit(
        ctx,
        "memory.delete",
        memory.space_id,
        "memory",
        memory.id,
        protected=memory.protected,
        **erased,
    )
