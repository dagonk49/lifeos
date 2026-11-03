import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse

from lifeos.api.deps import CtxDep
from lifeos.core.idempotency import idempotent
from lifeos.core.pagination import Page
from lifeos.modules.guardian.schemas import ApprovalOut
from lifeos.modules.journal import service
from lifeos.modules.journal.schemas import JournalEntryCreate, JournalEntryOut, JournalEntryUpdate
from lifeos.modules.privacy.schemas import DerivedOut

router = APIRouter(prefix="/journal", tags=["journal"])


@router.get("")
async def list_entries(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[JournalEntryOut]:
    return await service.list_entries(
        ctx, space_id=space_id, date_from=date_from, date_to=date_to, limit=limit, cursor=cursor
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=JournalEntryOut)
async def create_entry(
    body: JournalEntryCreate,
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    async def run() -> JournalEntryOut:
        return JournalEntryOut.model_validate(await service.create_entry(ctx, body))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.get("/{entry_id}")
async def get_entry(entry_id: uuid.UUID, ctx: CtxDep) -> JournalEntryOut:
    return JournalEntryOut.model_validate(await service.get_entry(ctx, entry_id))


@router.patch("/{entry_id}")
async def update_entry(
    entry_id: uuid.UUID,
    body: JournalEntryUpdate,
    ctx: CtxDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> JournalEntryOut:
    return JournalEntryOut.model_validate(
        await service.update_entry(ctx, entry_id, body, if_match=if_match)
    )


@router.get("/{entry_id}/derived")
async def derived(entry_id: uuid.UUID, ctx: CtxDep) -> DerivedOut:
    """What was saved from this entry: shown before deleting it."""
    return DerivedOut.of(await service.derived(ctx, entry_id))


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(entry_id: uuid.UUID, ctx: CtxDep, with_derived: bool = False) -> None:
    """Erasure; `with_derived=true` also erases the facts and tasks saved from it."""
    await service.delete_entry(ctx, entry_id, with_derived=with_derived)


@router.post("/{entry_id}/analyze", status_code=status.HTTP_201_CREATED)
async def analyze_entry(entry_id: uuid.UUID, ctx: CtxDep) -> ApprovalOut:
    """Asks the assistant for proposals; returns the approval the owner must review."""
    return ApprovalOut.model_validate(await service.analyze(ctx, entry_id))
