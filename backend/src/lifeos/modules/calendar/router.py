import uuid
from datetime import datetime

from fastapi import APIRouter

from lifeos.api.deps import CtxDep
from lifeos.modules.calendar import service
from lifeos.modules.calendar.schemas import EventsOut

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/events")
async def list_events(
    ctx: CtxDep, start: datetime, end: datetime, space_id: uuid.UUID | None = None
) -> EventsOut:
    """Synced occurrences overlapping [start, end) (62 days at most)."""
    items = await service.list_events(ctx, start=start, end=end, space_id=space_id)
    return EventsOut(items=items, timezone=str(service.owner_zone(ctx)))
