"""Documents (F10): upload, encrypted originals with limited retention, extracted text.

Extraction and analysis run in the background (`processing`); this module serves the
owner's requests. Nothing here sends document content anywhere.
"""

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.pagination import Page, keyset, page
from lifeos.core.security import utcnow
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.chat.models import Conversation, Message
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.processing import analyse, clean_text, owner_today
from lifeos.modules.documents.schemas import (
    DocumentOut,
    DocumentSummary,
    DocumentUpdate,
    DocumentUploadFields,
)
from lifeos.modules.documents.sniff import Detected
from lifeos.modules.documents.storage import DocumentStore, OriginalMissingError, StorageError
from lifeos.modules.finance.models import Transaction
from lifeos.modules.goals.models import Goal
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.tasks.models import Task

log = get_logger(__name__)

SOURCE_MODELS: dict[str, Any] = {
    "journal_entry": JournalEntry,
    "message": Message,
    "conversation": Conversation,
    "task": Task,
    "goal": Goal,
    "transaction": Transaction,
}
DEFAULT_TITLES = {
    "document": "Document",
    "receipt": "Ticket de caisse",
    "attachment": "Pièce jointe",
}


def summary(document: Document) -> DocumentSummary:
    return DocumentSummary.model_validate(_fields(document))


def detail(document: Document) -> DocumentOut:
    return DocumentOut.model_validate(
        {**_fields(document), "extracted_text": document.extracted_text}
    )


def _fields(document: Document) -> dict[str, Any]:
    available = document.storage_key is not None
    return {
        "id": document.id,
        "space_id": document.space_id,
        "title": document.title,
        "kind": document.kind,
        "mime": document.mime,
        "size_bytes": document.size_bytes,
        "purpose": document.purpose,
        "status": document.status,
        "error": document.error,
        "sensitivity": list(document.sensitivity or []),
        "text_source": document.text_source,
        "text_confidence": (
            float(document.text_confidence) if document.text_confidence is not None else None
        ),
        "pages": document.pages,
        "processed_at": document.processed_at,
        "text_corrected_at": document.text_corrected_at,
        "original_available": available,
        "original_until": document.original_until,
        "keep_original": available and document.original_until is None,
        "source_type": document.source_type,
        "source_id": document.source_id,
        "analysis": document.analysis,
        "version": document.version,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


async def _audit(ctx: Ctx, action: str, document: Document, **details: Any) -> None:
    # Identifiers and counters only: never a title, a file name or any content.
    await audit.record(
        ctx.db,
        action=action,
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=document.space_id,
        target_type="document",
        target_id=document.id,
        details=details,
    )


def _readable_spaces(ctx: Ctx, space_id: uuid.UUID | None = None) -> frozenset[uuid.UUID]:
    """Allowed spaces where the principal may read documents (agents need a grant)."""
    spaces = ctx.principal.spaces_or_all(space_id)
    return frozenset(s for s in spaces if ctx.principal.can(s, "documents", "read"))


def default_title(filename: str | None, purpose: str) -> str:
    """A title from the uploaded file name (without path or extension), or a default."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name.strip(".") else name
    cleaned = " ".join("".join(c if c.isprintable() else " " for c in stem).split())
    return cleaned[:200] or DEFAULT_TITLES.get(purpose, "Document")


def retention_end(ctx: Ctx) -> datetime:
    return utcnow() + timedelta(days=ctx.settings.documents_original_retention_days)


async def _check_source(
    ctx: Ctx, source_type: str, source_id: uuid.UUID, space_id: uuid.UUID
) -> None:
    """The source must exist, belong to the owner, and be in the document's space."""
    model = SOURCE_MODELS[source_type]
    found = await ctx.db.scalar(
        select(model.space_id).where(
            model.id == source_id,
            model.user_id == ctx.principal.user_id,
            model.space_id.in_(ctx.principal.space_ids),
        )
    )
    if found is None:
        raise not_found("source_not_found", "Source not found")
    if found != space_id:
        raise ProblemError(422, "source_space_mismatch", "The source belongs to another space")


# --- Reading -------------------------------------------------------------------------------


async def list_documents(
    ctx: Ctx,
    *,
    space_id: uuid.UUID | None,
    purpose: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[DocumentSummary]:
    spaces = _readable_spaces(ctx, space_id)
    stmt = select(Document).where(
        Document.user_id == ctx.principal.user_id, Document.space_id.in_(spaces)
    )
    if purpose:
        stmt = stmt.where(Document.purpose == purpose)
    if status:
        stmt = stmt.where(Document.status == status)
    if source_type:
        stmt = stmt.where(Document.source_type == source_type)
    if source_id:
        stmt = stmt.where(Document.source_id == source_id)
    rows = list(await ctx.db.scalars(keyset(stmt, Document, cursor, limit)))
    return page(rows, limit, summary)


async def get_document(ctx: Ctx, document_id: uuid.UUID, *, for_update: bool = False) -> Document:
    stmt = select(Document).where(
        Document.id == document_id,
        Document.user_id == ctx.principal.user_id,
        Document.space_id.in_(_readable_spaces(ctx)),
    )
    if for_update:
        stmt = stmt.with_for_update()
    document = await ctx.db.scalar(stmt)
    if document is None:
        raise not_found("document_not_found", "Document not found")
    return document


async def read_original(
    ctx: Ctx, store: DocumentStore, document_id: uuid.UUID
) -> tuple[Document, bytes]:
    document = await get_document(ctx, document_id)
    if document.storage_key is None:
        raise ProblemError(410, "original_purged", "The original file is no longer kept")
    if not store.available:
        raise ProblemError(503, "documents_unavailable", "Document storage is unavailable")
    try:
        data = await asyncio.to_thread(store.read, document.storage_key, document.id)
    except OriginalMissingError as exc:
        raise ProblemError(410, "original_purged", "The original file is no longer kept") from exc
    except StorageError as exc:
        log.error("original_unreadable", document=str(document.id), error=str(exc))
        raise ProblemError(
            500, "original_unreadable", "The original file cannot be decrypted"
        ) from exc
    await _audit(ctx, "document.download", document)
    return document, data


# --- Writing -------------------------------------------------------------------------------


async def create_document(
    ctx: Ctx,
    store: DocumentStore,
    fields: DocumentUploadFields,
    *,
    data: bytes,
    detected: Detected,
    filename: str | None,
) -> Document:
    ctx.principal.require_space(fields.space_id)
    await authorize(ctx, "document.write", fields.space_id)
    if fields.source_type is not None and fields.source_id is not None:
        await _check_source(ctx, fields.source_type, fields.source_id, fields.space_id)
    title = " ".join((fields.title or "").split()) or default_title(filename, fields.purpose)
    document_id = uuid.uuid4()
    document = Document(
        id=document_id,
        user_id=ctx.principal.user_id,
        space_id=fields.space_id,
        title=title,
        kind=detected.kind,
        mime=detected.mime,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        purpose=fields.purpose,
        status="pending",
        sensitivity=[],
        storage_key=DocumentStore.key_for(ctx.principal.user_id, document_id),
        original_until=None if fields.keep_original else retention_end(ctx),
        source_type=fields.source_type,
        source_id=fields.source_id,
    )
    ctx.db.add(document)
    await ctx.db.flush()
    assert document.storage_key is not None
    try:
        await asyncio.to_thread(store.write, document.storage_key, document.id, data)
    except (StorageError, OSError) as exc:
        log.error("document_store_failed", error=type(exc).__name__)
        raise ProblemError(503, "documents_unavailable", "Document storage is unavailable") from exc
    await _audit(
        ctx,
        "document.create",
        document,
        kind=document.kind,
        purpose=document.purpose,
        size_bytes=document.size_bytes,
        keep_original=fields.keep_original,
    )
    return document


def _check_version(document: Document, if_match: int | None) -> None:
    if if_match is not None and if_match != document.version:
        raise ProblemError(
            409,
            "version_conflict",
            "The document was modified elsewhere",
            extra={"current_version": document.version},
        )


async def update_document(
    ctx: Ctx, document_id: uuid.UUID, data: DocumentUpdate, *, if_match: int | None = None
) -> Document:
    document = await get_document(ctx, document_id, for_update=True)
    await authorize(ctx, "document.write", document.space_id)
    _check_version(document, if_match)
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    if "title" in changes:
        title = " ".join(changes["title"].split())
        if not title:
            raise ProblemError(422, "document_title_required", "The title cannot be empty")
        document.title = title
    if "keep_original" in changes:
        if document.storage_key is None:
            raise ProblemError(410, "original_purged", "The original file is no longer kept")
        if changes["keep_original"]:
            document.original_until = None
        elif document.original_until is None:
            document.original_until = retention_end(ctx)
    if "extracted_text" in changes:
        if document.status in ("pending", "processing"):
            raise ProblemError(409, "document_processing", "The document is still being processed")
        text = clean_text(changes["extracted_text"])
        categories, analysis = analyse(
            text, document.purpose, owner_today(ctx.user.timezone), document.analysis
        )
        document.extracted_text = text
        document.text_corrected_at = utcnow()
        document.sensitivity = categories
        document.analysis = analysis
        if document.status == "failed":
            # The owner typed the text of an unreadable document: it is usable now.
            document.status = "ready"
            document.error = None
    await ctx.db.flush()
    await _audit(ctx, "document.update", document, fields=",".join(sorted(changes)))
    return document


async def delete_document(ctx: Ctx, store: DocumentStore, document_id: uuid.UUID) -> None:
    """Erasure: the row (its vectors go with it, by trigger) and the encrypted file."""
    document = await get_document(ctx, document_id, for_update=True)
    await authorize(ctx, "document.delete", document.space_id)
    storage_key = document.storage_key
    await ctx.db.delete(document)
    await ctx.db.flush()
    if storage_key is not None:
        # A failure here leaves an orphan file, removed by the daily sweep.
        try:
            await asyncio.to_thread(store.delete, storage_key)
        except (StorageError, OSError) as exc:
            log.error("document_file_delete_failed", error=type(exc).__name__)
    await _audit(ctx, "document.delete", document)


async def reprocess_document(ctx: Ctx, document_id: uuid.UUID) -> Document:
    """Runs extraction again (e.g. after an OCR improvement); replaces any correction."""
    document = await get_document(ctx, document_id, for_update=True)
    await authorize(ctx, "document.write", document.space_id)
    if document.storage_key is None:
        raise ProblemError(410, "original_purged", "The original file is no longer kept")
    if document.status == "processing":
        raise ProblemError(409, "document_processing", "The document is being processed")
    if document.kind == "audio":
        raise ProblemError(422, "document_not_reprocessable", "Audio is handled by dictation")
    document.status = "pending"
    document.error = None
    await ctx.db.flush()
    await _audit(ctx, "document.reprocess", document)
    return document
