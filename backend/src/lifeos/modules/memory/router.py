import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse

from lifeos.api.deps import CtxDep
from lifeos.core.idempotency import idempotent
from lifeos.core.pagination import Page
from lifeos.modules.memory import quality, service
from lifeos.modules.memory.schemas import (
    FactCorrect,
    FactCreate,
    FactOut,
    MemoryCreate,
    MemoryOut,
    MemoryRevisionOut,
    MemoryUpdate,
    PairDismiss,
    ReviewQueue,
)

router = APIRouter(prefix="/memory", tags=["memory"])
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.get("/facts")
async def list_facts(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    kind: str | None = None,
    include_inactive: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[FactOut]:
    return await service.list_facts(
        ctx,
        space_id=space_id,
        kind=kind,
        include_inactive=include_inactive,
        limit=limit,
        cursor=cursor,
    )


@router.post("/facts", status_code=status.HTTP_201_CREATED, response_model=FactOut)
async def create_fact(
    body: FactCreate, request: Request, ctx: CtxDep, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    async def run() -> FactOut:
        return FactOut.model_validate(await service.create_fact(ctx, body))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.get("/facts/{fact_id}/history")
async def fact_history(fact_id: uuid.UUID, ctx: CtxDep) -> list[FactOut]:
    return [FactOut.model_validate(f) for f in await service.fact_history(ctx, fact_id)]


@router.post("/facts/{fact_id}/correct", status_code=status.HTTP_201_CREATED)
async def correct_fact(fact_id: uuid.UUID, body: FactCorrect, ctx: CtxDep) -> FactOut:
    return FactOut.model_validate(await service.correct_fact(ctx, fact_id, body))


@router.post("/facts/{fact_id}/confirm")
async def confirm_fact(fact_id: uuid.UUID, ctx: CtxDep) -> FactOut:
    return FactOut.model_validate(await quality.confirm_fact(ctx, fact_id))


@router.get("/review")
async def review_queue(space_id: uuid.UUID, ctx: CtxDep) -> ReviewQueue:
    """« À vérifier »: possible duplicates and contradictions, ageing preferences."""
    return await quality.review_queue(ctx, space_id)


@router.post("/review/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_pair(body: PairDismiss, ctx: CtxDep) -> None:
    await quality.dismiss_pair(ctx, body.fact_ids)


@router.post("/facts/{fact_id}/retract")
async def retract_fact(fact_id: uuid.UUID, ctx: CtxDep) -> FactOut:
    return FactOut.model_validate(await service.retract_fact(ctx, fact_id))


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fact(fact_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_fact(ctx, fact_id)


@router.get("/memories")
async def list_memories(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    protected: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[MemoryOut]:
    return await service.list_memories(
        ctx, space_id=space_id, protected=protected, limit=limit, cursor=cursor
    )


@router.post("/memories", status_code=status.HTTP_201_CREATED, response_model=MemoryOut)
async def create_memory(
    body: MemoryCreate, request: Request, ctx: CtxDep, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    async def run() -> MemoryOut:
        return MemoryOut.model_validate(await service.create_memory(ctx, body))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: uuid.UUID, ctx: CtxDep) -> MemoryOut:
    return MemoryOut.model_validate(await service.get_memory(ctx, memory_id))


@router.patch("/memories/{memory_id}")
async def update_memory(memory_id: uuid.UUID, body: MemoryUpdate, ctx: CtxDep) -> MemoryOut:
    return MemoryOut.model_validate(await service.update_memory(ctx, memory_id, body))


@router.get("/memories/{memory_id}/revisions")
async def memory_revisions(memory_id: uuid.UUID, ctx: CtxDep) -> list[MemoryRevisionOut]:
    return [
        MemoryRevisionOut.model_validate(r) for r in await service.list_revisions(ctx, memory_id)
    ]


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: uuid.UUID, ctx: CtxDep, confirm: bool = False) -> None:
    await service.delete_memory(ctx, memory_id, confirm=confirm)
