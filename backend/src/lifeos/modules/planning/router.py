import uuid
from datetime import datetime

from fastapi import APIRouter

from lifeos.api.deps import CtxDep
from lifeos.modules.calendar.service import owner_zone
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.planning import service, slots
from lifeos.modules.planning.schemas import (
    BusyOut,
    ConflictOut,
    ConflictsOut,
    SlotOut,
    SlotsIn,
    SlotsOut,
)

router = APIRouter(prefix="/planning", tags=["planning"])


def _busy(ctx: Ctx, event: slots.BusyEvent) -> BusyOut:
    zone = owner_zone(ctx)
    return BusyOut(
        event_id=uuid.UUID(event.ref) if event.title is not None else None,
        label=event.label,
        start=event.start.astimezone(zone),
        end=event.end.astimezone(zone),
        tentative=event.tentative,
    )


@router.get("/conflicts")
async def conflicts(
    ctx: CtxDep,
    space_id: uuid.UUID,
    start: datetime | None = None,
    end: datetime | None = None,
) -> ConflictsOut:
    """Overlapping occurrences (14 days by default, 31 at most), computed locally."""
    found, snap = await service.find_conflicts(ctx, space_id, start, end)
    zone = owner_zone(ctx)
    return ConflictsOut(
        items=[
            ConflictOut(
                first=_busy(ctx, c.first),
                second=_busy(ctx, c.second),
                start=c.start.astimezone(zone),
                end=c.end.astimezone(zone),
            )
            for c in found
        ],
        synced_at=snap.synced_at,
        calendars=snap.calendars,
    )


@router.post("/slots")
async def propose_slots(body: SlotsIn, ctx: CtxDep) -> SlotsOut:
    """Free slots (three at most), with their assumptions and the occurrences avoided.
    Nothing is written: blocking a slot is an approved calendar action."""
    proposals, snap, title = await service.propose(
        ctx,
        body.space_id,
        duration_minutes=body.duration_minutes,
        task_id=body.task_id,
        not_before=body.not_before,
        not_after=body.not_after,
    )
    zone = owner_zone(ctx)
    return SlotsOut(
        items=[
            SlotOut(
                start=p.start.astimezone(zone),
                end=p.end.astimezone(zone),
                assumptions=p.assumptions,
                events_used=[_busy(ctx, e) for e in p.events_used],
            )
            for p in proposals
        ],
        task_title=title,
        synced_at=snap.synced_at,
        calendars=snap.calendars,
    )
