"""Sending e-mails under approval (task 18, ADR 0018).

Acceptance criteria: no e-mail is ever sent without an explicit approval showing the exact
message (sender, recipients, subject, body, attachments); a replay, a retry or a crash
never sends it twice; a new recipient or a sensitive attachment requires a reinforced
approval (recent passkey). Every test uses the in-memory FakeConnector (its `outbox` is
what would have been delivered), except the Gmail request tests, which go through
httpx.MockTransport (no request reaches Google).
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.main import create_app
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.connectors.base import (
    ConnectorError,
    ConnectorUnavailableError,
    OutcomeUnknownError,
    SentLookup,
)
from lifeos.modules.connectors.fake import FakeConnector
from lifeos.modules.connectors.google import GoogleConnector
from lifeos.modules.connectors.models import ConnectorSource
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.storage import DocumentStore
from lifeos.modules.guardian import actions, operations
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval, ExternalOperation, params_digest
from lifeos.modules.guardian.principal import Principal
from lifeos.modules.identity.models import User, UserSession
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.mail import drafts, mime, send
from lifeos.modules.mail.models import MailDraft, MailMessage
from lifeos.modules.notifications.models import Notification

from .conftest import Owner, login, make_ctx

CONNECTORS_KEY = base64.b64encode(b"m" * 32).decode()
ACCOUNT_EMAIL = "agenda.demo@example.org"


@pytest.fixture
def fake_connector() -> FakeConnector:
    return FakeConnector()


@pytest.fixture
def mail_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "connectors_provider": "fake",
            "connectors_key": SecretStr(CONNECTORS_KEY),
            "documents_dir": str(tmp_path / "documents"),
            "documents_key": SecretStr(base64.b64encode(os.urandom(32)).decode()),
        }
    )


@pytest.fixture
async def app(
    mail_settings: Settings, fake_llm: FakeProvider, fake_connector: FakeConnector
) -> AsyncIterator[FastAPI]:
    application = create_app(mail_settings)
    application.state.llm = fake_llm
    application.state.connectors = {"fake": fake_connector}
    async with application.router.lifespan_context(application):
        yield application


async def connect(client: AsyncClient, space_id: str, capabilities: list[str]) -> str:
    response = await client.post(
        "/v1/connectors/oauth/start",
        json={"provider": "fake", "space_id": space_id, "capabilities": capabilities},
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    callback = await client.post(
        "/v1/connectors/oauth/callback",
        json={"state": query["state"][0], "code": query["code"][0]},
    )
    assert callback.status_code == 200, callback.text
    account_id: str = callback.json()["account"]["id"]
    return account_id


@pytest.fixture
async def sender(client: AsyncClient, spaces: dict[str, str]) -> dict[str, str]:
    """An account allowed to send and to read e-mails in the personal space."""
    space = spaces["personal"]
    account = await connect(client, space, ["mail.send", "mail.read"])
    return {"space": space, "account": account}


@pytest.fixture
async def send_only(client: AsyncClient, spaces: dict[str, str]) -> dict[str, str]:
    """An account allowed to send only: an uncertain send cannot be checked."""
    space = spaces["personal"]
    account = await connect(client, space, ["mail.send"])
    return {"space": space, "account": account}


async def add_document(
    db: AsyncSession,
    settings: Settings,
    owner: Owner,
    space_id: str,
    data: bytes,
    *,
    title: str = "Compte rendu",
    mime_type: str = "text/plain",
    sensitivity: list[str] | None = None,
    local_only: bool = False,
) -> str:
    document_id = uuid.uuid4()
    key = DocumentStore.key_for(owner.user.id, document_id)
    DocumentStore(settings).write(key, document_id, data)
    db.add(
        Document(
            id=document_id,
            user_id=owner.user.id,
            space_id=uuid.UUID(space_id),
            title=title,
            kind="text",
            mime=mime_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            status="ready",
            storage_key=key,
            sensitivity=sensitivity or [],
            local_only=local_only,
        )
    )
    await db.commit()
    return str(document_id)


async def new_draft(client: AsyncClient, space: str, **fields: Any) -> dict[str, Any]:
    body = {
        "space_id": space,
        "to": ["lea@example.org"],
        "subject": "Pique-nique de dimanche",
        "body": "Bonjour Léa,\n\nOn se retrouve à midi ?\n\nÀ bientôt",
        **fields,
    }
    response = await client.post("/v1/mail/drafts", json=body)
    assert response.status_code == 201, response.text
    draft: dict[str, Any] = response.json()
    return draft


async def request_send(client: AsyncClient, draft_id: str, **headers: str) -> httpx.Response:
    return await client.post(f"/v1/mail/drafts/{draft_id}/send", headers=headers)


async def approve(client: AsyncClient, approval_id: str, **body: Any) -> httpx.Response:
    return await client.post(f"/v1/approvals/{approval_id}/approve", json=body)


async def draft_state(client: AsyncClient, draft_id: str) -> dict[str, Any]:
    response = await client.get(f"/v1/mail/drafts/{draft_id}")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def send_once(client: AsyncClient, draft_id: str) -> dict[str, Any]:
    proposal = (await request_send(client, draft_id)).json()
    done = await approve(client, proposal["id"])
    assert done.status_code == 200, done.text
    result: dict[str, Any] = done.json()
    return result


async def operation(db: AsyncSession, approval_id: str) -> ExternalOperation | None:
    db.expire_all()
    return await db.scalar(
        select(ExternalOperation).where(ExternalOperation.approval_id == uuid.UUID(approval_id))
    )


async def owner_ctx(
    database: Database,
    db: AsyncSession,
    settings: Settings,
    owner: Owner,
    llm: FakeProvider,
    connector: FakeConnector,
) -> Ctx:
    user = await db.get(User, owner.user.id)
    assert user is not None
    ctx = await make_ctx(database, db, settings, user, llm)
    ctx.connectors = {"fake": connector}
    return ctx


async def make_session_old(db: AsyncSession, owner: Owner) -> None:
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == owner.user.id)
        .values(authenticated_at=utcnow() - timedelta(hours=1))
    )
    await db.commit()


# --- Nothing leaves without an approval ---------------------------------------------------


async def test_no_email_leaves_without_an_approval(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    mail_settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    draft = await new_draft(client, sender["space"])
    assert draft["state"] == "draft" and draft["from_address"] == ACCOUNT_EMAIL
    proposal = (await request_send(client, draft["id"])).json()
    assert proposal["status"] == "pending"
    assert (await draft_state(client, draft["id"]))["state"] == "awaiting_approval"
    ctx = await owner_ctx(database, db, mail_settings, logged_in, fake_llm, fake_connector)
    approval = await db.get(Approval, uuid.UUID(proposal["id"]))
    assert approval is not None
    # The very parameters of the proposal, without their approval: refused and audited.
    with pytest.raises(ProblemError) as exc:
        await actions.execute(ctx, approval.action, approval.space_id, approval.params)
    assert exc.value.code == "approval_required"
    # A pending approval is no approval either.
    with pytest.raises(ProblemError) as exc:
        await actions.execute(
            ctx, approval.action, approval.space_id, approval.params, approval=approval
        )
    assert exc.value.code == "approval_required"
    await db.rollback()
    denied = list(
        await db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == proposal["action"], AuditEvent.outcome == "denied"
            )
        )
    )
    assert len(denied) == 2 and all(e.details == {"reason": "approval_required"} for e in denied)
    # Rejected: never sent; the draft is a draft again.
    rejected = await client.post(f"/v1/approvals/{proposal['id']}/reject")
    assert rejected.json()["status"] == "rejected"
    assert (await draft_state(client, draft["id"]))["state"] == "draft"
    # Expired: never sent either.
    again = (await request_send(client, draft["id"])).json()
    await db.execute(
        update(Approval)
        .where(Approval.id == uuid.UUID(again["id"]))
        .values(expires_at=utcnow() - timedelta(seconds=1))
    )
    await db.commit()
    expired = await approve(client, again["id"])
    assert expired.status_code == 409 and expired.json()["approval_status"] == "expired"
    assert fake_connector.send_calls == 0 and fake_connector.outbox == []


async def test_the_approval_shows_the_exact_message_and_sends_it_once(
    client: AsyncClient,
    db: AsyncSession,
    mail_settings: Settings,
    logged_in: Owner,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    document = await add_document(
        db, mail_settings, logged_in, sender["space"], b"Ordre du jour\n", title="Ordre du jour"
    )
    draft = await new_draft(
        client,
        sender["space"],
        to=["Lea@Example.org", "lea@example.org"],
        cc=["sam@example.org"],
        bcc=["archive@example.org"],
        subject="Réunion d'été — ordre du jour",
        attachment_ids=[document],
    )
    assert draft["to"] == ["lea@example.org"]
    key = {"Idempotency-Key": "send-1"}
    first = await request_send(client, draft["id"], **key)
    assert first.status_code == 201, first.text
    proposal = first.json()
    # A retried request (same key), and a second click: the same single proposal.
    assert (await request_send(client, draft["id"], **key)).json()["id"] == proposal["id"]
    assert (await request_send(client, draft["id"])).json()["id"] == proposal["id"]
    assert proposal["action"] == "email.send_reinforced"  # first e-mail to these people
    assert proposal["level"] == "reinforced"
    preview = proposal["preview"]
    assert preview["from"] == ACCOUNT_EMAIL
    assert preview["to"] == ["lea@example.org"]
    assert preview["cc"] == ["sam@example.org"]
    assert preview["bcc"] == ["archive@example.org"]
    assert preview["subject"] == "Réunion d'été — ordre du jour"
    assert preview["body"] == "Bonjour Léa,\n\nOn se retrouve à midi ?\n\nÀ bientôt"
    assert preview["attachments"] == [
        {
            "title": "Ordre du jour",
            "filename": "Ordre du jour.txt",
            "mime": "text/plain",
            "size_bytes": 14,
            "sensitivity": [],
            "local_only": False,
        }
    ]
    assert preview["new_recipients"] == [
        "lea@example.org",
        "sam@example.org",
        "archive@example.org",
    ]
    assert "Modifier le brouillon annule cette approbation." in preview["notes"]
    # The parameters are the message: the preview renders exactly them.
    params = proposal["params"]
    assert params["to"] == preview["to"] and params["body"] == preview["body"]
    assert params["attachments"][0]["sha256"] == hashlib.sha256(b"Ordre du jour\n").hexdigest()
    assert fake_connector.send_calls == 0

    done = await approve(client, proposal["id"])
    assert done.status_code == 200 and done.json()["status"] == "executed", done.text
    assert done.json()["result"] == {"draft_id": draft["id"], "recipients": 3, "attachments": 1}
    [sent] = fake_connector.outbox
    key_id = operations.idempotency_key(uuid.UUID(proposal["id"]))
    assert sent.message_id_header == f"<{key_id}@example.org>"
    assert sent.from_address == ACCOUNT_EMAIL
    assert sent.to == ("lea@example.org",) and sent.cc == ("sam@example.org",)
    assert sent.bcc == ("archive@example.org",)  # read by Gmail, never in To or Cc
    assert "archive@example.org" not in sent.to + sent.cc
    assert sent.subject == "Réunion d'été — ordre du jour"
    assert sent.body.replace("\r\n", "\n").rstrip("\n") == preview["body"]
    assert sent.attachments == (("Ordre du jour.txt", "text/plain", b"Ordre du jour\n"),)
    stored = await draft_state(client, draft["id"])
    assert stored["state"] == "sent" and stored["sent_at"] is not None
    op = await operation(db, proposal["id"])
    assert op is not None and op.state == "succeeded" and op.provider_ref == "sent-1"

    # Replayed approval, new send request, edit: refused, still one e-mail.
    replay = await approve(client, proposal["id"])
    assert replay.status_code == 409 and replay.json()["code"] == "approval_not_pending"
    resend = await request_send(client, draft["id"])
    assert resend.status_code == 409 and resend.json()["code"] == "draft_already_sent"
    edit = await client.patch(f"/v1/mail/drafts/{draft['id']}", json={"body": "autre"})
    assert edit.status_code == 409 and edit.json()["code"] == "draft_already_sent"
    assert len(fake_connector.outbox) == 1 and fake_connector.send_calls == 1


async def test_a_known_recipient_needs_a_plain_approval_a_new_one_a_passkey(
    client: AsyncClient,
    db: AsyncSession,
    mail_settings: Settings,
    logged_in: Owner,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    first = await new_draft(client, sender["space"])
    assert (await send_once(client, first["id"]))["status"] == "executed"
    # Written to before from this space: an ordinary approval.
    again = await new_draft(client, sender["space"], subject="Et pour le dessert ?")
    proposal = (await request_send(client, again["id"])).json()
    assert proposal["action"] == "email.send" and proposal["level"] == "approval"
    assert proposal["preview"]["new_recipients"] == []
    # The owner's own address is never "new".
    to_me = await new_draft(client, sender["space"], to=[ACCOUNT_EMAIL])
    assert (await request_send(client, to_me["id"])).json()["level"] == "approval"
    # Someone new among them: reinforced, and the preview names them.
    mixed = await new_draft(client, sender["space"], cc=["nouveau@example.net"])
    reinforced = (await request_send(client, mixed["id"])).json()
    assert reinforced["action"] == "email.send_reinforced"
    assert reinforced["preview"]["new_recipients"] == ["nouveau@example.net"]
    assert any("nouveau@example.net" in n for n in reinforced["preview"]["notes"])

    # Without a recent passkey login: refused, audited, nothing sent.
    await make_session_old(db, logged_in)
    refused = await approve(client, reinforced["id"])
    assert refused.status_code == 403 and refused.json()["code"] == "reauthentication_required"
    assert len(fake_connector.outbox) == 1
    # The ordinary one goes through without it.
    assert (await approve(client, proposal["id"])).json()["status"] == "executed"
    await login(client, logged_in.authenticator)
    assert (await approve(client, reinforced["id"])).json()["status"] == "executed"
    assert len(fake_connector.outbox) == 3


async def test_a_sensitive_attachment_needs_a_reinforced_approval(
    client: AsyncClient,
    db: AsyncSession,
    mail_settings: Settings,
    logged_in: Owner,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    await send_once(client, (await new_draft(client, sender["space"]))["id"])
    payslip = await add_document(
        db, mail_settings, logged_in, sender["space"], b"Salaire", sensitivity=["bank"]
    )
    kept = await add_document(db, mail_settings, logged_in, sender["space"], b"x", local_only=True)
    for document in (payslip, kept):
        draft = await new_draft(client, sender["space"], attachment_ids=[document])
        proposal = (await request_send(client, draft["id"])).json()
        assert proposal["action"] == "email.send_reinforced", proposal
        assert proposal["level"] == "reinforced"
        assert any("sensible" in n for n in proposal["preview"]["notes"])
    # A document that became sensitive after the request: refused at sending.
    plain = await add_document(db, mail_settings, logged_in, sender["space"], b"Facture")
    draft = await new_draft(client, sender["space"], attachment_ids=[plain])
    proposal = (await request_send(client, draft["id"])).json()
    assert proposal["action"] == "email.send"
    await db.execute(
        update(Document).where(Document.id == uuid.UUID(plain)).values(sensitivity=["bank"])
    )
    await db.commit()
    failed = (await approve(client, proposal["id"])).json()
    assert failed["status"] == "failed" and failed["error"] == "approval_stale"
    assert len(fake_connector.outbox) == 1


def test_the_policy_requires_a_reinforced_approval_for_new_recipients() -> None:
    from lifeos.modules.guardian.policy import ACTIONS

    assert ACTIONS["email.send"].resource == "email"
    assert ACTIONS["email.send_reinforced"].approval_level == "reinforced"
    base: dict[str, Any] = {
        "draft_id": uuid.uuid4(),
        "draft_version": 1,
        "account_id": uuid.uuid4(),
        "from_address": "moi@example.org",
        "to": ["a@example.org"],
        "subject": "s",
        "body": "b",
    }
    assert not send.reinforced(send.SendEmailParams(**base))
    assert send.reinforced(send.SendEmailParams(**base, new_recipients=["a@example.org"]))


# --- Edits, tampering --------------------------------------------------------------------


async def test_editing_the_draft_invalidates_its_approval(
    client: AsyncClient, db: AsyncSession, fake_connector: FakeConnector, sender: dict[str, str]
) -> None:
    draft = await new_draft(client, sender["space"])
    proposal = (await request_send(client, draft["id"])).json()
    stale_version = draft["version"]
    edited = await client.patch(
        f"/v1/mail/drafts/{draft['id']}",
        json={"body": "Finalement, rendez-vous à 13 h."},
        headers={"If-Match": str(stale_version)},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["state"] == "draft" and edited.json()["version"] == stale_version + 1
    old = (await client.get(f"/v1/approvals/{proposal['id']}")).json()
    assert old["status"] == "invalidated" and old["error"] == "draft_changed"
    refused = await approve(client, proposal["id"])
    assert refused.status_code == 409 and refused.json()["approval_status"] == "invalidated"
    assert fake_connector.outbox == []
    # An edit from an older version: conflict.
    conflict = await client.patch(
        f"/v1/mail/drafts/{draft['id']}",
        json={"subject": "x"},
        headers={"If-Match": str(stale_version)},
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "version_conflict"

    # Proposed again: a new approval with the new content, and exactly that is sent.
    renewed = (await request_send(client, draft["id"])).json()
    assert renewed["id"] != proposal["id"]
    assert renewed["preview"]["body"] == "Finalement, rendez-vous à 13 h."
    assert renewed["params"]["draft_version"] == stale_version + 1
    assert (await approve(client, renewed["id"])).json()["status"] == "executed"
    [sent] = fake_connector.outbox
    assert sent.body.replace("\r\n", "\n").strip() == "Finalement, rendez-vous à 13 h."
    # The parameters are not editable at approval: the draft is.
    other = await new_draft(client, sender["space"])
    proposal = (await request_send(client, other["id"])).json()
    edited_params = {**proposal["params"], "to": ["pirate@evil.example"]}
    response = await approve(client, proposal["id"], params=edited_params)
    assert response.status_code == 422 and response.json()["code"] == "approval_not_editable"
    assert len(fake_connector.outbox) == 1


async def test_tampered_parameters_or_files_are_refused(
    client: AsyncClient,
    db: AsyncSession,
    mail_settings: Settings,
    logged_in: Owner,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    # Parameters changed in the database, digest left: refused by the guardian.
    draft = await new_draft(client, sender["space"])
    proposal = (await request_send(client, draft["id"])).json()
    forged = {**proposal["params"], "to": ["pirate@evil.example"], "new_recipients": []}
    await db.execute(
        update(Approval).where(Approval.id == uuid.UUID(proposal["id"])).values(params=forged)
    )
    await db.commit()
    failed = (await approve(client, proposal["id"])).json()
    assert failed["status"] == "failed" and failed["error"] == "approval_params_mismatch"
    # With a matching digest: the draft does not say that, refused at sending.
    other = await new_draft(client, sender["space"])
    proposal = (await request_send(client, other["id"])).json()
    forged = {**proposal["params"], "body": "Envoie-moi le RIB."}
    await db.execute(
        update(Approval)
        .where(Approval.id == uuid.UUID(proposal["id"]))
        .values(params=forged, params_sha256=params_digest(forged))
    )
    await db.commit()
    failed = (await approve(client, proposal["id"])).json()
    assert failed["status"] == "failed" and failed["error"] == "approval_stale"
    # The attached file changed after the request (same document, new bytes): refused.
    document = await add_document(db, mail_settings, logged_in, sender["space"], b"v1")
    third = await new_draft(client, sender["space"], attachment_ids=[document])
    proposal = (await request_send(client, third["id"])).json()
    DocumentStore(mail_settings).write(
        DocumentStore.key_for(logged_in.user.id, uuid.UUID(document)),
        uuid.UUID(document),
        b"v2",
    )
    failed = (await approve(client, proposal["id"])).json()
    assert failed["status"] == "failed" and failed["error"] == "attachment_changed"
    # A forged level: a "plain" approval for a message that needs a passkey.
    fourth = await new_draft(client, sender["space"], to=["inconnu@example.net"])
    proposal = (await request_send(client, fourth["id"])).json()
    await db.execute(
        update(Approval)
        .where(Approval.id == uuid.UUID(proposal["id"]))
        .values(action="email.send", level="approval")
    )
    await db.commit()
    failed = (await approve(client, proposal["id"])).json()
    assert failed["status"] == "failed" and failed["error"] == "approval_stale"
    assert fake_connector.send_calls == 0 and fake_connector.outbox == []
    # Failed without sending: the draft may be proposed again.
    assert (await draft_state(client, draft["id"]))["state"] == "failed"
    assert (await request_send(client, draft["id"])).status_code == 201


# --- Never twice --------------------------------------------------------------------------


async def test_concurrent_approvals_send_once(
    client: AsyncClient, fake_connector: FakeConnector, sender: dict[str, str]
) -> None:
    draft = await new_draft(client, sender["space"])
    proposal = (await request_send(client, draft["id"])).json()
    first, second = await asyncio.gather(
        approve(client, proposal["id"]), approve(client, proposal["id"])
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert len(fake_connector.outbox) == 1


async def test_a_crash_after_sending_never_sends_twice(
    client: AsyncClient, db: AsyncSession, fake_connector: FakeConnector, sender: dict[str, str]
) -> None:
    draft = await new_draft(client, sender["space"])
    proposal = (await request_send(client, draft["id"])).json()
    # Gmail accepts the message, then the process dies before recording it.
    fake_connector.fail_after_next("send_mail", RuntimeError("worker died"))
    with pytest.raises(RuntimeError):
        await approve(client, proposal["id"])
    db.expire_all()
    stored = await db.get(Approval, uuid.UUID(proposal["id"]))
    assert stored is not None and stored.status == "pending"  # the request rolled back
    op = await operation(db, proposal["id"])
    assert op is not None and op.state == "claimed"  # the claim did not
    # Maybe sent: the draft can no longer change.
    assert (await draft_state(client, draft["id"]))["state"] == "unknown"
    edit = await client.patch(f"/v1/mail/drafts/{draft['id']}", json={"body": "x"})
    assert edit.status_code == 409 and edit.json()["code"] == "outcome_unknown"
    deleted = await client.delete(f"/v1/mail/drafts/{draft['id']}")
    assert deleted.status_code == 409

    # Approved again: the provider is asked first, the e-mail is found, nothing resent.
    retried = await approve(client, proposal["id"])
    assert retried.json()["status"] == "executed", retried.text
    assert fake_connector.send_calls == 1 and len(fake_connector.outbox) == 1
    assert fake_connector.find_calls == 1
    assert (await draft_state(client, draft["id"]))["state"] == "sent"


async def test_found_even_when_the_provider_rewrote_the_message_id(
    client: AsyncClient, fake_connector: FakeConnector, sender: dict[str, str]
) -> None:
    fake_connector.rewrite_message_id = True
    draft = await new_draft(client, sender["space"])
    proposal = (await request_send(client, draft["id"])).json()
    fake_connector.fail_after_next("send_mail", RuntimeError("worker died"))
    with pytest.raises(RuntimeError):
        await approve(client, proposal["id"])
    # Same subject and visible recipients since the proposal: taken as sent.
    assert (await approve(client, proposal["id"])).json()["status"] == "executed"
    assert len(fake_connector.outbox) == 1


async def test_an_unknown_outcome_is_reconciled_never_resent(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    mail_settings: Settings,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    draft = await new_draft(client, sender["space"])
    proposal = (await request_send(client, draft["id"])).json()
    fake_connector.fail_after_next("send_mail", OutcomeUnknownError("timeout"))
    failed = (await approve(client, proposal["id"])).json()
    assert failed["status"] == "failed" and failed["error"] == "outcome_unknown"
    state = await draft_state(client, draft["id"])
    assert state["state"] == "unknown" and state["error"] == "outcome_unknown"
    # A new request to send it: refused while it is uncertain.
    again = await request_send(client, draft["id"])
    assert again.status_code == 409 and again.json()["code"] == "outcome_unknown"

    counts = await operations.reconcile_due(database, mail_settings, {"fake": fake_connector})
    assert counts == {"succeeded": 1}
    assert fake_connector.send_calls == 1  # checked, never sent again
    db.expire_all()
    stored = await db.get(Approval, uuid.UUID(proposal["id"]))
    assert stored is not None and stored.status == "executed"
    assert (await draft_state(client, draft["id"]))["state"] == "sent"


async def test_not_sent_before_the_crash_may_be_sent_once_after(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    mail_settings: Settings,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    draft = await new_draft(client, sender["space"])
    proposal = (await request_send(client, draft["id"])).json()
    # The request died before reaching Gmail: the claim stays, nothing was sent.
    fake_connector.fail_next("send_mail", RuntimeError("worker died"))
    with pytest.raises(RuntimeError):
        await approve(client, proposal["id"])
    await db.execute(
        update(ExternalOperation)
        .where(ExternalOperation.approval_id == uuid.UUID(proposal["id"]))
        .values(claimed_at=utcnow() - timedelta(minutes=10))
    )
    await db.commit()
    counts = await operations.reconcile_due(database, mail_settings, {"fake": fake_connector})
    assert counts == {"not_applied": 1}
    assert (await draft_state(client, draft["id"]))["state"] == "awaiting_approval"
    done = (await approve(client, proposal["id"])).json()
    assert done["status"] == "executed", done
    assert len(fake_connector.outbox) == 1


async def test_without_the_read_scope_an_uncertain_send_stays_uncertain(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    mail_settings: Settings,
    fake_connector: FakeConnector,
    send_only: dict[str, str],
) -> None:
    draft = await new_draft(client, send_only["space"])
    proposal = (await request_send(client, draft["id"])).json()
    fake_connector.fail_after_next("send_mail", OutcomeUnknownError("timeout"))
    assert (await approve(client, proposal["id"])).json()["error"] == "outcome_unknown"
    for _ in range(2):
        counts = await operations.reconcile_due(database, mail_settings, {"fake": fake_connector})
        assert counts == {"unknown": 1}
    assert fake_connector.find_calls == 0 and fake_connector.send_calls == 1
    notifications = list(await db.scalars(select(Notification)))
    assert len(notifications) == 1  # once, however many checks
    assert notifications[0].title == "Envoi d'un e-mail incertain"
    assert "lea@example.org" not in (notifications[0].body or "")
    assert (await draft_state(client, draft["id"]))["state"] == "unknown"

    # The owner checked their sent messages: it did not leave. Proposed again, sent once.
    resolved = await client.post(f"/v1/mail/drafts/{draft['id']}/resolve", json={"sent": False})
    assert resolved.status_code == 200 and resolved.json()["state"] == "draft", resolved.text
    renewed = (await request_send(client, draft["id"])).json()
    assert renewed["id"] != proposal["id"]
    fake_connector.outbox.clear()
    assert (await approve(client, renewed["id"])).json()["status"] == "executed"
    assert len(fake_connector.outbox) == 1
    assert fake_connector.outbox[0].message_id_header != (
        f"<{operations.idempotency_key(uuid.UUID(proposal['id']))}@example.org>"
    )
    # Settling a draft that is not uncertain: refused.
    refused = await client.post(f"/v1/mail/drafts/{draft['id']}/resolve", json={"sent": True})
    assert refused.status_code == 409 and refused.json()["code"] == "draft_not_uncertain"


async def test_the_owner_may_confirm_an_uncertain_send(
    client: AsyncClient, db: AsyncSession, fake_connector: FakeConnector, send_only: dict[str, str]
) -> None:
    draft = await new_draft(client, send_only["space"])
    proposal = (await request_send(client, draft["id"])).json()
    fake_connector.fail_after_next("send_mail", OutcomeUnknownError("timeout"))
    await approve(client, proposal["id"])
    resolved = await client.post(f"/v1/mail/drafts/{draft['id']}/resolve", json={"sent": True})
    assert resolved.json()["state"] == "sent"
    approval = (await client.get(f"/v1/approvals/{proposal['id']}")).json()
    assert approval["status"] == "executed" and approval["result"]["confirmed_by"] == "owner"
    op = await operation(db, proposal["id"])
    assert op is not None and op.state == "succeeded" and op.error_code == "owner_confirmed"


# --- Replies, validation, scope -------------------------------------------------------------


async def test_a_reply_is_threaded(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: Owner,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    source = ConnectorSource(
        user_id=logged_in.user.id,
        space_id=uuid.UUID(sender["space"]),
        account_id=uuid.UUID(sender["account"]),
        kind="mail_label",
        provider_ref="INBOX",
        name="Boîte de réception",
    )
    db.add(source)
    await db.flush()
    message = MailMessage(
        user_id=logged_in.user.id,
        space_id=uuid.UUID(sender["space"]),
        source_id=source.id,
        provider_message_id="m-1",
        thread_id="thread-9",
        from_address="ecole@example.org",
        subject="Sortie scolaire",
        body_text="Merci de répondre.",
        received_at=utcnow(),
        purge_after=utcnow() + timedelta(days=90),
        message_id_header="<sortie-1@ecole.example.org>",
    )
    db.add(message)
    await db.commit()
    draft = await new_draft(
        client,
        sender["space"],
        to=["ecole@example.org"],
        subject="Re: Sortie scolaire",
        in_reply_to_id=str(message.id),
    )
    assert draft["reply"] is True and draft["in_reply_to_id"] == str(message.id)
    proposal = (await request_send(client, draft["id"])).json()
    assert proposal["preview"]["reply"] is True
    assert (await approve(client, proposal["id"])).json()["status"] == "executed"
    [sent] = fake_connector.outbox
    assert sent.in_reply_to == "<sortie-1@ecole.example.org>"
    assert sent.references == "<sortie-1@ecole.example.org>"
    assert sent.thread_id == "thread-9"


async def test_invalid_drafts_are_refused_without_echoing_the_input(
    client: AsyncClient,
    db: AsyncSession,
    mail_settings: Settings,
    logged_in: Owner,
    spaces: dict[str, str],
    sender: dict[str, str],
) -> None:
    space = sender["space"]
    bad = await client.post(
        "/v1/mail/drafts", json={"space_id": space, "to": ["pas une adresse"], "subject": "s"}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_recipient"
    assert "pas une adresse" not in bad.text
    header = await client.post(
        "/v1/mail/drafts",
        json={"space_id": space, "to": ["a@example.org"], "subject": "s\r\nBcc: x@evil.example"},
    )
    assert header.status_code == 422 and header.json()["code"] == "invalid_subject"
    many = await client.post(
        "/v1/mail/drafts",
        json={
            "space_id": space,
            "to": [f"p{i}@example.org" for i in range(30)],
            "cc": [f"q{i}@example.org" for i in range(30)],
        },
    )
    assert many.status_code == 422 and many.json()["code"] == "too_many_recipients"
    # A document of another space, or a space without the consent to send.
    elsewhere = await add_document(db, mail_settings, logged_in, spaces["professional"], b"x")
    other = await client.post(
        "/v1/mail/drafts",
        json={"space_id": space, "to": ["a@example.org"], "attachment_ids": [elsewhere]},
    )
    assert other.status_code == 404 and other.json()["code"] == "document_not_found"
    no_consent = await client.post(
        "/v1/mail/drafts", json={"space_id": spaces["professional"], "to": ["a@example.org"]}
    )
    assert no_consent.status_code == 409 and no_consent.json()["code"] == "mail_send_unavailable"
    # No recipient, nothing written: nothing to propose.
    empty = await new_draft(client, space, to=[])
    response = await request_send(client, empty["id"])
    assert response.status_code == 422 and response.json()["code"] == "recipient_required"
    accounts = (await client.get(f"/v1/mail/send-accounts?space_id={space}")).json()
    assert [a["email"] for a in accounts] == [ACCOUNT_EMAIL]


async def test_too_large_attachments_are_refused(
    client: AsyncClient,
    db: AsyncSession,
    mail_settings: Settings,
    logged_in: Owner,
    app: FastAPI,
    sender: dict[str, str],
) -> None:
    app.state.settings = mail_settings.model_copy(update={"mail_max_send_bytes": 1024 * 1024})
    big = await add_document(
        db, mail_settings, logged_in, sender["space"], b"x" * (1024 * 1024 + 1)
    )
    response = await client.post(
        "/v1/mail/drafts",
        json={"space_id": sender["space"], "to": ["a@example.org"], "attachment_ids": [big]},
    )
    assert response.status_code == 413 and response.json()["code"] == "mail_too_large"


async def test_an_agent_proposes_but_never_approves(
    database: Database,
    db: AsyncSession,
    mail_settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    from lifeos.modules.mail.schemas import DraftIn

    space = uuid.UUID(sender["space"])
    ctx = await owner_ctx(database, db, mail_settings, logged_in, fake_llm, fake_connector)
    writer = Principal(
        kind="agent",
        user_id=logged_in.user.id,
        actor_id="core",
        agent="core",
        space_ids=frozenset({space}),
        grants=frozenset({(space, "email", "write")}),
    )
    agent_ctx = Ctx(**{**ctx.__dict__, "principal": writer})
    draft = await drafts.create_draft(
        agent_ctx, DraftIn(space_id=space, to=["a@example.org"], subject="Proposé")
    )
    approval = await send.request_send(agent_ctx, draft.id, requested_by="agent:core")
    assert approval.status == "pending" and approval.requested_by == "agent:core"
    with pytest.raises(ProblemError) as exc:
        await actions.approve(agent_ctx, approval.id)
    assert exc.value.code == "action_denied"
    reader = Principal(
        kind="agent",
        user_id=logged_in.user.id,
        actor_id="core",
        agent="core",
        space_ids=frozenset({space}),
        grants=frozenset({(space, "email", "read")}),
    )
    with pytest.raises(ProblemError) as exc:
        await drafts.create_draft(
            Ctx(**{**ctx.__dict__, "principal": reader}),
            DraftIn(space_id=space, to=["a@example.org"]),
        )
    assert exc.value.code == "agent_permission_required"
    assert fake_connector.outbox == []


async def test_audit_and_logs_hold_no_address_nor_content(
    client: AsyncClient,
    db: AsyncSession,
    fake_connector: FakeConnector,
    sender: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    draft = await new_draft(client, sender["space"], bcc=["secret@example.org"])
    proposal = (await request_send(client, draft["id"])).json()
    await approve(client, proposal["id"])
    events = list(await db.scalars(select(AuditEvent)))
    sent = [e for e in events if e.action == "email.sent"]
    assert len(sent) == 1
    assert sent[0].details["recipients"] == 2 and sent[0].details["attachments"] == 0
    dumped = json.dumps([e.details for e in events]) + caplog.text
    for secret in ("lea@example.org", "secret@example.org", "Pique-nique", "Bonjour Léa"):
        assert secret not in dumped


# --- Retention ---------------------------------------------------------------------------


async def test_sent_drafts_are_purged_and_approvals_redacted(
    client: AsyncClient,
    db: AsyncSession,
    mail_settings: Settings,
    fake_connector: FakeConnector,
    sender: dict[str, str],
) -> None:
    draft = await new_draft(client, sender["space"])
    done = await send_once(client, draft["id"])
    pending = await new_draft(client, sender["space"], subject="En attente")
    waiting = (await request_send(client, pending["id"])).json()
    later = utcnow() + timedelta(days=mail_settings.mail_retention_days + 1)
    counts = await drafts.purge_and_redact(db, mail_settings, now=later)
    await db.commit()
    assert counts == {"drafts": 1, "approvals_redacted": 1}
    db.expire_all()
    redacted = await db.get(Approval, uuid.UUID(done["id"]))
    assert redacted is not None and redacted.params["redacted"] is True
    assert redacted.params["recipients"] == 1 and "body" not in redacted.params
    assert "lea@example.org" not in json.dumps([redacted.params, redacted.preview])
    assert redacted.params_sha256 is not None  # the digest stays
    kept = await db.get(Approval, uuid.UUID(waiting["id"]))
    assert kept is not None and "body" in kept.params  # still to decide
    assert await db.scalar(select(func.count()).select_from(MailDraft)) == 1
    audit = await db.scalar(select(AuditEvent).where(AuditEvent.action == "mail.drafts.purge"))
    assert audit is not None and audit.details == {"drafts": 1, "approvals_redacted": 1}


# --- MIME and the Gmail requests -------------------------------------------------------------


def test_the_message_is_deterministic_plain_text_and_encoded() -> None:
    raw = mime.build(
        key="lo0123456789abcdefghijklmnop",
        from_address="moi@example.org",
        to=["lea@example.org"],
        cc=[],
        bcc=["b@example.org"],
        subject="Réunion d'été",
        body="Bonjour,\nÇa va ?",
        attachments=[mime.Attachment("notes.txt", "text/plain", b"hello")],
        reply=mime.Reply("<abc@x.example>"),
        date=datetime(2026, 10, 30, 9, 0, tzinfo=UTC),
    )
    expected = (
        "From: moi@example.org\r\n"
        "To: lea@example.org\r\n"
        "Bcc: b@example.org\r\n"
        "Subject: =?utf-8?b?UsOpdW5pb24gZCfDqXTDqQ==?=\r\n"
        "Date: Fri, 30 Oct 2026 09:00:00 +0000\r\n"
        "Message-ID: <lo0123456789abcdefghijklmnop@example.org>\r\n"
        "In-Reply-To: <abc@x.example>\r\n"
        "References: <abc@x.example>\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="=_lo0123456789abcdefghijklmnop"\r\n'
        "\r\n"
        "--=_lo0123456789abcdefghijklmnop\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "Content-Transfer-Encoding: quoted-printable\r\n"
        "\r\n"
        "Bonjour,\r\n"
        "=C3=87a va ?\r\n"
        "\r\n"
        "--=_lo0123456789abcdefghijklmnop\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        'Content-Disposition: attachment; filename="notes.txt"\r\n'
        "MIME-Version: 1.0\r\n"
        "\r\n"
        "aGVsbG8=\r\n"
        "\r\n"
        "--=_lo0123456789abcdefghijklmnop--\r\n"
    )
    assert raw.decode() == expected
    # Same approval, same message; never a header injection.
    assert mime.message_id("lo1", "Moi@Example.ORG") == "<lo1@example.org>"
    with pytest.raises(ValueError):
        mime.build(
            key="lo1",
            from_address="moi@example.org",
            to=["a@example.org\r\nBcc: x@evil.example"],
            cc=[],
            bcc=[],
            subject="s",
            body="b",
            attachments=[],
            reply=None,
            date=datetime(2026, 10, 30, tzinfo=UTC),
        )
    assert mime.filename_for("Facture: mars/avril", "application/pdf") == "Facture mars avril.pdf"


def google(settings: Settings, handler: Any) -> GoogleConnector:
    configured = settings.model_copy(
        update={"google_client_id": "client-id", "google_client_secret": SecretStr("secret")}
    )
    return GoogleConnector(configured, transport=httpx.MockTransport(handler))


async def test_gmail_send_request_shape(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "18c0", "threadId": "18bf"})

    connector = google(settings, handler)
    raw = b"From: moi@example.org\r\nSubject: s\r\n\r\nbody\r\n"
    sent = await connector.send_mail("token-1", raw, thread_id="18bf")
    assert sent.provider_id == "18c0" and sent.thread_id == "18bf"
    [request] = seen
    assert request.method == "POST"
    assert request.url.host == "gmail.googleapis.com"
    assert request.url.path == "/gmail/v1/users/me/messages/send"
    assert request.headers["authorization"] == "Bearer token-1"
    body = json.loads(request.content)
    assert body["threadId"] == "18bf"
    assert base64.urlsafe_b64decode(body["raw"] + "=" * (-len(body["raw"]) % 4)) == raw
    assert "token-1" not in str(request.url)

    # A large message goes through the upload endpoint, as message/rfc822.
    seen.clear()
    large = b"From: moi@example.org\r\n\r\n" + b"x" * (5 * 1024 * 1024)
    await connector.send_mail("token-1", large)
    [request] = seen
    assert request.url.path == "/upload/gmail/v1/users/me/messages/send"
    assert request.url.params["uploadType"] == "multipart"
    assert request.headers["content-type"].startswith("multipart/related; boundary=")
    assert b"Content-Type: message/rfc822\r\n\r\n" + large in request.content


async def test_gmail_send_failures_are_unknown_only_after_sending(settings: Settings) -> None:
    def status(code: int) -> Any:
        return lambda request: httpx.Response(code, json={"error": {"code": code}})

    with pytest.raises(OutcomeUnknownError):
        await google(settings, status(503)).send_mail("t", b"x")
    with pytest.raises(ConnectorError) as exc:
        await google(settings, status(400)).send_mail("t", b"x")
    assert not isinstance(exc.value, OutcomeUnknownError)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(OutcomeUnknownError):
        await google(settings, timeout).send_mail("t", b"x")

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ConnectorUnavailableError):
        await google(settings, refused).send_mail("t", b"x")
    with pytest.raises(OutcomeUnknownError):
        await google(settings, lambda r: httpx.Response(200, text="not json")).send_mail("t", b"x")


async def test_gmail_lookup_of_a_sent_message(settings: Settings) -> None:
    since = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    lookup = SentLookup(
        message_id_header="<lo1@example.org>",
        since=since,
        subject="Réunion",
        recipients=frozenset({"lea@example.org"}),
    )
    queries: list[str] = []

    def by_id(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params.get("q", ""))
        return httpx.Response(200, json={"messages": [{"id": "m9"}]})

    assert await google(settings, by_id).find_sent_mail("t", lookup) == "m9"
    assert queries == ["rfc822msgid:lo1@example.org"]

    def by_headers(request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        if q.startswith("rfc822msgid:"):
            return httpx.Response(200, json={})
        if q.startswith("in:sent"):
            assert q == f"in:sent after:{int(since.timestamp()) - 60}"
            return httpx.Response(200, json={"messages": [{"id": "a"}, {"id": "b"}]})
        assert request.url.params["format"] == "metadata"
        headers = {
            "a": [{"name": "Subject", "value": "Autre"}],
            "b": [
                {"name": "Message-ID", "value": "<CAxyz@mail.gmail.com>"},
                {"name": "Subject", "value": "=?utf-8?q?R=C3=A9union?="},
                {"name": "To", "value": "Léa <Lea@Example.org>"},
            ],
        }[request.url.path.rsplit("/", 1)[-1]]
        return httpx.Response(200, json={"payload": {"headers": headers}})

    assert await google(settings, by_headers).find_sent_mail("t", lookup) == "b"

    def nothing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    assert await google(settings, nothing).find_sent_mail("t", lookup) is None

    def too_many(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("q", "").startswith("in:sent"):
            return httpx.Response(200, json={"messages": [{"id": "a"}], "nextPageToken": "p"})
        return httpx.Response(200, json={})

    with pytest.raises(ConnectorError):
        await google(settings, too_many).find_sent_mail("t", lookup)


def test_gmail_keeps_the_message_id_of_a_read_message() -> None:
    from lifeos.modules.connectors.google import parse_message

    item = {
        "id": "m1",
        "threadId": "t1",
        "internalDate": "1761800000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Message-ID", "value": " <abc.1@ecole.example.org> "},
                {"name": "Subject", "value": "Sortie"},
            ],
            "body": {"data": base64.urlsafe_b64encode(b"Bonjour").decode()},
        },
    }
    parsed = parse_message(item)
    assert parsed is not None and parsed.message_id_header == "<abc.1@ecole.example.org>"
    item["payload"]["headers"][0]["value"] = "<a\r\nBcc: x@evil.example>"  # type: ignore[index]
    reparsed = parse_message(item)
    assert reparsed is not None and reparsed.message_id_header is None
