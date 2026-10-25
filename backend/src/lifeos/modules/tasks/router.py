import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import AfterValidator

from lifeos.api.deps import CtxDep
from lifeos.core.idempotency import idempotent
from lifeos.core.pagination import Page
from lifeos.modules.tasks import service
from lifeos.modules.tasks.schemas import (
    DependencyCreate,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    normalize_tag,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _tag(value: str | None) -> str | None:
    return None if value is None else normalize_tag(value)


@router.get("")
async def list_tasks(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    due_before: datetime | None = None,
    goal_id: uuid.UUID | None = None,
    blocked: bool | None = None,
    tag: Annotated[str | None, Query(max_length=100), AfterValidator(_tag)] = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[TaskOut]:
    return await service.list_tasks(
        ctx,
        space_id=space_id,
        statuses=status_filter,
        due_before=due_before,
        goal_id=goal_id,
        blocked=blocked,
        tag=tag,
        limit=limit,
        cursor=cursor,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TaskOut)
async def create_task(
    body: TaskCreate,
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    async def run() -> TaskOut:
        return await service.task_out(ctx, await service.create_task(ctx, body))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.get("/{task_id}")
async def get_task(task_id: uuid.UUID, ctx: CtxDep) -> TaskOut:
    return await service.task_out(ctx, await service.get_task(ctx, task_id))


@router.patch("/{task_id}")
async def update_task(
    task_id: uuid.UUID,
    body: TaskUpdate,
    ctx: CtxDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> TaskOut:
    task = await service.update_task(ctx, task_id, body, if_match=if_match)
    return await service.task_out(ctx, task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_task(ctx, task_id)


@router.post("/{task_id}/dependencies", status_code=status.HTTP_201_CREATED, response_model=TaskOut)
async def add_dependency(
    task_id: uuid.UUID,
    body: DependencyCreate,
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """`task_id` waits for `depends_on_id` (same space, never a cycle). Adding a link that
    exists already changes nothing."""

    async def run() -> TaskOut:
        task = await service.add_dependency(ctx, task_id, body.depends_on_id)
        return await service.task_out(ctx, task)

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.delete("/{task_id}/dependencies/{depends_on_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_dependency(task_id: uuid.UUID, depends_on_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.remove_dependency(ctx, task_id, depends_on_id)
