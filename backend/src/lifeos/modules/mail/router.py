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
from lifeos.modules.mail import service
from lifeos.modules.mail.schemas import MailOut, MailSummary
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
