"""E-mail drafts (task 18, ADR 0018): written by the owner, sent only after an approval.

A draft holds exactly what will be sent: recipients (To, Cc, Bcc), subject, plain-text
body, documents of its space as attachments, and the message it answers. Sending it is a
proposal (`send.request_send`); editing or deleting it invalidates that proposal, and a
draft whose sending runs, is uncertain or is done can no longer change.

Its state is derived from its latest send approval (and that approval's claim at the
provider), so that it can never disagree with what the guardian recorded:

- draft: being written, or back after a refusal, an expiry, an edit;
- awaiting_approval: the owner has to decide;
- sending: approved, the provider is being called;
- unknown: sent or maybe not (timeout, crash): checked with the provider, never resent
  blindly; the owner may settle it after checking their sent messages;
- failed: nothing was sent (the error says why); it may be proposed again;
- sent: the provider accepted it.

Addresses, subjects and bodies never reach the audit log or the logs (T7): ids and counts.
"""

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.connectors.models import ConnectorAccount, ConnectorGrant, ConnectorSource
from lifeos.modules.documents import service as documents
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.storage import DocumentStore, StorageError
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval, ExternalOperation
from lifeos.modules.mail import mime
from lifeos.modules.mail.models import MailDraft
from lifeos.modules.mail.schemas import DraftAttachmentOut, DraftIn, DraftOut, DraftPatch
from lifeos.modules.mail.service import get_message

SEND_ACTIONS = ("email.send", "email.send_reinforced")
MAX_RECIPIENTS = 50
MAX_ATTACHMENTS = 10
SUBJECT_CHARS = 300
BODY_CHARS = 100_000
_ADDRESS = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$"
)


def target(draft_id: uuid.UUID) -> str:
    """The object a send approval acts on (invalidation, one pending proposal)."""
    return f"mail_draft:{draft_id}"


# --- Problems ---------------------------------------------------------------------------------


def already_sent() -> ProblemError:
    return ProblemError(409, "draft_already_sent", "This e-mail was already sent")


def outcome_unknown() -> ProblemError:
    return ProblemError(
        409,
        "outcome_unknown",
        "This e-mail is being sent, or whether it left is not known yet",
        "Nothing is sent again meanwhile. Check your sent messages.",
    )


def unavailable() -> ProblemError:
    return ProblemError(
        409,
        "mail_send_unavailable",
        "No connected account may send e-mails in this space",
        "Allow « Envoyer des e-mails » for this space in the connected accounts.",
    )


# --- Validation (the input is never echoed) ---------------------------------------------------


def _addresses(values: list[str], seen: set[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        address = value.strip().lower()
        if not _ADDRESS.match(address):
            raise ProblemError(422, "invalid_recipient", "A recipient address is not valid")
        if address not in seen:
            seen.add(address)
            cleaned.append(address)
    return cleaned


def recipients(
    to: list[str], cc: list[str], bcc: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """Lower case, valid, each address once (To first, then Cc, then Bcc)."""
    seen: set[str] = set()
    out = _addresses(to, seen), _addresses(cc, seen), _addresses(bcc, seen)
    if len(seen) > MAX_RECIPIENTS:
        raise ProblemError(
            422,
            "too_many_recipients",
            "Too many recipients",
            extra={"max_recipients": MAX_RECIPIENTS},
        )
    return out


def clean_subject(value: str) -> str:
    subject = value.strip()
    if len(subject) > SUBJECT_CHARS or not mime.header_safe(subject):
        raise ProblemError(422, "invalid_subject", "The subject must be one line of text")
    return subject


def clean_body(value: str) -> str:
    body = value.replace("\x00", "")
    if len(body) > BODY_CHARS:
        raise ProblemError(
            422, "body_too_long", "The text is too long", extra={"max_chars": BODY_CHARS}
        )
    return body


def _check_write(ctx: Ctx, space_id: uuid.UUID) -> None:
    ctx.principal.require_space(space_id)
    if ctx.principal.kind == "agent" and not ctx.principal.can(space_id, "email", "write"):
        raise ProblemError(
            403,
            "agent_permission_required",
            "The agent has no permission on this data",
            extra={"resource": "email"},
        )


# --- Accounts allowed to send ------------------------------------------------------------------


async def send_accounts(ctx: Ctx, space_id: uuid.UUID) -> list[ConnectorAccount]:
    """Active accounts of the owner with the consent « send e-mails » in this space."""
    ctx.principal.require_space(space_id)
    now = utcnow()
    return list(
        await ctx.db.scalars(
            select(ConnectorAccount)
            .join(ConnectorGrant, ConnectorGrant.account_id == ConnectorAccount.id)
            .where(
                ConnectorAccount.user_id == ctx.principal.user_id,
                ConnectorAccount.status == "active",
                ConnectorGrant.space_id == space_id,
                ConnectorGrant.capability == "mail.send",
                ConnectorGrant.revoked_at.is_(None),
                or_(ConnectorGrant.expires_at.is_(None), ConnectorGrant.expires_at > now),
            )
            .order_by(ConnectorAccount.connected_at)
        )
    )


async def _account(ctx: Ctx, space_id: uuid.UUID, account_id: uuid.UUID | None) -> uuid.UUID:
    accounts = await send_accounts(ctx, space_id)
    if account_id is None:
        if not accounts:
            raise unavailable()
        return accounts[0].id
    if account_id not in {a.id for a in accounts}:
        raise unavailable()
    return account_id


# --- Attachments --------------------------------------------------------------------------------


async def _documents(ctx: Ctx, space_id: uuid.UUID, ids: list[uuid.UUID]) -> list[Document]:
    """Ready documents of the draft's space, with their original kept, in order."""
    if len(ids) > MAX_ATTACHMENTS:
        raise ProblemError(
            422,
            "too_many_attachments",
            "Too many attachments",
            extra={"max_attachments": MAX_ATTACHMENTS},
        )
    found: list[Document] = []
    for document_id in dict.fromkeys(ids):
        document = await documents.get_document(ctx, document_id)
        if document.space_id != space_id:
            raise not_found("document_not_found", "Document not found")
        if (
            document.status != "ready"
            or document.storage_key is None
            or documents.original_withheld(document)
        ):
            raise ProblemError(
                409,
                "attachment_unavailable",
                "This document cannot be attached: its original is not available",
                extra={"document_id": str(document.id)},
            )
        found.append(document)
    return found


def _check_size(settings: Settings, found: list[Document]) -> None:
    if sum(d.size_bytes for d in found) > settings.mail_max_send_bytes:
        raise ProblemError(
            413,
            "mail_too_large",
            "The attachments are too large for one e-mail",
            extra={"max_bytes": settings.mail_max_send_bytes},
        )


async def attachment_files(ctx: Ctx, draft: MailDraft) -> list[tuple[Document, bytes]]:
    """The attached documents and their bytes, read now (checked again at sending)."""
    found = await _documents(ctx, draft.space_id, list(draft.attachment_ids or []))
    _check_size(ctx.settings, found)
    return await read_files(ctx, found)


async def read_files(ctx: Ctx, found: list[Document]) -> list[tuple[Document, bytes]]:
    store = DocumentStore(ctx.settings)
    if found and not store.available:
        raise ProblemError(503, "documents_unavailable", "Document storage is unavailable")
    files: list[tuple[Document, bytes]] = []
    for document in found:
        assert document.storage_key is not None
        try:
            data = await asyncio.to_thread(store.read, document.storage_key, document.id)
        except StorageError as exc:
            raise ProblemError(
                409,
                "attachment_unavailable",
                "This document cannot be attached: its original is not available",
                extra={"document_id": str(document.id)},
            ) from exc
        files.append((document, data))
    return files


# --- State ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DraftState:
    state: str
    approval: Approval | None
    operation: ExternalOperation | None


def derive(draft: MailDraft, approval: Approval | None, operation: ExternalOperation | None) -> str:
    if draft.status == "sent":
        return "sent"
    if draft.status == "draft" or approval is None:
        return "draft"
    claim_open = operation is not None and operation.state in ("claimed", "unknown")
    if approval.status == "pending":
        if claim_open:
            # A request died after claiming the send: maybe sent; settled before any edit.
            return "unknown"
        return "awaiting_approval" if approval.expires_at > utcnow() else "draft"
    if approval.status == "approved":
        return "sending"
    if approval.status == "executed":
        return "sent"
    if approval.status == "failed":
        return "unknown" if approval.error == "outcome_unknown" or claim_open else "failed"
    return "draft"  # rejected, expired, invalidated


async def states(db: AsyncSession, drafts: list[MailDraft]) -> dict[uuid.UUID, DraftState]:
    ids = [d.approval_id for d in drafts if d.approval_id is not None]
    approvals = (
        {a.id: a for a in await db.scalars(select(Approval).where(Approval.id.in_(ids)))}
        if ids
        else {}
    )
    operations = (
        {
            o.approval_id: o
            for o in await db.scalars(
                select(ExternalOperation).where(ExternalOperation.approval_id.in_(ids))
            )
        }
        if ids
        else {}
    )
    out: dict[uuid.UUID, DraftState] = {}
    for draft in drafts:
        approval = approvals.get(draft.approval_id) if draft.approval_id else None
        operation = operations.get(draft.approval_id) if draft.approval_id else None
        out[draft.id] = DraftState(derive(draft, approval, operation), approval, operation)
    return out


async def state_of(db: AsyncSession, draft: MailDraft) -> DraftState:
    return (await states(db, [draft]))[draft.id]


def require_editable(state: DraftState) -> None:
    if state.state == "sent":
        raise already_sent()
    if state.state in ("sending", "unknown"):
        raise outcome_unknown()


# --- Reading ----------------------------------------------------------------------------------


def _visible(ctx: Ctx, space_id: uuid.UUID | None = None) -> frozenset[uuid.UUID]:
    spaces = ctx.principal.spaces_or_all(space_id)
    return frozenset(s for s in spaces if ctx.principal.can(s, "email", "write"))


async def get_draft(ctx: Ctx, draft_id: uuid.UUID, *, for_update: bool = False) -> MailDraft:
    stmt = select(MailDraft).where(
        MailDraft.id == draft_id,
        MailDraft.user_id == ctx.principal.user_id,
        MailDraft.space_id.in_(_visible(ctx)),
    )
    if for_update:
        stmt = stmt.with_for_update()
    draft = await ctx.db.scalar(stmt)
    if draft is None:
        raise not_found("draft_not_found", "Draft not found")
    return draft


async def lock_target(ctx: Ctx, draft_id: uuid.UUID) -> None:
    """Locks the draft's live approvals BEFORE the draft: the order the approval of a send
    takes (approval, then draft), so that an edit and an approval never deadlock."""
    await ctx.db.execute(
        select(Approval.id)
        .where(
            Approval.user_id == ctx.principal.user_id,
            Approval.target == target(draft_id),
            Approval.status.in_(("pending", "approved")),
        )
        .with_for_update()
    )


async def _from_addresses(db: AsyncSession, drafts: list[MailDraft]) -> dict[uuid.UUID, str]:
    ids = {d.account_id for d in drafts if d.account_id is not None}
    if not ids:
        return {}
    rows = await db.execute(
        select(ConnectorAccount.id, ConnectorAccount.email).where(ConnectorAccount.id.in_(ids))
    )
    return {account_id: email for account_id, email in rows.all() if email}


async def views(ctx: Ctx, drafts: list[MailDraft]) -> list[DraftOut]:
    found = await states(ctx.db, drafts)
    senders = await _from_addresses(ctx.db, drafts)
    attached_ids = {i for d in drafts for i in d.attachment_ids or []}
    attached = (
        {
            d.id: d
            for d in await ctx.db.scalars(
                select(Document).where(
                    Document.id.in_(attached_ids), Document.user_id == ctx.principal.user_id
                )
            )
        }
        if attached_ids
        else {}
    )
    out: list[DraftOut] = []
    for draft in drafts:
        state = found[draft.id]
        items = []
        for document_id in draft.attachment_ids or []:
            document = attached.get(document_id)
            items.append(
                DraftAttachmentOut(
                    document_id=document_id,
                    title=document.title if document else None,
                    mime=document.mime if document else None,
                    size_bytes=document.size_bytes if document else None,
                    sensitivity=list(document.sensitivity or []) if document else [],
                    local_only=document.local_only if document else False,
                    available=document is not None,
                )
            )
        approval = state.approval
        out.append(
            DraftOut(
                id=draft.id,
                space_id=draft.space_id,
                account_id=draft.account_id,
                from_address=senders.get(draft.account_id) if draft.account_id else None,
                to=list(draft.to_addresses or []),
                cc=list(draft.cc_addresses or []),
                bcc=list(draft.bcc_addresses or []),
                subject=draft.subject,
                body=draft.body_text,
                attachments=items,
                in_reply_to_id=draft.in_reply_to_id,
                reply=draft.reply_message_id_header is not None
                or draft.reply_thread_id is not None,
                state=state.state,
                approval_id=approval.id if approval else None,
                approval_level=approval.level if approval else None,
                error=approval.error if approval and state.state in ("failed", "unknown") else None,
                sent_at=draft.sent_at,
                version=draft.version,
                created_at=draft.created_at,
                updated_at=draft.updated_at,
            )
        )
    return out


async def view(ctx: Ctx, draft: MailDraft) -> DraftOut:
    return (await views(ctx, [draft]))[0]


async def list_drafts(ctx: Ctx, *, space_id: uuid.UUID | None, limit: int = 100) -> list[MailDraft]:
    return list(
        await ctx.db.scalars(
            select(MailDraft)
            .where(
                MailDraft.user_id == ctx.principal.user_id,
                MailDraft.space_id.in_(_visible(ctx, space_id)),
            )
            .order_by(MailDraft.updated_at.desc(), MailDraft.id.desc())
            .limit(max(1, min(limit, 200)))
        )
    )


# --- Writing ----------------------------------------------------------------------------------


async def _audit(ctx: Ctx, action: str, draft: MailDraft, **details: Any) -> None:
    await audit.record(
        ctx.db,
        action=action,
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=draft.space_id,
        target_type="mail_draft",
        target_id=draft.id,
        details=details,
    )


async def _reply(ctx: Ctx, draft: MailDraft, message_id: uuid.UUID | None) -> None:
    """The message answered: of the same space; it threads the reply when it was read
    through the sending account."""
    draft.in_reply_to_id = None
    draft.reply_message_id_header = None
    draft.reply_thread_id = None
    if message_id is None:
        return
    message = await get_message(ctx, message_id)
    if message.space_id != draft.space_id:
        raise not_found("mail_not_found", "E-mail not found")
    draft.in_reply_to_id = message.id
    draft.reply_message_id_header = message.message_id_header
    source = await ctx.db.get(ConnectorSource, message.source_id)
    if source is not None and source.account_id == draft.account_id:
        draft.reply_thread_id = message.thread_id


async def create_draft(ctx: Ctx, data: DraftIn) -> MailDraft:
    _check_write(ctx, data.space_id)
    to, cc, bcc = recipients(data.to, data.cc, data.bcc)
    found = await _documents(ctx, data.space_id, data.attachment_ids)
    _check_size(ctx.settings, found)
    draft = MailDraft(
        user_id=ctx.principal.user_id,
        space_id=data.space_id,
        account_id=await _account(ctx, data.space_id, data.account_id),
        to_addresses=to,
        cc_addresses=cc,
        bcc_addresses=bcc,
        subject=clean_subject(data.subject),
        body_text=clean_body(data.body),
        attachment_ids=[d.id for d in found],
        status="draft",
    )
    await _reply(ctx, draft, data.in_reply_to_id)
    ctx.db.add(draft)
    await ctx.db.flush()
    await _audit(
        ctx,
        "mail.draft.create",
        draft,
        recipients=len(to) + len(cc) + len(bcc),
        attachments=len(found),
    )
    return draft


async def update_draft(
    ctx: Ctx, draft_id: uuid.UUID, data: DraftPatch, *, if_match: int | None
) -> MailDraft:
    """A new version of the draft: its pending send approval can no longer be approved."""
    await lock_target(ctx, draft_id)
    draft = await get_draft(ctx, draft_id, for_update=True)
    _check_write(ctx, draft.space_id)
    require_editable(await state_of(ctx.db, draft))
    if if_match is not None and if_match != draft.version:
        raise ProblemError(
            409,
            "version_conflict",
            "The draft was modified elsewhere",
            extra={"current_version": draft.version},
        )
    fields = data.model_fields_set
    if fields & {"to", "cc", "bcc"}:
        draft.to_addresses, draft.cc_addresses, draft.bcc_addresses = recipients(
            data.to if data.to is not None else list(draft.to_addresses),
            data.cc if data.cc is not None else list(draft.cc_addresses),
            data.bcc if data.bcc is not None else list(draft.bcc_addresses),
        )
    if "subject" in fields and data.subject is not None:
        draft.subject = clean_subject(data.subject)
    if "body" in fields and data.body is not None:
        draft.body_text = clean_body(data.body)
    if "attachment_ids" in fields and data.attachment_ids is not None:
        found = await _documents(ctx, draft.space_id, data.attachment_ids)
        _check_size(ctx.settings, found)
        draft.attachment_ids = [d.id for d in found]
    if "account_id" in fields and data.account_id is not None:
        draft.account_id = await _account(ctx, draft.space_id, data.account_id)
        await _reply(ctx, draft, draft.in_reply_to_id)
    draft.status = "draft"
    await ctx.db.flush()
    invalidated = await _invalidate(ctx, draft, reason="draft_changed")
    await _audit(ctx, "mail.draft.update", draft, version=draft.version, invalidated=invalidated)
    return draft


async def _invalidate(ctx: Ctx, draft: MailDraft, *, reason: str) -> int:
    from lifeos.modules.guardian.actions import invalidate_targets

    return await invalidate_targets(
        ctx.db, ctx.principal.user_id, [target(draft.id)], reason=reason
    )


async def delete_draft(ctx: Ctx, draft_id: uuid.UUID) -> None:
    await lock_target(ctx, draft_id)
    draft = await get_draft(ctx, draft_id, for_update=True)
    _check_write(ctx, draft.space_id)
    state = await state_of(ctx.db, draft)
    if state.state in ("sending", "unknown"):
        raise outcome_unknown()
    await _invalidate(ctx, draft, reason="draft_deleted")
    await _audit(ctx, "mail.draft.delete", draft, sent=state.state == "sent")
    await ctx.db.delete(draft)


async def known_recipients(db: AsyncSession, user_id: uuid.UUID, space_id: uuid.UUID) -> set[str]:
    """Addresses LifeOS already sent to from this space (kept as long as the sent drafts)."""
    rows = await db.execute(
        select(MailDraft.to_addresses, MailDraft.cc_addresses, MailDraft.bcc_addresses).where(
            MailDraft.user_id == user_id,
            MailDraft.space_id == space_id,
            MailDraft.status == "sent",
        )
    )
    return {address for row in rows.all() for column in row for address in column or []}


async def mark_status(db: AsyncSession, draft_id: uuid.UUID, **values: Any) -> None:
    """A change of state that is not an edit: no new version (the approval stays bound)."""
    await db.execute(
        update(MailDraft)
        .where(MailDraft.id == draft_id)
        .values(**values, updated_at=utcnow())
        .execution_options(synchronize_session="fetch")
    )


# --- Retention ---------------------------------------------------------------------------------


def _redacted_params(params: dict[str, Any]) -> dict[str, Any]:
    def count(key: str) -> int:
        value = params.get(key)
        return len(value) if isinstance(value, list) else 0

    return {
        "redacted": True,
        "draft_id": params.get("draft_id"),
        "recipients": count("to") + count("cc") + count("bcc"),
        "attachments": count("attachments"),
    }


async def purge_and_redact(
    session: AsyncSession, settings: Settings, *, now: datetime | None = None
) -> dict[str, int]:
    """Sent drafts `mail_retention_days` after sending; the exact content kept in decided
    send approvals `mail_approval_redact_days` after the decision (digest and counts stay).
    Never while the outcome of a send is still being checked. Audited by counts."""
    now = now or utcnow()
    purged = await session.execute(
        delete(MailDraft).where(
            MailDraft.status == "sent",
            MailDraft.sent_at < now - timedelta(days=settings.mail_retention_days),
        )
    )
    open_claim = (
        select(ExternalOperation.id)
        .where(
            ExternalOperation.approval_id == Approval.id,
            ExternalOperation.state.in_(("claimed", "unknown")),
        )
        .exists()
    )
    stale = list(
        await session.scalars(
            select(Approval)
            .where(
                Approval.action.in_(SEND_ACTIONS),
                Approval.status.not_in(("pending", "approved")),
                Approval.decided_at.is_not(None),
                Approval.decided_at < now - timedelta(days=settings.mail_approval_redact_days),
                ~Approval.params.has_key("redacted"),
                ~open_claim,
            )
            .limit(1000)
            .with_for_update(skip_locked=True)
        )
    )
    for approval in stale:
        approval.params = _redacted_params(approval.params)
        approval.preview = {
            "redacted": True,
            "summary_line": "Contenu effacé après la durée de conservation",
            "recipients": approval.params["recipients"],
            "attachments": approval.params["attachments"],
        }
    counts = {"drafts": int(purged.rowcount or 0), "approvals_redacted": len(stale)}  # type: ignore[attr-defined]
    if any(counts.values()):
        await audit.record(
            session,
            action="mail.drafts.purge",
            actor_type="system",
            actor_id="purge_mail",
            details={
                "drafts": counts["drafts"],
                "approvals_redacted": counts["approvals_redacted"],
            },
        )
    return counts
