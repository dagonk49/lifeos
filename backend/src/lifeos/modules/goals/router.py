import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse

from lifeos.api.deps import CtxDep
from lifeos.core.idempotency import idempotent
from lifeos.modules.goals import service
from lifeos.modules.goals.schemas import (
    CheckinCreate,
    CheckinOut,
    GoalCreate,
    GoalDetail,
    GoalOut,
    GoalSummary,
    GoalUpdate,
    MilestoneCreate,
    MilestoneOut,
    MilestoneUpdate,
)
from lifeos.modules.guardian.context import Ctx

router = APIRouter(prefix="/goals", tags=["goals"])


async def _detail(ctx: Ctx, goal_id: uuid.UUID) -> GoalDetail:
    goal = await service.get_goal(ctx, goal_id)
    progress = (await service.progress_for(ctx, [goal]))[goal.id]
    return GoalDetail(
        **GoalOut.model_validate(goal).model_dump(),
        progress=progress,
        milestones=[
            MilestoneOut.model_validate(m) for m in await service.list_milestones(ctx, goal)
        ],
        checkins=[CheckinOut.model_validate(c) for c in await service.list_checkins(ctx, goal)],
    )


@router.get("")
async def list_goals(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
) -> list[GoalSummary]:
    goals = await service.list_goals(ctx, space_id=space_id, statuses=status_filter)
    progress = await service.progress_for(ctx, goals)
    return [
        GoalSummary(**GoalOut.model_validate(g).model_dump(), progress=progress[g.id])
        for g in goals
    ]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=GoalDetail)
async def create_goal(
    body: GoalCreate,
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    async def run() -> GoalDetail:
        goal = await service.create_goal(ctx, body)
        return await _detail(ctx, goal.id)

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.get("/{goal_id}")
async def get_goal(goal_id: uuid.UUID, ctx: CtxDep) -> GoalDetail:
    return await _detail(ctx, goal_id)


@router.patch("/{goal_id}")
async def update_goal(
    goal_id: uuid.UUID,
    body: GoalUpdate,
    ctx: CtxDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> GoalDetail:
    await service.update_goal(ctx, goal_id, body, if_match=if_match)
    return await _detail(ctx, goal_id)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(goal_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_goal(ctx, goal_id)


@router.post("/{goal_id}/milestones", status_code=status.HTTP_201_CREATED)
async def add_milestone(goal_id: uuid.UUID, body: MilestoneCreate, ctx: CtxDep) -> MilestoneOut:
    return MilestoneOut.model_validate(await service.add_milestone(ctx, goal_id, body))


@router.patch("/{goal_id}/milestones/{milestone_id}")
async def update_milestone(
    goal_id: uuid.UUID, milestone_id: uuid.UUID, body: MilestoneUpdate, ctx: CtxDep
) -> MilestoneOut:
    return MilestoneOut.model_validate(
        await service.update_milestone(ctx, goal_id, milestone_id, body)
    )


@router.delete("/{goal_id}/milestones/{milestone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_milestone(goal_id: uuid.UUID, milestone_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_milestone(ctx, goal_id, milestone_id)


@router.post("/{goal_id}/checkins", status_code=status.HTTP_201_CREATED)
async def add_checkin(goal_id: uuid.UUID, body: CheckinCreate, ctx: CtxDep) -> CheckinOut:
    return CheckinOut.model_validate(await service.add_checkin(ctx, goal_id, body))
