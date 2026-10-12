import uuid
from datetime import datetime

from sqlalchemy import select

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.pagination import Page, keyset, page
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.goals.service import check_task_goal
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.tasks.models import Task
from lifeos.modules.tasks.schemas import TaskCreate, TaskOut, TaskUpdate

OPEN_STATUSES = ("todo", "in_progress")


async def list_tasks(
    ctx: Ctx,
    *,
    space_id: uuid.UUID | None,
    statuses: list[str] | None,
    due_before: datetime | None = None,
    goal_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[TaskOut]:
    spaces = ctx.principal.spaces_or_all(space_id)
    stmt = select(Task).where(Task.user_id == ctx.principal.user_id, Task.space_id.in_(spaces))
    if statuses:
        stmt = stmt.where(Task.status.in_(statuses))
    if due_before is not None:
        stmt = stmt.where(Task.due_at < due_before)
    if goal_id is not None:
        stmt = stmt.where(Task.goal_id == goal_id)
    rows = list(await ctx.db.scalars(keyset(stmt, Task, cursor, limit)))
    return page(rows, limit, TaskOut.model_validate)


async def get_task(ctx: Ctx, task_id: uuid.UUID) -> Task:
    task = await ctx.db.scalar(
        select(Task).where(
            Task.id == task_id,
            Task.user_id == ctx.principal.user_id,
            Task.space_id.in_(ctx.principal.space_ids),
        )
    )
    if task is None:
        raise not_found("task_not_found", "Task not found")
    return task


async def create_task(
    ctx: Ctx,
    data: TaskCreate,
    *,
    source_type: str = "manual",
    source_id: uuid.UUID | None = None,
) -> Task:
    ctx.principal.require_space(data.space_id)
    await authorize(ctx, "task.create", data.space_id)
    await check_task_goal(ctx, data.goal_id, data.space_id)
    task = Task(
        user_id=ctx.principal.user_id,
        space_id=data.space_id,
        title=data.title.strip(),
        notes=data.notes,
        priority=data.priority,
        due_at=data.due_at,
        remind_at=data.remind_at,
        goal_id=data.goal_id,
        source_type=source_type,
        source_id=source_id,
    )
    ctx.db.add(task)
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="task.create",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=task.space_id,
        target_type="task",
        target_id=task.id,
        details={"source": source_type},
    )
    return task


async def update_task(
    ctx: Ctx, task_id: uuid.UUID, data: TaskUpdate, *, if_match: int | None = None
) -> Task:
    task = await get_task(ctx, task_id)
    await authorize(ctx, "task.update", task.space_id)
    if if_match is not None and if_match != task.version:
        raise ProblemError(
            409,
            "version_conflict",
            "The task was modified elsewhere",
            extra={"current_version": task.version},
        )
    changes = data.model_dump(exclude_unset=True)
    if changes.get("goal_id") is not None:
        await check_task_goal(ctx, changes["goal_id"], task.space_id)
    for key in ("title", "status", "priority"):
        if key in changes and changes[key] is None:
            changes.pop(key)
    if "status" in changes and changes["status"] != task.status:
        task.completed_at = utcnow() if changes["status"] == "done" else None
    if "remind_at" in changes and changes["remind_at"] != task.remind_at:
        task.reminded_at = None
    for key, value in changes.items():
        setattr(task, key, value.strip() if key == "title" else value)
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="task.update",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=task.space_id,
        target_type="task",
        target_id=task.id,
        details={"fields": ",".join(sorted(changes))},
    )
    return task


async def delete_task(ctx: Ctx, task_id: uuid.UUID) -> None:
    task = await get_task(ctx, task_id)
    await authorize(ctx, "task.delete", task.space_id)
    await ctx.db.delete(task)
    await audit.record(
        ctx.db,
        action="task.delete",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=task.space_id,
        target_type="task",
        target_id=task.id,
    )
