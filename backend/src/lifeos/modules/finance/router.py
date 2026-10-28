import hashlib
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse

from lifeos.api.deps import CtxDep
from lifeos.core.idempotency import idempotent
from lifeos.core.pagination import Page
from lifeos.core.uploads import read_upload
from lifeos.modules.finance import imports, service, tracking
from lifeos.modules.finance.schemas import (
    MONTH_PATTERN,
    BudgetSummary,
    CategoryCreate,
    CategoryDefaults,
    CategoryOut,
    CategoryUpdate,
    FinanceProjection,
    ImportCommit,
    ImportFingerprint,
    ImportMapping,
    ImportOut,
    ImportRowOut,
    ImportRowStatus,
    ImportUndo,
    ImportUploadFields,
    ReceiptProposalOut,
    RecurringCreate,
    RecurringOut,
    RecurringUpdate,
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


# --- Bank statement imports (task 25) --------------------------------------------------------

IMPORT_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file", "space_id"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "space_id": {"type": "string", "format": "uuid"},
                        "account_label": {"type": "string", "maxLength": 60},
                        "encoding": {
                            "type": "string",
                            "enum": ["auto", "utf-8", "cp1252"],
                            "default": "auto",
                        },
                    },
                }
            }
        },
    }
}


@router.get("/imports")
async def list_imports(ctx: CtxDep, space_id: uuid.UUID | None = None) -> list[ImportOut]:
    return await imports.list_imports(ctx, space_id=space_id)


@router.post(
    "/imports",
    status_code=status.HTTP_201_CREATED,
    response_model=ImportOut,
    openapi_extra=IMPORT_OPENAPI,
)
async def create_import(
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """A CSV statement, read into a preview: nothing reaches the budget before
    `POST /imports/{id}/commit`. The Idempotency-Key is bound to the file's SHA-256."""
    upload = await read_upload(request, ctx.settings.finance_import_max_bytes, ImportUploadFields)
    fingerprint = ImportFingerprint(
        **upload.fields.model_dump(), sha256=hashlib.sha256(upload.data).hexdigest()
    )

    async def run() -> ImportOut:
        created = await imports.create_import(
            ctx, upload.fields, data=upload.data, filename=upload.filename
        )
        return await imports.describe(ctx, created)

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=fingerprint,
        run=run,
    )


@router.get("/imports/{import_id}")
async def get_import(import_id: uuid.UUID, ctx: CtxDep) -> ImportOut:
    return await imports.describe(ctx, await imports.get_import(ctx, import_id))


@router.put("/imports/{import_id}/mapping")
async def set_import_mapping(import_id: uuid.UUID, body: ImportMapping, ctx: CtxDep) -> ImportOut:
    """Reads every row again with these columns and formats (still nothing written)."""
    return await imports.describe(ctx, await imports.set_mapping(ctx, import_id, body))


@router.get("/imports/{import_id}/rows")
async def list_import_rows(
    import_id: uuid.UUID,
    ctx: CtxDep,
    status_filter: Annotated[ImportRowStatus | None, Query(alias="status")] = None,
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = None,
) -> Page[ImportRowOut]:
    return await imports.list_rows(ctx, import_id, status=status_filter, cursor=cursor, limit=limit)


@router.post("/imports/{import_id}/commit", response_model=ImportOut)
async def commit_import(
    import_id: uuid.UUID,
    body: ImportCommit,
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """The owner's validation: the chosen rows become operations of the budget."""

    async def run() -> ImportOut:
        return await imports.describe(ctx, await imports.commit_import(ctx, import_id, body))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
        status_code=200,
    )


@router.post("/imports/{import_id}/undo")
async def undo_import(import_id: uuid.UUID, body: ImportUndo, ctx: CtxDep) -> ImportOut:
    """Deletes the operations this import added (named confirmation in the interface)."""
    del body  # `confirm: true` is required by the schema
    return await imports.describe(ctx, await imports.undo_import(ctx, import_id))


@router.delete("/imports/{import_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_import(import_id: uuid.UUID, ctx: CtxDep) -> None:
    """Cancels a preview (nothing was written)."""
    await imports.discard_import(ctx, import_id)


# --- Recurring items and projections (task 26) -----------------------------------------------


@router.get("/recurring")
async def list_recurring(
    ctx: CtxDep, space_id: uuid.UUID | None = None, include_inactive: bool = False
) -> list[RecurringOut]:
    rows = await tracking.list_recurring(ctx, space_id=space_id, include_inactive=include_inactive)
    return [tracking.recurring_out(ctx, r) for r in rows]


@router.post("/recurring", status_code=status.HTTP_201_CREATED, response_model=RecurringOut)
async def create_recurring(
    body: RecurringCreate,
    request: Request,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    async def run() -> RecurringOut:
        return tracking.recurring_out(ctx, await tracking.create_recurring(ctx, body))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.patch("/recurring/{recurring_id}")
async def update_recurring(
    recurring_id: uuid.UUID,
    body: RecurringUpdate,
    ctx: CtxDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> RecurringOut:
    item = await tracking.update_recurring(ctx, recurring_id, body, if_match=if_match)
    return tracking.recurring_out(ctx, item)


@router.delete("/recurring/{recurring_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recurring(recurring_id: uuid.UUID, ctx: CtxDep) -> None:
    await tracking.delete_recurring(ctx, recurring_id)


@router.get("/projection")
async def projection(
    ctx: CtxDep,
    space_id: uuid.UUID,
    months: int = Query(default=6, ge=1, le=24),
    baseline_months: int = Query(default=3, ge=1, le=12),
    currency: Annotated[str, Query(pattern=r"^[A-Z]{3}$")] = "EUR",
) -> FinanceProjection:
    """Known, scheduled and estimated amounts of the coming months, with the assumptions;
    computed locally on every read (never stored, no LLM)."""
    return await tracking.projection(
        ctx, space_id=space_id, months=months, baseline_months=baseline_months, currency=currency
    )
