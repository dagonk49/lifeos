"""Sending an e-mail under approval (task 18, ADR 0018).

The owner (or an agent allowed to write e-mails in the space) asks to send a draft: that
is a proposal. Its parameters ARE the message: sender account, To, Cc, Bcc, subject,
plain-text body, attachments (title, type, size, SHA-256, sensitivity) and the message
answered; the preview renders those same fields. Nothing leaves before the owner approves
that exact content:

- the approval is bound to its parameters (guardian digest), to the draft version and to
  the attachments' SHA-256: an edit invalidates it, a changed file or draft refuses it;
- a recipient LifeOS never wrote to from this space, or a sensitive attachment (detected
  sensitive, or « toujours garder en local »), requires the reinforced approval
  (`email.send_reinforced`: a recent passkey login);
- the send runs through guardian/operations.py: claimed once before the provider is
  called, with a Message-ID derived from the approval. Gmail has no idempotency for
  sending: an existing claim (retry, crash) is first looked for at the provider by that
  Message-ID (needs the read capability), never resent blindly. Without the read
  capability, an uncertain send stays uncertain: the owner is notified and settles it.

Audit and logs hold ids and counts only, never an address, a subject or a body (T7).
"""

import hashlib
import uuid
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.connectors import tokens
from lifeos.modules.connectors.base import (
    ConnectorAuthError,
    ConnectorError,
    MailAdapter,
    SentLookup,
)
from lifeos.modules.connectors.models import ConnectorAccount
from lifeos.modules.connectors.service import provider_for, require_capability
from lifeos.modules.guardian import actions
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval, ExternalOperation
from lifeos.modules.guardian.operations import Attempt, ExternalAction, Outcome
from lifeos.modules.identity.models import User
from lifeos.modules.mail import drafts, mime
from lifeos.modules.mail.models import MailDraft
from lifeos.modules.notifications.service import notify

log = get_logger(__name__)

SEND = "email.send"
SEND_REINFORCED = "email.send_reinforced"


# --- Exact parameters -------------------------------------------------------------------------


class AttachmentParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    title: str = Field(max_length=500)
    filename: str = Field(max_length=300)
    mime: str = Field(max_length=200)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    sensitivity: list[str] = Field(default_factory=list, max_length=10)
    local_only: bool = False


class ReplyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mail_id: uuid.UUID | None = None
    message_id_header: str | None = Field(default=None, max_length=998)
    thread_id: str | None = Field(default=None, max_length=4096)


class SendEmailParams(BaseModel):
    """Exactly the message that will be sent (and nothing else can be)."""

    model_config = ConfigDict(extra="forbid")

    draft_id: uuid.UUID
    draft_version: int
    account_id: uuid.UUID
    from_address: str = Field(max_length=320)
    to: list[str] = Field(max_length=drafts.MAX_RECIPIENTS)
    cc: list[str] = Field(default_factory=list, max_length=drafts.MAX_RECIPIENTS)
    bcc: list[str] = Field(default_factory=list, max_length=drafts.MAX_RECIPIENTS)
    subject: str = Field(max_length=drafts.SUBJECT_CHARS)
    body: str = Field(max_length=drafts.BODY_CHARS)
    attachments: list[AttachmentParams] = Field(
        default_factory=list, max_length=drafts.MAX_ATTACHMENTS
    )
    reply: ReplyParams | None = None
    # Recipients LifeOS never sent to from this space: they make the approval reinforced.
    new_recipients: list[str] = Field(default_factory=list, max_length=drafts.MAX_RECIPIENTS)


def reinforced(params: SendEmailParams) -> bool:
    return bool(params.new_recipients) or any(
        a.sensitivity or a.local_only for a in params.attachments
    )


def recipient_count(params: SendEmailParams) -> int:
    return len(params.to) + len(params.cc) + len(params.bcc)


# --- Preview ----------------------------------------------------------------------------------


def preview(params: Any) -> dict[str, Any]:
    """The same fields as the parameters, readable: the owner approves this, exactly."""
    assert isinstance(params, SendEmailParams)
    count = recipient_count(params)
    notes = ["Modifier le brouillon annule cette approbation."]
    if params.new_recipients:
        notes.append(
            "Première fois que LifeOS écrit à : "
            + ", ".join(params.new_recipients)
            + " — vérifiez l'adresse."
        )
    sensitive = [a.title for a in params.attachments if a.sensitivity or a.local_only]
    if sensitive:
        notes.append("Pièce jointe sensible : " + ", ".join(sensitive) + ".")
    if params.bcc:
        notes.append("Les destinataires en copie cachée ne sont pas visibles des autres.")
    subject = params.subject or "(sans objet)"
    return {
        "from": params.from_address,
        "to": list(params.to),
        "cc": list(params.cc),
        "bcc": list(params.bcc),
        "subject": params.subject,
        "body": params.body,
        "attachments": [
            {
                "title": a.title,
                "filename": a.filename,
                "mime": a.mime,
                "size_bytes": a.size_bytes,
                "sensitivity": list(a.sensitivity),
                "local_only": a.local_only,
            }
            for a in params.attachments
        ],
        "reply": params.reply is not None,
        "new_recipients": list(params.new_recipients),
        "reinforced": reinforced(params),
        "summary_line": (f"Envoyer « {subject} » à {count} destinataire{'s' if count > 1 else ''}"),
        "notes": notes,
    }


# --- Proposal ---------------------------------------------------------------------------------


def _adapter(provider: object) -> MailAdapter:
    if not hasattr(provider, "send_mail"):
        raise ProblemError(409, "connector_capability_missing", "No mail for this account")
    adapter: MailAdapter = provider  # type: ignore[assignment]
    return adapter


async def request_send(ctx: Ctx, draft_id: uuid.UUID, *, requested_by: str = "owner") -> Approval:
    """The draft as a proposal to send (nothing leaves before the owner approves it)."""
    await drafts.lock_target(ctx, draft_id)
    draft = await drafts.get_draft(ctx, draft_id, for_update=True)
    state = await drafts.state_of(ctx.db, draft)
    drafts.require_editable(state)
    if state.state == "awaiting_approval" and state.approval is not None:
        return state.approval  # one pending proposal per draft (double click, replay)
    if not draft.to_addresses:
        raise ProblemError(422, "recipient_required", "Give at least one recipient")
    if not draft.subject.strip() and not draft.body_text.strip():
        raise ProblemError(422, "mail_empty", "Write a subject or a text")
    if draft.account_id is None:
        raise drafts.unavailable()
    account = await require_capability(
        ctx, ctx.connectors, draft.account_id, draft.space_id, "mail.send"
    )
    if not account.email:
        raise drafts.unavailable()
    files = await drafts.attachment_files(ctx, draft)
    known = await drafts.known_recipients(ctx.db, ctx.principal.user_id, draft.space_id)
    known.add(account.email.lower())
    everyone = [*draft.to_addresses, *draft.cc_addresses, *draft.bcc_addresses]
    params = SendEmailParams(
        draft_id=draft.id,
        draft_version=draft.version,
        account_id=account.id,
        from_address=account.email.lower(),
        to=list(draft.to_addresses),
        cc=list(draft.cc_addresses),
        bcc=list(draft.bcc_addresses),
        subject=draft.subject,
        body=draft.body_text,
        attachments=[
            AttachmentParams(
                document_id=document.id,
                title=document.title,
                filename=mime.filename_for(document.title, document.mime),
                mime=document.mime,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                sensitivity=list(document.sensitivity or []),
                local_only=document.local_only,
            )
            for document, data in files
        ],
        reply=(
            ReplyParams(
                mail_id=draft.in_reply_to_id,
                message_id_header=draft.reply_message_id_header,
                thread_id=draft.reply_thread_id,
            )
            if draft.reply_message_id_header or draft.reply_thread_id
            else None
        ),
        new_recipients=[a for a in everyone if a not in known],
    )
    action = SEND_REINFORCED if reinforced(params) else SEND
    approval = await actions.request_approval(
        ctx,
        action,
        draft.space_id,
        params,
        requested_by=requested_by,
        target=drafts.target(draft.id),
    )
    await drafts.mark_status(ctx.db, draft.id, status="awaiting_approval", approval_id=approval.id)
    return approval


# --- Execution (after approval) ---------------------------------------------------------------


def _stale(reason: str) -> ProblemError:
    return ProblemError(
        409,
        "approval_stale",
        "The draft changed since this approval was requested",
        extra={"reason": reason},
    )


def _same_content(draft: MailDraft, params: SendEmailParams) -> bool:
    reply_header = params.reply.message_id_header if params.reply else None
    reply_thread = params.reply.thread_id if params.reply else None
    return (
        list(draft.to_addresses) == params.to
        and list(draft.cc_addresses) == params.cc
        and list(draft.bcc_addresses) == params.bcc
        and draft.subject == params.subject
        and draft.body_text == params.body
        and list(draft.attachment_ids) == [a.document_id for a in params.attachments]
        and draft.account_id == params.account_id
        and draft.reply_message_id_header == reply_header
        and draft.reply_thread_id == reply_thread
    )


async def _sent(
    ctx: Ctx,
    params: SendEmailParams,
    attempt: Attempt,
    provider_id: str,
    thread_id: str | None,
    *,
    confirmed_by: str,
) -> Outcome:
    """Local effects of a send that happened (now, or found by the reconciliation)."""
    draft = await ctx.db.scalar(
        select(MailDraft).where(
            MailDraft.id == params.draft_id, MailDraft.user_id == ctx.principal.user_id
        )
    )
    if draft is not None and draft.status != "sent":
        await drafts.mark_status(
            ctx.db,
            draft.id,
            status="sent",
            sent_at=utcnow(),
            message_id_header=mime.message_id(attempt.idempotency_key, params.from_address),
            provider_message_id=provider_id,
            provider_thread_id=thread_id,
        )
    counts = {
        "recipients": recipient_count(params),
        "attachments": len(params.attachments),
    }
    await audit.record(
        ctx.db,
        action="email.sent",
        actor_type=ctx.principal.kind,
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=draft.space_id if draft is not None else None,
        target_type="mail_draft",
        target_id=params.draft_id,
        details={
            **counts,
            "account_id": str(params.account_id),
            "approval_id": str(attempt.approval_id),
            "confirmed_by": confirmed_by,
        },
    )
    log.info(
        "email_sent",
        approval=str(attempt.approval_id),
        draft=str(params.draft_id),
        confirmed_by=confirmed_by,
        **counts,
    )
    return Outcome({"draft_id": str(params.draft_id), **counts}, provider_id)


class _Send:
    """Perform and reconcile of one send action. `reinforced`: the approved level."""

    def __init__(self, reinforced: bool) -> None:
        self.reinforced = reinforced

    async def perform(
        self, ctx: Ctx, space_id: uuid.UUID, params: Any, attempt: Attempt
    ) -> Outcome:
        assert isinstance(params, SendEmailParams)
        if reinforced(params) != self.reinforced:
            raise _stale("level")  # forged: the level is decided from the parameters
        draft = await ctx.db.scalar(
            select(MailDraft)
            .where(
                MailDraft.id == params.draft_id,
                MailDraft.user_id == ctx.principal.user_id,
                MailDraft.space_id == space_id,
            )
            .with_for_update()
        )
        if draft is None:
            raise ProblemError(409, "draft_gone", "This draft no longer exists")
        if draft.status == "sent":
            raise drafts.already_sent()
        if (
            draft.status != "awaiting_approval"
            or draft.approval_id != attempt.approval_id
            or draft.version != params.draft_version
        ):
            raise _stale("draft_changed")
        if not _same_content(draft, params):
            raise _stale("content_changed")
        account = await require_capability(
            ctx, ctx.connectors, params.account_id, space_id, "mail.send"
        )
        if (account.email or "").lower() != params.from_address:
            raise _stale("account_changed")
        found = [await self._document(ctx, space_id, a.document_id) for a in params.attachments]
        files = await drafts.read_files(ctx, found)
        attachments: list[mime.Attachment] = []
        for expected, (document, data) in zip(params.attachments, files, strict=True):
            if hashlib.sha256(data).hexdigest() != expected.sha256 or len(data) != (
                expected.size_bytes
            ):
                raise ProblemError(
                    409,
                    "attachment_changed",
                    "An attachment changed since the approval was requested",
                    extra={"document_id": str(document.id)},
                )
            if (document.sensitivity or document.local_only) and not (
                expected.sensitivity or expected.local_only
            ):
                raise _stale("attachment_sensitive")
            attachments.append(mime.Attachment(expected.filename, expected.mime, data))
        try:
            raw = mime.build(
                key=attempt.idempotency_key,
                from_address=params.from_address,
                to=params.to,
                cc=params.cc,
                bcc=params.bcc,
                subject=params.subject,
                body=params.body,
                attachments=attachments,
                reply=mime.Reply(params.reply.message_id_header) if params.reply else None,
                date=utcnow(),
            )
        except ValueError as exc:
            raise ProblemError(422, "invalid_action_params", "Invalid action parameters") from exc
        provider = provider_for(ctx.connectors, account.provider)
        adapter = _adapter(provider)
        token = await tokens.access_token(ctx.database, ctx.settings, provider, account.id)
        sent = await adapter.send_mail(
            token, raw, thread_id=params.reply.thread_id if params.reply else None
        )
        return await _sent(
            ctx, params, attempt, sent.provider_id, sent.thread_id, confirmed_by="send"
        )

    @staticmethod
    async def _document(ctx: Ctx, space_id: uuid.UUID, document_id: uuid.UUID) -> Any:
        from lifeos.modules.documents.models import Document

        document = await ctx.db.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.user_id == ctx.principal.user_id,
                Document.space_id == space_id,
            )
        )
        if document is None or document.storage_key is None:
            raise ProblemError(
                409,
                "attachment_changed",
                "An attachment changed since the approval was requested",
                extra={"document_id": str(document_id)},
            )
        return document

    async def reconcile(
        self, ctx: Ctx, space_id: uuid.UUID, params: Any, attempt: Attempt
    ) -> Outcome | None:
        """Did the earlier attempt send it? Looked for by its Message-ID at the provider;
        without the read capability, no conclusion (never "not sent")."""
        assert isinstance(params, SendEmailParams)
        approval = await ctx.db.get(Approval, attempt.approval_id)
        since = approval.created_at if approval is not None else utcnow() - timedelta(days=8)
        account = await ctx.db.get(ConnectorAccount, params.account_id)
        if account is None or account.status != "active":
            raise ConnectorAuthError("account_inactive")
        provider = ctx.connectors.get(account.provider)
        if provider is None:
            raise ConnectorError("provider_unavailable")
        if "mail.read" not in provider.capabilities_in(account.scopes):
            await _notify_unknown(ctx, space_id, params, attempt)
            raise ConnectorError("read_scope_missing")
        adapter = _adapter(provider)
        token = await tokens.access_token(ctx.database, ctx.settings, provider, account.id)
        found = await adapter.find_sent_mail(
            token,
            SentLookup(
                message_id_header=mime.message_id(attempt.idempotency_key, params.from_address),
                since=since,
                subject=params.subject,
                recipients=frozenset([*params.to, *params.cc]),
            ),
        )
        if found is None:
            return None
        return await _sent(ctx, params, attempt, found, None, confirmed_by="reconcile")

    @staticmethod
    def account(params: Any) -> uuid.UUID | None:
        return params.account_id if isinstance(params, SendEmailParams) else None


async def _notify_unknown(
    ctx: Ctx, space_id: uuid.UUID, params: SendEmailParams, attempt: Attempt
) -> None:
    """Once per approval (detached: it survives the rollback of the attempt)."""
    async with ctx.database.sessionmaker() as session, session.begin():
        user = await session.get(User, ctx.principal.user_id)
        if user is None:
            return
        await notify(
            session,
            user,
            category="important",
            title="Envoi d'un e-mail incertain",
            body=(
                "LifeOS ne peut pas vérifier si l'e-mail est parti : la lecture de Gmail "
                "n'est pas accordée. Vérifiez vos messages envoyés, puis indiquez-le sur le "
                "brouillon. Rien n'est renvoyé automatiquement."
            ),
            dedup_key=f"mail_send_unknown:{attempt.approval_id}",
            space_id=space_id,
            source_type="mail_draft",
            source_id=params.draft_id,
        )


# --- The owner settles an uncertain send ------------------------------------------------------


async def resolve(ctx: Ctx, draft_id: uuid.UUID, *, sent: bool) -> MailDraft:
    """After checking their sent messages, the owner says whether the e-mail left. Only
    for an uncertain send; « not sent » lets the draft be proposed again (a new approval,
    a new Message-ID)."""
    if ctx.principal.kind != "user":
        raise ProblemError(403, "action_denied", "Only the owner can settle a send")
    await drafts.lock_target(ctx, draft_id)
    draft = await drafts.get_draft(ctx, draft_id, for_update=True)
    state = await drafts.state_of(ctx.db, draft)
    approval = state.approval
    if state.state != "unknown" or approval is None:
        raise ProblemError(409, "draft_not_uncertain", "This e-mail is not in an uncertain state")
    approval = await ctx.db.scalar(
        select(Approval).where(Approval.id == approval.id).with_for_update()
    )
    assert approval is not None
    operation = await ctx.db.scalar(
        select(ExternalOperation)
        .where(ExternalOperation.approval_id == approval.id)
        .with_for_update()
    )
    now = utcnow()
    params = SendEmailParams.model_validate(approval.params)
    if sent:
        key = operation.idempotency_key if operation is not None else None
        await drafts.mark_status(
            ctx.db,
            draft.id,
            status="sent",
            sent_at=now,
            message_id_header=mime.message_id(key, params.from_address) if key else None,
        )
        if operation is not None:
            operation.state, operation.error_code = "succeeded", "owner_confirmed"
            operation.finished_at = now
        approval.status, approval.error = "executed", None
        approval.result = {
            "draft_id": str(draft.id),
            "recipients": recipient_count(params),
            "attachments": len(params.attachments),
            "confirmed_by": "owner",
        }
        approval.executed_at = now
        approval.decided_at = approval.decided_at or now
    else:
        if operation is not None:
            operation.state, operation.error_code = "failed", "owner_not_sent"
            operation.finished_at = now
        if approval.status == "pending":
            approval.status, approval.error = "invalidated", "not_sent"
            approval.decided_at = now
        else:
            approval.error = "not_applied"
        await drafts.mark_status(ctx.db, draft.id, status="draft")
    await audit.record(
        ctx.db,
        action="mail.draft.resolve",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        space_id=draft.space_id,
        target_type="mail_draft",
        target_id=draft.id,
        details={"sent": sent, "approval_id": str(approval.id)},
    )
    await ctx.db.refresh(draft)
    return draft


async def get_send_approval(ctx: Ctx, draft_id: uuid.UUID) -> Approval:
    draft = await drafts.get_draft(ctx, draft_id)
    if draft.approval_id is None:
        raise not_found("approval_not_found", "Approval not found")
    return await actions.get_approval(ctx, draft.approval_id)


def _external(level_reinforced: bool) -> ExternalAction:
    send = _Send(level_reinforced)
    return ExternalAction(perform=send.perform, reconcile=send.reconcile, account_id=send.account)


# Not editable at approval: the parameters are derived from the draft and the documents
# (recipients known or not, attachments' digests); a change goes through the draft.
actions.register(SEND, SendEmailParams, None, preview, external=_external(False), editable=False)
actions.register(
    SEND_REINFORCED, SendEmailParams, None, preview, external=_external(True), editable=False
)
