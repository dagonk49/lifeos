import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse

from lifeos.api.deps import CtxDep
from lifeos.core.idempotency import idempotent
from lifeos.core.pagination import Page
from lifeos.modules.finance import service
from lifeos.modules.finance.schemas import (
    MONTH_PATTERN,
    BudgetSummary,
    CategoryCreate,
    CategoryDefaults,
    CategoryOut,
    CategoryUpdate,
    ReceiptProposalOut,
    TransactionCreate,
    TransactionOut,
    TransactionUpdate,
)

router = APIRouter(prefix="/finance", tags=["finance"])

Month = Annotated[str | None, Query(pattern=MONTH_PATTERN, description="YYYY-MM")]


# --- Categories ------------------------------------------------------------------------------


@router.get("/categories")
async def list_categories(
    ctx: CtxDep, space_id: uuid.UUID | None = None, include_archived: bool = False
) -> list[CategoryOut]:
    rows = await service.list_categories(ctx, space_id=space_id, include_archived=include_archived)
    return [CategoryOut.model_validate(c) for c in rows]


@router.post("/categories", status_code=status.HTTP_201_CREATED, response_model=CategoryOut)
async def create_category(
    body: CategoryCreate,
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    async def run() -> CategoryOut:
        return CategoryOut.model_validate(await service.create_category(ctx, body))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.post("/categories/defaults")
async def create_default_categories(body: CategoryDefaults, ctx: CtxDep) -> list[CategoryOut]:
    """Adds the default French categories missing from the space (idempotent)."""
    rows = await service.create_defaults(ctx, body.space_id)
    return [CategoryOut.model_validate(c) for c in rows]


@router.patch("/categories/{category_id}")
async def update_category(category_id: uuid.UUID, body: CategoryUpdate, ctx: CtxDep) -> CategoryOut:
    return CategoryOut.model_validate(await service.update_category(ctx, category_id, body))


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_category(ctx, category_id)


# --- Transactions ----------------------------------------------------------------------------


@router.get("/transactions")
async def list_transactions(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    month: Month = None,
    category_id: uuid.UUID | None = None,
    uncategorised: bool = False,
    direction: Literal["expense", "income"] | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[TransactionOut]:
    return await service.list_transactions(
        ctx,
        space_id=space_id,
        month=month,
        category_id=category_id,
        uncategorised=uncategorised,
        direction=direction,
        q=q,
        limit=limit,
        cursor=cursor,
    )


@router.post("/transactions", status_code=status.HTTP_201_CREATED, response_model=TransactionOut)
async def create_transaction(
    body: TransactionCreate,
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    async def run() -> TransactionOut:
        return TransactionOut.model_validate(await service.create_transaction(ctx, body))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: uuid.UUID, ctx: CtxDep) -> TransactionOut:
    return TransactionOut.model_validate(await service.get_transaction(ctx, transaction_id))


@router.patch("/transactions/{transaction_id}")
async def update_transaction(
    transaction_id: uuid.UUID,
    body: TransactionUpdate,
    ctx: CtxDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> TransactionOut:
    transaction = await service.update_transaction(ctx, transaction_id, body, if_match=if_match)
    return TransactionOut.model_validate(transaction)


@router.delete("/transactions/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(transaction_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_transaction(ctx, transaction_id)


# --- Receipts and budget ---------------------------------------------------------------------


@router.get("/receipts/{document_id}/proposal")
async def receipt_proposal(document_id: uuid.UUID, ctx: CtxDep) -> ReceiptProposalOut:
    """What was read from the receipt; the owner then posts the (corrected) transaction."""
    return await service.receipt_proposal(ctx, document_id)


@router.get("/summary")
async def summary(
    ctx: CtxDep,
    space_id: uuid.UUID,
    month: Month = None,
    currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")] = "EUR",
) -> BudgetSummary:
    """Month budget per category, computed locally (no cloud involved)."""
    return await service.summary(ctx, space_id=space_id, month=month, currency=currency)
