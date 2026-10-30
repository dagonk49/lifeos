import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from lifeos.api.deps import CtxDep
from lifeos.core.idempotency import idempotent
from lifeos.modules.calendar import actions, service
from lifeos.modules.calendar.schemas import (
    CancelEventIn,
    CreateEventIn,
    EventsOut,
    MoveEventIn,
    WritableCalendarOut,
)
from lifeos.modules.guardian.models import Approval
from lifeos.modules.guardian.schemas import ApprovalOut

router = APIRouter(prefix="/calendar", tags=["calendar"])
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.get("/events")
async def list_events(
    ctx: CtxDep, start: datetime, end: datetime, space_id: uuid.UUID | None = None
) -> EventsOut:
    """Synced occurrences overlapping [start, end) (62 days at most)."""
    items = await service.list_events(ctx, start=start, end=end, space_id=space_id)
    return EventsOut(items=items, timezone=str(service.owner_zone(ctx)))


@router.get("/calendars")
async def writable_calendars(ctx: CtxDep, space_id: uuid.UUID) -> list[WritableCalendarOut]:
    """Followed calendars of the space that may be modified (calendar.write consent)."""
    return [
        WritableCalendarOut(source_id=s.id, name=s.name, account_id=s.account_id)
        for s in await actions.writable_sources(ctx, space_id)
    ]


async def _proposal(
    ctx: CtxDep,
    request: Request,
    key: str | None,
    body: BaseModel,
    run: Callable[[], Awaitable[Approval]],
) -> JSONResponse:
    async def create() -> ApprovalOut:
        approval = await run()
        return ApprovalOut.model_validate(approval)

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=key,
        method="POST",
        path=request.url.path,
        body=body,
        run=create,
    )


@router.post("/events", status_code=status.HTTP_201_CREATED, response_model=ApprovalOut)
async def propose_event(
    body: CreateEventIn, request: Request, ctx: CtxDep, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    """A new event, as a proposal to approve (nothing is written before)."""
    zone = service.owner_zone(ctx)

    async def run() -> Approval:
        return await actions.propose_create(
            ctx,
            space_id=body.space_id,
            source_id=body.source_id,
            summary=body.summary,
            description=body.description,
            location=body.location,
            when=actions.times_in(
                zone,
                starts_at=body.starts_at,
                ends_at=body.ends_at,
                start_date=body.start_date,
                end_date=body.end_date,
            ),
            guests=body.guests,
            notify=body.notify,
        )

    return await _proposal(ctx, request, idempotency_key, body, run)


@router.post(
    "/events/{event_id}/move", status_code=status.HTTP_201_CREATED, response_model=ApprovalOut
)
async def propose_move(
    event_id: uuid.UUID,
    body: MoveEventIn,
    request: Request,
    ctx: CtxDep,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Moving an occurrence, as a proposal (exact preview read again at the provider)."""
    zone = service.owner_zone(ctx)

    async def run() -> Approval:
        return await actions.propose_move(
            ctx,
            event_id,
            after=actions.times_in(
                zone,
                starts_at=body.starts_at,
                ends_at=body.ends_at,
                start_date=body.start_date,
                end_date=body.end_date,
            ),
            notify=body.notify,
        )

    return await _proposal(ctx, request, idempotency_key, body, run)


@router.post(
    "/events/{event_id}/cancel", status_code=status.HTTP_201_CREATED, response_model=ApprovalOut
)
async def propose_cancel(
    event_id: uuid.UUID,
    body: CancelEventIn,
    request: Request,
    ctx: CtxDep,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Cancelling an occurrence, as a proposal."""

    async def run() -> Approval:
        return await actions.propose_cancel(ctx, event_id, notify=body.notify)

    return await _proposal(ctx, request, idempotency_key, body, run)
