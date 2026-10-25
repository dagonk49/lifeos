import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Uuid, delete, exists, func, literal, select
from sqlalchemy.orm import aliased

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.pagination import Page, keyset, page
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.goals.service import check_task_goal
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.notifications.service import notify
from lifeos.modules.privacy import erasure
from lifeos.modules.tasks.models import Task, TaskDependency
from lifeos.modules.tasks.schemas import (
    MAX_DEPENDENCIES,
    TaskBlocker,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)

OPEN_STATUSES = ("todo", "in_progress")
# Moving a task to one of these needs every task it waits for to be finished.
PROGRESS_STATUSES = ("in_progress", "done")
# A dependency done or cancelled no longer blocks: its dependents are released.
RELEASING_STATUSES = ("done", "cancelled")
# Serialises the changes of a user's dependency graph (cycle check, blocked check).
TASK_DEPS_LOCK_CLASS = 0x5441534B  # "TASK"


def _deps_lock(user_id: uuid.UUID) -> Any:
    return func.pg_advisory_xact_lock(TASK_DEPS_LOCK_CLASS, func.hashtext(str(user_id)))


async def _lock_graph(ctx: Ctx) -> None:
    await ctx.db.execute(select(_deps_lock(ctx.principal.user_id)))


# --- Reading ------------------------------------------------------------------------------


@dataclass
class Links:
    depends_on: list[uuid.UUID] = field(default_factory=list)
    blocked_by: list[TaskBlocker] = field(default_factory=list)


async def links_for(ctx: Ctx, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, Links]:
    """The dependencies of these tasks, in one query (lists, dashboard, agent context)."""
    if not task_ids:
        return {}
    rows = await ctx.db.execute(
        select(TaskDependency.task_id, Task.id, Task.title, Task.status)
        .join(Task, Task.id == TaskDependency.depends_on_id)
        .where(
            TaskDependency.user_id == ctx.principal.user_id,
            TaskDependency.task_id.in_(task_ids),
        )
        .order_by(TaskDependency.created_at, Task.id)
    )
    links: dict[uuid.UUID, Links] = {}
    for task_id, blocker_id, title, status in rows:
        link = links.setdefault(task_id, Links())
        link.depends_on.append(blocker_id)
        if status in OPEN_STATUSES:
            link.blocked_by.append(TaskBlocker(id=blocker_id, title=title, status=status))
    return links


def to_out(task: Task, links: dict[uuid.UUID, Links]) -> TaskOut:
    out = TaskOut.model_validate(task)
    link = links.get(task.id)
    if link is not None:
        out.depends_on = list(link.depends_on)
        out.blocked_by = list(link.blocked_by)
        out.blocked = task.status in OPEN_STATUSES and bool(link.blocked_by)
    return out


async def task_outs(ctx: Ctx, tasks: list[Task]) -> list[TaskOut]:
    links = await links_for(ctx, [t.id for t in tasks])
    return [to_out(t, links) for t in tasks]


async def task_out(ctx: Ctx, task: Task) -> TaskOut:
    return (await task_outs(ctx, [task]))[0]


def _has_open_blocker(task: Any) -> ColumnElement[bool]:
    """Correlated: `task` waits for at least one open task."""
    blocker = aliased(Task)
    edge = aliased(TaskDependency)
    return exists(
        select(edge.task_id)
        .join(blocker, blocker.id == edge.depends_on_id)
        .where(edge.task_id == task.id, blocker.status.in_(OPEN_STATUSES))
    )


async def list_tasks(
    ctx: Ctx,
    *,
    space_id: uuid.UUID | None,
    statuses: list[str] | None,
    due_before: datetime | None = None,
    goal_id: uuid.UUID | None = None,
    blocked: bool | None = None,
    tag: str | None = None,
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
    if blocked is not None:
        is_blocked = Task.status.in_(OPEN_STATUSES) & _has_open_blocker(Task)
        stmt = stmt.where(is_blocked if blocked else ~is_blocked)
    if tag is not None:
        stmt = stmt.where(Task.tags.contains([tag]))
    rows = list(await ctx.db.scalars(keyset(stmt, Task, cursor, limit)))
    result = page(rows, limit, lambda t: t)
    links = await links_for(ctx, [t.id for t in result.items])
    return Page[TaskOut](
        items=[to_out(t, links) for t in result.items], next_cursor=result.next_cursor
    )


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


# --- Writing ------------------------------------------------------------------------------


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
    # Nothing depends on a new task yet: its links cannot close a cycle.
    blockers = [await get_task(ctx, i) for i in dict.fromkeys(data.depends_on)]
    for blocker in blockers:
        _check_same_space(blocker, data.space_id)
    task = Task(
        user_id=ctx.principal.user_id,
        space_id=data.space_id,
        title=data.title.strip(),
        notes=data.notes,
        priority=data.priority,
        due_at=data.due_at,
        remind_at=data.remind_at,
        goal_id=data.goal_id,
        tags=data.tags,
        source_type=source_type,
        source_id=source_id,
    )
    ctx.db.add(task)
    await ctx.db.flush()
    now = utcnow()
    for blocker in blockers:
        ctx.db.add(_edge(task, blocker, now))
    await ctx.db.flush()
    details: dict[str, Any] = {"source": source_type}
    if blockers:
        details["dependencies"] = len(blockers)
    await audit.record(
        ctx.db,
        action="task.create",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=task.space_id,
        target_type="task",
        target_id=task.id,
        details=details,
    )
    return task


def _blocked(blockers: list[TaskBlocker]) -> ProblemError:
    return ProblemError(
        409,
        "task_blocked",
        "The task waits for unfinished tasks",
        "Finish or cancel the tasks it depends on first.",
        extra={"blocked_by": [b.model_dump(mode="json") for b in blockers]},
    )


async def update_task(
    ctx: Ctx, task_id: uuid.UUID, data: TaskUpdate, *, if_match: int | None = None
) -> Task:
    changes = data.model_dump(exclude_unset=True)
    for key in ("title", "status", "priority", "tags"):
        if key in changes and changes[key] is None:
            changes.pop(key)
    if "status" in changes:
        # Same lock as the dependency changes: a link added meanwhile is seen.
        await _lock_graph(ctx)
    task = await get_task(ctx, task_id)
    await authorize(ctx, "task.update", task.space_id)
    if if_match is not None and if_match != task.version:
        raise ProblemError(
            409,
            "version_conflict",
            "The task was modified elsewhere",
            extra={"current_version": task.version},
        )
    if changes.get("goal_id") is not None:
        await check_task_goal(ctx, changes["goal_id"], task.space_id)
    previous = task.status
    status = changes.get("status", previous)
    if status != previous and status in PROGRESS_STATUSES:
        link = (await links_for(ctx, [task.id])).get(task.id)
        if link is not None and link.blocked_by:
            raise _blocked(link.blocked_by)
    if status != previous:
        task.completed_at = utcnow() if status == "done" else None
    if "remind_at" in changes and changes["remind_at"] != task.remind_at:
        task.reminded_at = None
    for key, value in changes.items():
        setattr(task, key, value.strip() if key == "title" else value)
    await ctx.db.flush()
    details: dict[str, Any] = {"fields": ",".join(sorted(changes))}
    if previous in OPEN_STATUSES and status in RELEASING_STATUSES:
        released = await _release_dependents(ctx, task)
        if released:
            details["released"] = released
    await audit.record(
        ctx.db,
        action="task.update",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=task.space_id,
        target_type="task",
        target_id=task.id,
        details=details,
    )
    return task


async def _release_dependents(ctx: Ctx, task: Task) -> int:
    """Open tasks that waited for `task` and for nothing else still open: each gets one
    notification « Débloquée : <titre> » (deduplicated: reopening and finishing `task`
    again does not repeat it). Returns how many were released."""
    dependents = list(
        await ctx.db.scalars(
            select(Task)
            .join(TaskDependency, TaskDependency.task_id == Task.id)
            .where(
                TaskDependency.depends_on_id == task.id,
                TaskDependency.user_id == ctx.principal.user_id,
                Task.status.in_(OPEN_STATUSES),
                ~_has_open_blocker(Task),
            )
            .order_by(Task.created_at, Task.id)
        )
    )
    for dependent in dependents:
        # The title names the released task only: it goes with that task when erased.
        await notify(
            ctx.db,
            ctx.user,
            category="important",
            title=f"Débloquée : {dependent.title}",
            body="Les tâches dont elle dépendait sont terminées.",
            dedup_key=f"task:{dependent.id}:unblocked:{task.id}",
            space_id=dependent.space_id,
            source_type="task",
            source_id=dependent.id,
        )
    return len(dependents)


async def delete_task(ctx: Ctx, task_id: uuid.UUID) -> None:
    task = await get_task(ctx, task_id)
    await authorize(ctx, "task.delete", task.space_id)
    # Its reminders (« Rappel : <titre> ») and its mentions in past reviews go with it; its
    # dependency links go by cascade (the tasks that waited for it are no longer blocked).
    erased = await erasure.erase(ctx.db, task)
    await audit.record(
        ctx.db,
        action="task.delete",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=task.space_id,
        target_type="task",
        target_id=task.id,
        details=dict(erased),
    )


# --- Dependencies -------------------------------------------------------------------------


def _check_same_space(blocker: Task, space_id: uuid.UUID) -> None:
    if blocker.space_id != space_id:
        raise ProblemError(
            422, "dependency_space_mismatch", "The dependency belongs to another space"
        )


def _edge(task: Task, blocker: Task, now: datetime) -> TaskDependency:
    return TaskDependency(
        user_id=task.user_id,
        space_id=task.space_id,
        task_id=task.id,
        depends_on_id=blocker.id,
        created_at=now,
    )


def _cycle(path: list[uuid.UUID]) -> ProblemError:
    return ProblemError(
        422,
        "dependency_cycle",
        "This dependency would create a cycle",
        extra={"path": [str(i) for i in path]},
    )


async def _path(ctx: Ctx, start: uuid.UUID, target: uuid.UUID) -> list[uuid.UUID] | None:
    """A chain of dependencies from `start` to `target`, if there is one."""
    uid = ctx.principal.user_id
    reach = select(literal(start, Uuid).label("id")).cte("reach", recursive=True)
    reach = reach.union(
        select(TaskDependency.depends_on_id)
        .join(reach, TaskDependency.task_id == reach.c.id)
        .where(TaskDependency.user_id == uid)
    )
    reachable = set(await ctx.db.scalars(select(reach.c.id)))
    if target not in reachable:
        return None
    # Rare (a refusal): rebuild one chain from the edges of the reached tasks.
    edges = await ctx.db.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_id).where(
            TaskDependency.user_id == uid, TaskDependency.task_id.in_(reachable)
        )
    )
    successors: dict[uuid.UUID, list[uuid.UUID]] = {}
    for source, dest in edges:
        successors.setdefault(source, []).append(dest)
    previous: dict[uuid.UUID, uuid.UUID] = {}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == target:
            break
        for nxt in sorted(successors.get(current, [])):
            if nxt not in previous and nxt != start:
                previous[nxt] = current
                queue.append(nxt)
    chain = [target]
    while chain[-1] != start:
        chain.append(previous[chain[-1]])
    return list(reversed(chain))


async def add_dependency(ctx: Ctx, task_id: uuid.UUID, depends_on_id: uuid.UUID) -> Task:
    """`task_id` waits for `depends_on_id`. Same space only; a cycle is never accepted."""
    await _lock_graph(ctx)
    task = await get_task(ctx, task_id)
    blocker = await get_task(ctx, depends_on_id)
    await authorize(ctx, "task.update", task.space_id)
    _check_same_space(blocker, task.space_id)
    if blocker.id == task.id:
        raise _cycle([task.id, task.id])
    known = await ctx.db.scalar(
        select(TaskDependency.task_id).where(
            TaskDependency.task_id == task.id, TaskDependency.depends_on_id == blocker.id
        )
    )
    if known is not None:
        return task
    count = await ctx.db.scalar(select(func.count()).where(TaskDependency.task_id == task.id))
    if (count or 0) >= MAX_DEPENDENCIES:
        raise ProblemError(
            422,
            "too_many_dependencies",
            "The task already waits for too many tasks",
            extra={"max": MAX_DEPENDENCIES},
        )
    # The new link closes a cycle if the blocker already (indirectly) waits for the task.
    chain = await _path(ctx, blocker.id, task.id)
    if chain is not None:
        raise _cycle([task.id, *chain])
    ctx.db.add(_edge(task, blocker, utcnow()))
    # The task's representation changed: a new version (If-Match).
    task.updated_at = utcnow()
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="task.dependency.add",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=task.space_id,
        target_type="task",
        target_id=task.id,
        details={"depends_on_id": str(blocker.id)},
    )
    return task


async def remove_dependency(ctx: Ctx, task_id: uuid.UUID, depends_on_id: uuid.UUID) -> None:
    await _lock_graph(ctx)
    task = await get_task(ctx, task_id)
    await authorize(ctx, "task.update", task.space_id)
    result = await ctx.db.execute(
        delete(TaskDependency).where(
            TaskDependency.user_id == ctx.principal.user_id,
            TaskDependency.task_id == task.id,
            TaskDependency.depends_on_id == depends_on_id,
        )
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        raise not_found("dependency_not_found", "Dependency not found")
    task.updated_at = utcnow()
    await ctx.db.flush()
    await audit.record(
        ctx.db,
        action="task.dependency.remove",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=task.space_id,
        target_type="task",
        target_id=task.id,
        details={"depends_on_id": str(depends_on_id)},
    )
