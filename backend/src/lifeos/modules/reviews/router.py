import uuid
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from lifeos.api.deps import CtxDep
from lifeos.modules.reviews import service
from lifeos.modules.reviews.schemas import Period, ReviewOut, ReviewRequest

router = APIRouter(prefix="/reviews", tags=["reviews"])


class AutomationUpdate(BaseModel):
    space_id: uuid.UUID
    enabled: bool


@router.get("")
async def list_reviews(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    period: Period | None = None,
    limit: int = Query(default=30, ge=1, le=200),
) -> list[ReviewOut]:
    rows = await service.list_reviews(ctx, space_id=space_id, period=period, limit=limit)
    return [ReviewOut.model_validate(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def generate_review(body: ReviewRequest, ctx: CtxDep) -> ReviewOut:
    """Generates (or regenerates) the review of the period containing `day`."""
    return ReviewOut.model_validate(await service.generate(ctx, body, generated_by="user"))


@router.get("/automation")
async def automation(space_id: uuid.UUID, ctx: CtxDep) -> dict[str, Any]:
    return await service.automation_status(ctx, space_id)


@router.put("/automation")
async def set_automation(body: AutomationUpdate, ctx: CtxDep) -> dict[str, Any]:
    return await service.set_automation(ctx, body.space_id, body.enabled)


@router.get("/{review_id}")
async def get_review(review_id: uuid.UUID, ctx: CtxDep) -> ReviewOut:
    return ReviewOut.model_validate(await service.get_review(ctx, review_id))


@router.delete("/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(review_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_review(ctx, review_id)
