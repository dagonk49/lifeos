"""Home page, computed locally (no LLM): works when the cloud is unavailable (task 23)."""

import uuid
from datetime import date

from fastapi import APIRouter

from lifeos.api.deps import CtxDep
from lifeos.modules.dashboard import service
from lifeos.modules.dashboard.schemas import Dashboard, DashboardLayout, LayoutIn, WeekOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard(ctx: CtxDep, space_id: uuid.UUID | None = None) -> Dashboard:
    return await service.dashboard(ctx, space_id)


@router.get("/week")
async def week(
    ctx: CtxDep, start: date | None = None, space_id: uuid.UUID | None = None
) -> WeekOut:
    """Seven local days from `start` (default: this week, Monday or today per the layout)."""
    return await service.week(ctx, space_id, start)


@router.get("/layout")
async def get_layout(ctx: CtxDep) -> DashboardLayout:
    return await service.get_layout(ctx)


@router.put("/layout")
async def put_layout(body: LayoutIn, ctx: CtxDep) -> DashboardLayout:
    """Cards shown and their order; unknown or repeated ids: 422 dashboard_layout_invalid.
    Cards left out are appended, visible, in the default order."""
    return await service.put_layout(ctx, body)
