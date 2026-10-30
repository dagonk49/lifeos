import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Path, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from lifeos.api.deps import CtxDep
from lifeos.core.idempotency import idempotent
from lifeos.core.pagination import Page
from lifeos.modules.connectors.router import ConnectorsDep
from lifeos.modules.documents import service as documents
from lifeos.modules.documents.schemas import DocumentOut
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.guardian.schemas import ApprovalOut
from lifeos.modules.mail import drafts, send, service
from lifeos.modules.mail.schemas import (
    DraftIn,
    DraftOut,
    DraftPatch,
    MailOut,
    MailSummary,
    ResolveIn,
    SendAccountOut,
)
from lifeos.workers.queue import enqueue

router = APIRouter(prefix="/mail", tags=["mail"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
PartId = Annotated[str, Path(min_length=1, max_length=64)]


class _ImportFingerprint(BaseModel):
    message_id: uuid.UUID
    part_id: str


@router.get("/messages")
async def list_messages(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[MailSummary]:
    """Copied e-mails of the allowed spaces, newest first."""
    return await service.list_messages(
        ctx, space_id=space_id, source_id=source_id, limit=limit, cursor=cursor
    )


@router.get("/messages/{message_id}")
async def get_message(message_id: uuid.UUID, ctx: CtxDep) -> MailOut:
    return await service.detail(ctx, await service.get_message(ctx, message_id))


@router.post(
    "/messages/{message_id}/attachments/{part_id}/import",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentOut,
)
async def import_attachment(
    message_id: uuid.UUID,
    part_id: PartId,
    request: Request,
    ctx: CtxDep,
    connectors: ConnectorsDep,
    background: BackgroundTasks,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Makes one attachment a document of the e-mail's space (owner's explicit action)."""
    store = DocumentStore(ctx.settings)
    created: list[uuid.UUID] = []

    async def run() -> DocumentOut:
        document = await service.import_attachment(ctx, connectors, store, message_id, part_id)
        if document.status == "pending":
            created.append(document.id)
        return documents.detail(document)

    response = await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=_ImportFingerprint(message_id=message_id, part_id=part_id),
        run=run,
    )
    for document_id in created:
        # After the response, hence after the commit: the worker sees the document.
        background.add_task(enqueue, "process_document", str(document_id))
    return response


# --- Writing and sending (task 18, ADR 0018) ---------------------------------------------------


@router.get("/send-accounts")
async def send_accounts(ctx: CtxDep, space_id: uuid.UUID) -> list[SendAccountOut]:
    """Accounts allowed to send e-mails in this space (consent « Envoyer des e-mails »)."""
    return [
        SendAccountOut(id=a.id, email=a.email, provider=a.provider)
        for a in await drafts.send_accounts(ctx, space_id)
    ]


@router.get("/drafts")
async def list_drafts(ctx: CtxDep, space_id: uuid.UUID | None = None) -> list[DraftOut]:
    return await drafts.views(ctx, await drafts.list_drafts(ctx, space_id=space_id))


@router.post("/drafts", status_code=status.HTTP_201_CREATED, response_model=DraftOut)
async def create_draft(
    body: DraftIn, request: Request, ctx: CtxDep, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    """A new draft: nothing is sent (sending is a proposal to approve)."""

    async def run() -> DraftOut:
        return await drafts.view(ctx, await drafts.create_draft(ctx, body))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=body,
        run=run,
    )


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: uuid.UUID, ctx: CtxDep) -> DraftOut:
    return await drafts.view(ctx, await drafts.get_draft(ctx, draft_id))


@router.patch("/drafts/{draft_id}")
async def update_draft(
    draft_id: uuid.UUID,
    body: DraftPatch,
    ctx: CtxDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> DraftOut:
    """A new version; a pending send approval of the draft is invalidated."""
    draft = await drafts.update_draft(ctx, draft_id, body, if_match=if_match)
    return await drafts.view(ctx, draft)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(draft_id: uuid.UUID, ctx: CtxDep) -> None:
    await drafts.delete_draft(ctx, draft_id)


class _SendFingerprint(BaseModel):
    draft_id: uuid.UUID


@router.post(
    "/drafts/{draft_id}/send", status_code=status.HTTP_201_CREATED, response_model=ApprovalOut
)
async def request_send(
    draft_id: uuid.UUID, request: Request, ctx: CtxDep, idempotency_key: IdempotencyKey = None
) -> JSONResponse:
    """The draft as a proposal: the approval shows the exact message; nothing is sent
    before the owner approves it (POST /v1/approvals/{id}/approve)."""

    async def run() -> ApprovalOut:
        return ApprovalOut.model_validate(await send.request_send(ctx, draft_id))

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=_SendFingerprint(draft_id=draft_id),
        run=run,
    )


@router.get("/drafts/{draft_id}/approval")
async def draft_approval(draft_id: uuid.UUID, ctx: CtxDep) -> ApprovalOut:
    """The latest send approval of the draft."""
    return ApprovalOut.model_validate(await send.get_send_approval(ctx, draft_id))


@router.post("/drafts/{draft_id}/resolve")
async def resolve_draft(draft_id: uuid.UUID, body: ResolveIn, ctx: CtxDep) -> DraftOut:
    """An uncertain send settled by the owner after checking their sent messages."""
    return await drafts.view(ctx, await send.resolve(ctx, draft_id, sent=body.sent))
