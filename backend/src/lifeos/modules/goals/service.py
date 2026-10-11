"""Goals and projects (F06): measurable or qualitative, milestones, indicator history."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.goals.models import Goal, GoalCheckin, GoalMilestone
from lifeos.modules.goals.schemas import (
    CheckinCreate,
    GoalCreate,
    GoalProgress,
    GoalUpdate,
    MilestoneCreate,
    MilestoneUpdate,
)
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.tasks.models import Task

_CENTS = Decimal("0.01")
_DECIMAL_FIELDS = ("start_value", "target_value", "current_value")


def _q(value: Decimal | None) -> Decimal | None:
    """Same representation before and after a round trip through numeric(14, 2)."""
    return value.quantize(_CENTS) if value is not None else None


def metric_ratio(
    start: Decimal | None, target: Decimal | None, current: Decimal | None
) -> float | None:
    """Share of the way from start to target, for increasing or decreasing targets."""
    if target is None or current is None:
        return None
    origin = start if start is not None else Decimal(0)
    span = target - origin
    if span == 0:
        return 1.0 if current == target else 0.0
    return max(0.0, min(1.0, float((current - origin) / span)))


async def _audit(ctx: Ctx, action: str, goal: Goal, **details: Any) -> None:
    await audit.record(
        ctx.db,
        action=action,
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=goal.space_id,
        target_type="goal",
        target_id=goal.id,
        details=details,
    )


async def progress_for(ctx: Ctx, goals: list[Goal]) -> dict[uuid.UUID, GoalProgress]:
    ids = [g.id for g in goals]
    if not ids:
        return {}
    milestone_rows = await ctx.db.execute(
        select(
            GoalMilestone.goal_id,
            func.count(),
            func.count().filter(GoalMilestone.done_at.is_not(None)),
        )
        .where(GoalMilestone.goal_id.in_(ids))
        .group_by(GoalMilestone.goal_id)
    )
    milestones = {row[0]: (int(row[2]), int(row[1])) for row in milestone_rows}
    task_rows = await ctx.db.execute(
        select(
            Task.goal_id,
            func.count().filter(Task.status.in_(("todo", "in_progress"))),
            func.count().filter(Task.status == "done"),
        )
        .where(Task.goal_id.in_(ids), Task.user_id == ctx.principal.user_id)
        .group_by(Task.goal_id)
    )
    tasks = {row[0]: (int(row[1]), int(row[2])) for row in task_rows}
    result: dict[uuid.UUID, GoalProgress] = {}
    for goal in goals:
        done, total = milestones.get(goal.id, (0, 0))
        open_tasks, done_tasks = tasks.get(goal.id, (0, 0))
        ratio = metric_ratio(goal.start_value, goal.target_value, goal.current_value)
        if goal.kind == "measurable" and ratio is not None:
            basis = "metric"
        elif total:
            ratio, basis = done / total, "milestones"
        else:
            ratio, basis = None, "none"
        if goal.status == "achieved":
            ratio = 1.0
        result[goal.id] = GoalProgress(
            ratio=ratio,
            basis=basis,
            milestones_done=done,
            milestones_total=total,
            tasks_open=open_tasks,
            tasks_done=done_tasks,
        )
    return result


async def list_goals(
    ctx: Ctx, *, space_id: uuid.UUID | None, statuses: list[str] | None
) -> list[Goal]:
    spaces = ctx.principal.spaces_or_all(space_id)
    stmt = select(Goal).where(Goal.user_id == ctx.principal.user_id, Goal.space_id.in_(spaces))
    if statuses:
        stmt = stmt.where(Goal.status.in_(statuses))
    rows = await ctx.db.scalars(
        stmt.order_by(Goal.due_date.asc().nulls_last(), Goal.created_at.desc()).limit(500)
    )
    return list(rows)


async def get_goal(ctx: Ctx, goal_id: uuid.UUID) -> Goal:
    goal = await ctx.db.scalar(
        select(Goal).where(
            Goal.id == goal_id,
            Goal.user_id == ctx.principal.user_id,
            Goal.space_id.in_(ctx.principal.space_ids),
        )
    )
    if goal is None:
        raise not_found("goal_not_found", "Goal not found")
    return goal


async def create_goal(ctx: Ctx, data: GoalCreate) -> Goal:
    ctx.principal.require_space(data.space_id)
    await authorize(ctx, "goal.write", data.space_id)
    values = data.model_dump()
    for key in ("start_value", "target_value"):
        values[key] = _q(values[key])
    goal = Goal(user_id=ctx.principal.user_id, **values, current_value=values["start_value"])
    ctx.db.add(goal)
    await ctx.db.flush()
    await _audit(ctx, "goal.create", goal, kind=goal.kind)
    return goal


async def update_goal(
    ctx: Ctx, goal_id: uuid.UUID, data: GoalUpdate, *, if_match: int | None = None
) -> Goal:
    goal = await get_goal(ctx, goal_id)
    await authorize(ctx, "goal.write", goal.space_id)
    if if_match is not None and if_match != goal.version:
        raise ProblemError(
            409,
            "version_conflict",
            "The goal was modified elsewhere",
            extra={"current_version": goal.version},
        )
    changes = data.model_dump(exclude_unset=True)
    for key in ("title", "status", "review_cadence"):
        if key in changes and changes[key] is None:
            changes.pop(key)
    if "status" in changes and changes["status"] != goal.status:
        goal.achieved_at = utcnow() if changes["status"] == "achieved" else None
    for key, value in changes.items():
        setattr(goal, key, _q(value) if key in _DECIMAL_FIELDS else value)
    if goal.kind == "measurable" and goal.target_value is None:
        raise ProblemError(422, "goal_target_required", "A measurable goal needs a target value")
    await ctx.db.flush()
    await _audit(ctx, "goal.update", goal, fields=",".join(sorted(changes)))
    return goal


async def delete_goal(ctx: Ctx, goal_id: uuid.UUID) -> None:
    goal = await get_goal(ctx, goal_id)
    await authorize(ctx, "goal.write", goal.space_id)
    await ctx.db.delete(goal)
    await _audit(ctx, "goal.delete", goal)


async def list_milestones(ctx: Ctx, goal: Goal) -> list[GoalMilestone]:
    rows = await ctx.db.scalars(
        select(GoalMilestone)
        .where(GoalMilestone.goal_id == goal.id)
        .order_by(GoalMilestone.position, GoalMilestone.created_at)
    )
    return list(rows)


async def list_checkins(ctx: Ctx, goal: Goal, limit: int = 50) -> list[GoalCheckin]:
    rows = await ctx.db.scalars(
        select(GoalCheckin)
        .where(GoalCheckin.goal_id == goal.id)
        .order_by(GoalCheckin.created_at.desc())
        .limit(limit)
    )
    return list(rows)


async def add_milestone(ctx: Ctx, goal_id: uuid.UUID, data: MilestoneCreate) -> GoalMilestone:
    goal = await get_goal(ctx, goal_id)
    await authorize(ctx, "goal.write", goal.space_id)
    position = await ctx.db.scalar(
        select(func.coalesce(func.max(GoalMilestone.position), -1) + 1).where(
            GoalMilestone.goal_id == goal.id
        )
    )
    milestone = GoalMilestone(
        goal_id=goal.id, title=data.title.strip(), due_date=data.due_date, position=position or 0
    )
    ctx.db.add(milestone)
    await ctx.db.flush()
    await _audit(ctx, "goal.milestone.create", goal)
    return milestone


async def _milestone(ctx: Ctx, goal: Goal, milestone_id: uuid.UUID) -> GoalMilestone:
    milestone = await ctx.db.scalar(
        select(GoalMilestone).where(
            GoalMilestone.id == milestone_id, GoalMilestone.goal_id == goal.id
        )
    )
    if milestone is None:
        raise not_found("milestone_not_found", "Milestone not found")
    return milestone


async def update_milestone(
    ctx: Ctx, goal_id: uuid.UUID, milestone_id: uuid.UUID, data: MilestoneUpdate
) -> GoalMilestone:
    goal = await get_goal(ctx, goal_id)
    await authorize(ctx, "goal.write", goal.space_id)
    milestone = await _milestone(ctx, goal, milestone_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("title"):
        milestone.title = changes["title"].strip()
    if "due_date" in changes:
        milestone.due_date = changes["due_date"]
    if changes.get("done") is not None:
        milestone.done_at = (milestone.done_at or utcnow()) if changes["done"] else None
    await ctx.db.flush()
    await _audit(ctx, "goal.milestone.update", goal, fields=",".join(sorted(changes)))
    return milestone


async def delete_milestone(ctx: Ctx, goal_id: uuid.UUID, milestone_id: uuid.UUID) -> None:
    goal = await get_goal(ctx, goal_id)
    await authorize(ctx, "goal.write", goal.space_id)
    await ctx.db.delete(await _milestone(ctx, goal, milestone_id))
    await _audit(ctx, "goal.milestone.delete", goal)


async def add_checkin(ctx: Ctx, goal_id: uuid.UUID, data: CheckinCreate) -> GoalCheckin:
    goal = await get_goal(ctx, goal_id)
    await authorize(ctx, "goal.write", goal.space_id)
    value = _q(data.value)
    checkin = GoalCheckin(goal_id=goal.id, value=value, note=data.note, created_at=utcnow())
    ctx.db.add(checkin)
    if value is not None:
        goal.current_value = value
    await ctx.db.flush()
    await _audit(ctx, "goal.checkin", goal, has_value=data.value is not None)
    return checkin


async def check_task_goal(ctx: Ctx, goal_id: uuid.UUID | None, space_id: uuid.UUID) -> None:
    """A task can only be linked to a goal of the same space."""
    if goal_id is None:
        return
    goal = await get_goal(ctx, goal_id)
    if goal.space_id != space_id:
        raise ProblemError(422, "goal_space_mismatch", "The goal belongs to another space")
