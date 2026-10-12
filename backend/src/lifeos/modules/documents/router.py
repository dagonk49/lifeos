import hashlib
import unicodedata
import uuid
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, status
from fastapi.responses import JSONResponse, Response

from lifeos.api.deps import CtxDep
from lifeos.core.errors import ProblemError
from lifeos.core.idempotency import idempotent
from lifeos.core.pagination import Page
from lifeos.core.uploads import read_upload
from lifeos.modules.documents import service
from lifeos.modules.documents.schemas import (
    DocumentOut,
    DocumentSummary,
    DocumentUpdate,
    DocumentUploadFields,
    SourceType,
    UploadFingerprint,
)
from lifeos.modules.documents.sniff import sniff
from lifeos.modules.documents.storage import DocumentStore
from lifeos.workers.queue import enqueue

router = APIRouter(prefix="/documents", tags=["documents"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
PurposeFilter = Literal["document", "receipt", "attachment", "dictation"]
StatusFilter = Literal["pending", "processing", "ready", "failed", "rejected"]

EXTENSIONS = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/heic": "heic",
    "text/plain": "txt",
    "audio/mp4": "m4a",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
}
UPLOAD_OPENAPI: dict[str, Any] = {
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
                        "purpose": {
                            "type": "string",
                            "enum": ["document", "receipt", "attachment"],
                            "default": "document",
                        },
                        "title": {"type": "string", "maxLength": 200},
                        "source_type": {
                            "type": "string",
                            "enum": [
                                "journal_entry",
                                "message",
                                "conversation",
                                "task",
                                "goal",
                                "transaction",
                            ],
                        },
                        "source_id": {"type": "string", "format": "uuid"},
                        "keep_original": {"type": "boolean", "default": False},
                    },
                }
            }
        },
    }
}


def content_disposition(title: str, mime: str) -> str:
    """RFC 6266 attachment with a safe ASCII fallback and the UTF-8 name (RFC 8187)."""
    base = "".join(c for c in title if c.isprintable() and c not in '"\\/:*?<>|;%')
    base = " ".join(base.split()).strip(" .")[:100] or "document"
    filename = f"{base}.{EXTENSIONS.get(mime, 'bin')}"
    folded = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    fallback = "".join(c if c.isalnum() or c in "._- " else "_" for c in folded).strip() or (
        f"document.{EXTENSIONS.get(mime, 'bin')}"
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _process_later(background: BackgroundTasks, document_id: uuid.UUID) -> None:
    # After the response, hence after the commit: the worker sees the document. A lost
    # message is harmless, the periodic job processes pending documents.
    background.add_task(enqueue, "process_document", str(document_id))


@router.get("")
async def list_documents(
    ctx: CtxDep,
    space_id: uuid.UUID | None = None,
    purpose: PurposeFilter | None = None,
    status_filter: Annotated[StatusFilter | None, Query(alias="status")] = None,
    source_type: SourceType | None = None,
    source_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[DocumentSummary]:
    return await service.list_documents(
        ctx,
        space_id=space_id,
        purpose=purpose,
        status=status_filter,
        source_type=source_type,
        source_id=source_id,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentOut,
    openapi_extra=UPLOAD_OPENAPI,
)
async def upload_document(
    request: Request,
    ctx: CtxDep,
    background: BackgroundTasks,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Multipart upload. The Idempotency-Key is bound to the form fields and to the
    file's SHA-256: replaying it with another file answers 409 idempotency_key_reused."""
    store = DocumentStore(ctx.settings)
    if not store.available:
        raise ProblemError(503, "documents_unavailable", "Document storage is unavailable")
    upload = await read_upload(request, ctx.settings.documents_max_bytes, DocumentUploadFields)
    detected = sniff(upload.data)
    # Audio is dictation, handled by the voice module.
    if detected is None or detected.kind == "audio":
        raise ProblemError(415, "unsupported_type", "This file type is not accepted")
    fingerprint = UploadFingerprint(
        **upload.fields.model_dump(), sha256=hashlib.sha256(upload.data).hexdigest()
    )
    created: list[uuid.UUID] = []

    async def run() -> DocumentOut:
        document = await service.create_document(
            ctx,
            store,
            upload.fields,
            data=upload.data,
            detected=detected,
            filename=upload.filename,
        )
        created.append(document.id)
        return service.detail(document)

    response = await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        body=fingerprint,
        run=run,
    )
    for document_id in created:
        _process_later(background, document_id)
    return response


@router.get("/{document_id}")
async def get_document(document_id: uuid.UUID, ctx: CtxDep) -> DocumentOut:
    return service.detail(await service.get_document(ctx, document_id))


@router.get(
    "/{document_id}/original",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_original(document_id: uuid.UUID, ctx: CtxDep) -> Response:
    document, data = await service.read_original(ctx, DocumentStore(ctx.settings), document_id)
    return Response(
        content=data,
        media_type=document.mime,
        headers={
            "Content-Disposition": content_disposition(document.title, document.mime),
            "X-Content-Type-Options": "nosniff",
            # Opened directly anyway (not through the web client): render nothing active.
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, no-store",
        },
    )


@router.patch("/{document_id}")
async def update_document(
    document_id: uuid.UUID,
    body: DocumentUpdate,
    ctx: CtxDep,
    if_match: Annotated[int | None, Header(alias="If-Match")] = None,
) -> DocumentOut:
    document = await service.update_document(ctx, document_id, body, if_match=if_match)
    return service.detail(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, ctx: CtxDep) -> None:
    await service.delete_document(ctx, DocumentStore(ctx.settings), document_id)


@router.post("/{document_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_document(
    document_id: uuid.UUID, ctx: CtxDep, background: BackgroundTasks
) -> DocumentOut:
    document = await service.reprocess_document(ctx, document_id)
    _process_later(background, document.id)
    return service.detail(document)
