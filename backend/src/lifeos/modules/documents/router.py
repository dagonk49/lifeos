import hashlib
import unicodedata
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from lifeos.api.deps import CtxDep
from lifeos.core.errors import ProblemError
from lifeos.core.idempotency import idempotent
from lifeos.core.pagination import Page
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

# Room for the form fields and multipart boundaries around the file itself.
FORM_OVERHEAD = 64 * 1024
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


@dataclass(frozen=True)
class Upload:
    fields: DocumentUploadFields
    data: bytes
    filename: str | None


def _too_large(max_bytes: int) -> ProblemError:
    return ProblemError(
        413, "document_too_large", "The file is too large", extra={"max_bytes": max_bytes}
    )


def _invalid(loc: tuple[str, ...], msg: str, kind: str) -> RequestValidationError:
    return RequestValidationError([{"loc": ("body", *loc), "msg": msg, "type": kind}])


def _parse_fields(values: dict[str, str]) -> DocumentUploadFields:
    try:
        return DocumentUploadFields.model_validate(values)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        ) from exc


async def read_upload(request: Request, max_bytes: int) -> Upload:
    """Parses the multipart body with a hard size limit, counted while it streams in.

    The body is never read past the limit (nor at all if Content-Length announces more),
    and the file stays in memory: its plaintext never touches the disk.
    """
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ProblemError(415, "unsupported_media_type", "Expected multipart/form-data")
    limit = max_bytes + FORM_OVERHEAD
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(max_bytes)

    async def body() -> AsyncGenerator[bytes]:
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                raise _too_large(max_bytes)
            yield chunk

    parser = MultiPartParser(
        request.headers, body(), max_files=1, max_fields=10, max_part_size=8 * 1024
    )
    parser.spool_max_size = limit
    try:
        form = await parser.parse()
    except MultiPartException as exc:
        raise ProblemError(400, "invalid_form", "Malformed multipart form") from exc
    except ClientDisconnect as exc:
        raise ProblemError(400, "upload_interrupted", "The upload was interrupted") from exc
    try:
        values: dict[str, str] = {}
        upload: UploadFile | None = None
        for key, value in form.multi_items():
            if isinstance(value, UploadFile):
                if key != "file":
                    raise _invalid((key,), "Unexpected file", "unexpected_file")
                upload = value
            elif key in values:
                raise _invalid((key,), "Field given more than once", "duplicate")
            elif value != "":  # an empty optional field means "not given"
                values[key] = value
        fields = _parse_fields(values)
        if upload is None:
            raise _invalid(("file",), "Field required", "missing")
        data = await upload.read()
        filename = upload.filename
    finally:
        await form.close()
    if len(data) > max_bytes:
        raise _too_large(max_bytes)
    if not data:
        raise ProblemError(422, "document_empty", "The file is empty")
    return Upload(fields=fields, data=data, filename=filename)


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
    upload = await read_upload(request, ctx.settings.documents_max_bytes)
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
