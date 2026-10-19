import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.guardian import actions, gateway
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import LLMCall
from lifeos.modules.guardian.policy import evaluate
from lifeos.modules.guardian.principal import Principal, agent_principal
from lifeos.modules.guardian.privacy import mask, ocr_digits
from lifeos.modules.identity.models import Space, User, UserSession
from lifeos.modules.llm.base import LLMMessage, LLMRequest, LLMUnavailableError
from lifeos.modules.llm.fake import FakeProvider

from .conftest import Owner, enable_cloud, make_ctx

SPACE_A = uuid.uuid4()
SPACE_B = uuid.uuid4()
USER = uuid.uuid4()


def _user(**kwargs: Any) -> Principal:
    return Principal(
        kind="user", user_id=USER, actor_id=str(USER), space_ids=frozenset({SPACE_A}), **kwargs
    )


def _agent(*grants: tuple[uuid.UUID, str, str]) -> Principal:
    return Principal(
        kind="agent",
        user_id=USER,
        actor_id="finance",
        agent="finance",
        space_ids=frozenset(g[0] for g in grants),
        grants=frozenset(grants),
    )


class _FakeApproval:
    def __init__(self, action: str, space_id: uuid.UUID, status: str = "approved") -> None:
        self.action = action
        self.space_id = space_id
        self.status = status
        self.user_id = USER


# --- Policy (pure) --------------------------------------------------------------------------


def test_policy_levels() -> None:
    assert evaluate(_user(), "task.create", SPACE_A).allowed
    assert evaluate(_user(), "task.create", SPACE_B).code == "space_forbidden"
    assert evaluate(_user(), "nope", SPACE_A).code == "unknown_action"

    # Approval level: refused without an approved approval for this exact action and space.
    assert evaluate(_user(), "email.send", SPACE_A).code == "approval_required"
    approval: Any = _FakeApproval("email.send", SPACE_A)
    assert evaluate(_user(), "email.send", SPACE_A, approval).allowed
    assert not evaluate(
        _user(), "email.send", SPACE_A, _FakeApproval("document.share", SPACE_A)
    ).allowed  # type: ignore[arg-type]
    assert not evaluate(
        _user(), "email.send", SPACE_A, _FakeApproval("email.send", SPACE_A, "pending")
    ).allowed  # type: ignore[arg-type]

    # Reinforced: recent authentication, never agents.
    assert evaluate(_user(), "permission.grant", SPACE_A).code == "reauthentication_required"
    assert evaluate(_user(recent_auth=True), "permission.grant", SPACE_A).allowed


def test_policy_agents_need_explicit_grants() -> None:
    no_grant = _agent()
    assert evaluate(no_grant, "task.create", SPACE_A).code == "space_forbidden"
    reader = _agent((SPACE_A, "tasks", "read"))
    assert evaluate(reader, "task.create", SPACE_A).code == "no_permission"
    writer = _agent((SPACE_A, "tasks", "write"))
    assert evaluate(writer, "task.create", SPACE_A).allowed
    assert writer.can(SPACE_A, "tasks", "read")  # write implies read
    assert evaluate(writer, "task.delete", SPACE_A).code == "approval_required"
    assert evaluate(writer, "permission.grant", SPACE_A).code == "forbidden_for_agents"
    assert evaluate(writer, "payment.execute", None).code == "forbidden_for_agents"


# --- Privacy engine -------------------------------------------------------------------------


def test_masking() -> None:
    text = (
        "Écris à jean.dupont@example.fr ou appelle le 06 12 34 56 78. "
        "IBAN FR76 3000 6000 0112 3456 7890 189, carte 4111 1111 1111 1111, "
        "NIR 1 85 05 78 006 084 36, mot de passe: hunter2. "
        "Commande 1234 5678 9012 3456 et FR00 1234 ne sont pas sensibles."
    )
    result = mask(text)
    assert "jean.dupont" not in result.text
    assert "06 12 34 56 78" not in result.text
    assert "3000 6000" not in result.text
    assert "4111 1111" not in result.text
    assert "hunter2" not in result.text
    assert "78 006 084" not in result.text
    assert "1234 5678 9012 3456" in result.text  # fails the Luhn check: not a card
    assert result.counts == {
        "EMAIL": 1,
        "TÉLÉPHONE": 1,
        "IBAN": 1,
        "CARTE": 1,
        "NIR": 1,
        "SECRET": 1,
    }


# Identifier variants found in French documents and messages. Synthetic values (see
# tests/fixtures/sensitivity/README.md): IBAN with a valid key, NIR 1 85 05 78 006 084
# with the valid key 91 or the deliberately invalid 36, test card 4111..., phone numbers
# in the ranges ARCEP reserves for fiction.
NBSP, NNBSP = "\u00a0", "\u202f"
IBAN_GROUPS = ("FR76", "3000", "6000", "0112", "3456", "7890", "189")


@pytest.mark.parametrize(
    ("text", "secret", "label"),
    [
        (f"IBAN {NBSP.join(IBAN_GROUPS)}", "6000", "IBAN"),
        (f"IBAN {NNBSP.join(IBAN_GROUPS)}", "6000", "IBAN"),
        (f"IBAN {'-'.join(IBAN_GROUPS)}", "6000", "IBAN"),
        (f"IBAN {'.'.join(IBAN_GROUPS)}", "6000", "IBAN"),
        (f"iban {' '.join(IBAN_GROUPS).lower()}", "6000", "IBAN"),
        ("IBAN FR76 3OOO 6OOO Ol12 3456 789O l89", "3456", "IBAN"),  # OCR: O/0, l/1
        (f"IBAN {''.join(IBAN_GROUPS)}", "3000600001", "IBAN"),
        ("NIR 1.85.05.78.006.084.91", "78.006", "NIR"),
        ("NIR 1-85-05-78-006-084-36", "78-006", "NIR"),
        (f"NIR 1{NBSP}85{NBSP}05{NBSP}78{NBSP}006{NBSP}084{NBSP}91", "006", "NIR"),
        ("NIR 1 85 O5 78 OO6 O84 91", "78 OO6", "NIR"),  # OCR
        ("carte 4111.1111.1111.1111", "4111", "CARTE"),
        (f"carte 4111{NBSP}1111{NBSP}1111{NBSP}1111", "4111", "CARTE"),
        ("Tél. 0033 6 39 98 12 34", "39 98", "TÉLÉPHONE"),
        ("Tél. +33 (0)6 39 98 12 34", "39 98", "TÉLÉPHONE"),
        ("Tél. (+33) 6 39 98 12 34", "39 98", "TÉLÉPHONE"),
        (f"Tél. 06{NBSP}39{NBSP}98{NBSP}12{NBSP}34", "98", "TÉLÉPHONE"),
        ("Tél. O6 39 98 12 34", "39 98", "TÉLÉPHONE"),  # OCR
        ("Call +1 415 555 0100", "555", "TÉLÉPHONE"),
        ("Écrire à jean . dupont @ example . org", "dupont", "EMAIL"),
        ("Écrire à jean.dupont(at)example.org", "dupont", "EMAIL"),
        ("Écrire à jean.dupont [at] example [dot] org", "dupont", "EMAIL"),
        ("Écrire à jean.dupont arobase example.org", "dupont", "EMAIL"),
        ("JEAN.DUPONT@EXAMPLE.ORG", "DUPONT", "EMAIL"),
        ("mot de passe hunter2", "hunter2", "SECRET"),
        ("mon mot de passe est hunter2.", "hunter2", "SECRET"),
        ("code PIN : 1234", "1234", "SECRET"),
        ("code PIN 1234", "1234", "SECRET"),
        ("api_key=sk-test-123", "sk-test", "SECRET"),
        ("Passeport n° 99XX99999", "99XX99999", "PASSEPORT"),
        ("CARTE NATIONALE D'IDENTITÉ N° : 990000000001", "990000000001", "IDENTITÉ"),
        ("Carte d'identité n° Z9ZZ99ZZ9", "Z9ZZ99ZZ9", "IDENTITÉ"),
        ("Titre de séjour\nN° 9900000001", "9900000001", "IDENTITÉ"),
        ("Numéro fiscal : 30 99 999 999 999", "999 999", "FISCAL"),
    ],
)
def test_masking_variants(text: str, secret: str, label: str) -> None:
    result = mask(text)
    assert secret not in result.text
    assert result.counts == {label: 1}
    assert f"[{label}]" in result.text


@pytest.mark.parametrize(
    "text",
    [
        "SIRET 123 456 782 00002",  # passes the Luhn check, but announced as a SIRET
        "Commande 1234 5678 9012 3456",  # fails the Luhn check
        "IBAN FR76 3000 6000 0112 3456 7890 188",  # wrong key
        "Montant : 1 234 567,89 €",
        f"Total{NNBSP}:{NBSP}12{NNBSP}345,00{NBSP}€",
        "Rendez-vous le 05/10/2026 à 10h30",
        "Prix 12.50 € le 12.03.2024",
        "mot de passe oublié.",
        "mot de passe l'ancien",
        "Tweet @jean. Bonjour",
        "J'ai renouvelé mon passeport hier, ça a coûté 86 euros.",
        "RÉPUBLIQUE FRANÇAISE — CARTE NATIONALE D'IDENTITÉ\nREPUBL1QUE FRANCA1SE",
    ],
)
def test_masking_leaves_ordinary_text(text: str) -> None:
    result = mask(text)
    assert result.text == text
    assert result.total == 0


def test_masking_accented_addresses() -> None:
    # Better masked than sent: some providers accept accented local parts.
    assert mask("Écrire à élodie.prénom@exemple.fr demain.").text == "Écrire à [EMAIL] demain."


@pytest.mark.parametrize("chunk", ["a . ", "a.", "1 ", "x (at) ", "O1lI|", "+33 (0)6 12 "])
def test_masking_and_classifying_stay_linear(chunk: str) -> None:
    # Every cloud request and every document goes through these regexes: a long run of a
    # near-match (« a . a . a . … ») must not be rescanned from every position.
    from lifeos.modules.documents.sensitivity import classify

    text = chunk * (100_000 // len(chunk))
    started = time.perf_counter()
    mask(text)
    classify(text)
    assert time.perf_counter() - started < 3


def test_masking_replaces_only_identifiers() -> None:
    text = f"Bonjour{NNBSP}! IBAN{NBSP}: {NBSP.join(IBAN_GROUPS)}, merci{NNBSP}; à bientôt."
    result = mask(text)
    # Typography around the identifier is sent as written.
    assert result.text == f"Bonjour{NNBSP}! IBAN{NBSP}: [IBAN], merci{NNBSP}; à bientôt."
    assert result.kinds() == {"iban": 1}
    assert ocr_digits("3OOO l2 Il") == "3000 12 Il"  # one for one: positions are kept


# --- Actions, approvals and audit (acceptance criteria) -------------------------------------


class EmailParams(BaseModel):
    to: str
    subject: str
    body: str


@pytest.fixture
def email_action() -> Any:
    sent: list[EmailParams] = []

    async def handler(ctx: Ctx, space_id: uuid.UUID, params: EmailParams) -> dict[str, Any]:
        sent.append(params)
        return {"sent": True}

    actions.register(
        "email.send", EmailParams, handler, lambda p: {"to": p.to, "subject": p.subject}
    )
    yield sent
    actions.REGISTRY.pop("email.send", None)


async def _owner_ctx(
    database: Database, db: AsyncSession, settings: Settings, owner: Owner, llm: FakeProvider
) -> Ctx:
    user = await db.get(User, owner.user.id)
    assert user is not None
    return await make_ctx(database, db, settings, user, llm)


async def test_email_without_confirmation_is_blocked_and_audited(
    database: Database,
    db: AsyncSession,
    settings: Settings,
    owner: Owner,
    fake_llm: FakeProvider,
    email_action: list[EmailParams],
) -> None:
    ctx = await _owner_ctx(database, db, settings, owner, fake_llm)
    space = next(iter(ctx.principal.space_ids))
    params = {"to": "x@example.org", "subject": "Bonjour", "body": "..."}

    with pytest.raises(ProblemError) as exc:
        await actions.execute(ctx, "email.send", space, params)
    assert exc.value.code == "approval_required"
    assert email_action == []
    await db.rollback()
    denied = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "email.send", AuditEvent.outcome == "denied")
    )
    assert denied is not None and denied.details == {"reason": "approval_required"}

    # Same action through an approval: exact preview, explicit consent, then execution.
    approval = await actions.request_approval(
        ctx, "email.send", space, EmailParams(**params), requested_by="agent:core"
    )
    assert approval.preview == {"to": "x@example.org", "subject": "Bonjour"}
    assert email_action == []
    approved = await actions.approve(ctx, approval.id)
    assert approved.status == "executed"
    assert approved.result == {"sent": True}
    assert email_action[0].to == "x@example.org"

    # An approval is single use.
    with pytest.raises(ProblemError) as exc:
        await actions.approve(ctx, approval.id)
    assert exc.value.code == "approval_not_pending"


async def test_rejected_and_expired_approvals_never_run(
    database: Database,
    db: AsyncSession,
    settings: Settings,
    owner: Owner,
    fake_llm: FakeProvider,
    email_action: list[EmailParams],
) -> None:
    ctx = await _owner_ctx(database, db, settings, owner, fake_llm)
    space = next(iter(ctx.principal.space_ids))
    params = EmailParams(to="a@b.fr", subject="s", body="b")
    first = await actions.request_approval(ctx, "email.send", space, params, requested_by="t")
    assert (await actions.reject(ctx, first.id)).status == "rejected"
    second = await actions.request_approval(ctx, "email.send", space, params, requested_by="t")
    second.expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(ProblemError):
        await actions.approve(ctx, second.id)
    assert email_action == []


async def test_finance_agent_cannot_read_professional_data_without_permission(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    logged_in: Owner,
) -> None:
    pro = uuid.UUID(spaces["professional"])
    agent = await agent_principal(db, logged_in.user.id, "finance")
    assert not agent.can(pro, "journal", "read")
    assert not evaluate(agent, "fact.write", pro).allowed

    granted = await client.post(
        "/v1/guardian/permissions",
        json={
            "agent": "finance",
            "space_id": str(pro),
            "resource": "journal",
            "access": "read",
            "purpose": "Préparer le bilan des frais professionnels",
        },
    )
    assert granted.status_code == 201, granted.text
    agent = await agent_principal(db, logged_in.user.id, "finance")
    assert agent.can(pro, "journal", "read")
    assert not agent.can(pro, "facts", "read")
    assert not agent.can(uuid.UUID(spaces["personal"]), "journal", "read")

    # Immediate withdrawal.
    assert (
        await client.delete(f"/v1/guardian/permissions/{granted.json()['id']}")
    ).status_code == 204
    agent = await agent_principal(db, logged_in.user.id, "finance")
    assert not agent.can(pro, "journal", "read")


async def test_granting_a_permission_requires_recent_login(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    await db.execute(update(UserSession).values(authenticated_at=utcnow() - timedelta(hours=1)))
    await db.commit()
    response = await client.post(
        "/v1/guardian/permissions",
        json={
            "agent": "coaching",
            "space_id": spaces["personal"],
            "resource": "journal",
            "purpose": "Bilans hebdomadaires",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "reauthentication_required"


async def test_expired_permission_is_ignored(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, logged_in: Owner
) -> None:
    response = await client.post(
        "/v1/guardian/permissions",
        json={
            "agent": "planning",
            "space_id": spaces["personal"],
            "resource": "tasks",
            "access": "write",
            "purpose": "Planifier la semaine",
            "expires_at": (utcnow() + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 201
    from lifeos.modules.guardian.models import AgentPermission

    await db.execute(update(AgentPermission).values(expires_at=utcnow() - timedelta(seconds=1)))
    await db.commit()
    agent = await agent_principal(db, logged_in.user.id, "planning")
    assert agent.grants == frozenset()


# --- Cloud gateway --------------------------------------------------------------------------


def _req(text: str) -> LLMRequest:
    return LLMRequest(
        purpose="test", system="Contexte", messages=[LLMMessage(role="user", text=text)]
    )


async def test_gateway_requires_consent_and_meters_without_content(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    db: AsyncSession,
    settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    ctx = await _owner_ctx(database, db, settings, logged_in, fake_llm)
    space = uuid.UUID(spaces["personal"])
    with pytest.raises(ProblemError) as exc:
        await gateway.complete(ctx, space_id=space, request=_req("Bonjour"))
    assert exc.value.code == "cloud_consent_required"
    assert fake_llm.requests == []  # nothing left the server

    await enable_cloud(client, spaces["personal"])
    # The consent was changed by another request (another session): reload the space.
    cached = await db.get(Space, space)
    assert cached is not None
    await db.refresh(cached)
    response = await gateway.complete(
        ctx, space_id=space, request=_req("Mon mail est moi@example.org")
    )
    assert response.text.startswith("[fake]")
    sent = fake_llm.requests[-1]
    assert "moi@example.org" not in sent.messages[0].text
    assert "[EMAIL]" in sent.messages[0].text

    calls = list(await db.scalars(select(LLMCall).order_by(LLMCall.created_at)))
    assert [c.outcome for c in calls] == ["denied", "ok"]
    assert calls[1].redactions == 1 and calls[1].input_tokens > 0
    columns = {c.key for c in LLMCall.__table__.columns}
    assert not columns & {"prompt", "content", "text", "response"}


async def test_gateway_budget_and_unavailability(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    db: AsyncSession,
    settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
) -> None:
    await enable_cloud(client, spaces["personal"])
    ctx = await _owner_ctx(database, db, settings, logged_in, fake_llm)
    space = uuid.UUID(spaces["personal"])

    fake_llm.push(LLMUnavailableError("down"))
    with pytest.raises(ProblemError) as exc:
        await gateway.complete(ctx, space_id=space, request=_req("Bonjour"))
    assert exc.value.status == 503 and exc.value.code == "llm_unavailable"

    await gateway.complete(ctx, space_id=space, request=_req("Bonjour"))
    ctx.user.llm_monthly_token_budget = 1
    with pytest.raises(ProblemError) as exc:
        await gateway.complete(ctx, space_id=space, request=_req("Encore"))
    assert exc.value.code == "llm_budget_exceeded"
    outcomes = list(await db.scalars(select(LLMCall.outcome).order_by(LLMCall.created_at)))
    assert outcomes == ["error", "ok", "budget_exceeded"]


async def test_cloud_consent_change_is_reinforced(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    await db.execute(update(UserSession).values(authenticated_at=utcnow() - timedelta(hours=1)))
    await db.commit()
    response = await client.patch(
        f"/v1/spaces/{spaces['personal']}", json={"cloud_ai_enabled": True}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "reauthentication_required"


async def test_approvals_api_and_audit_log(client: AsyncClient, spaces: dict[str, str]) -> None:
    assert (await client.get("/v1/approvals?status=pending")).json() == []
    actions_list = (await client.get("/v1/guardian/actions")).json()
    email = next(a for a in actions_list if a["name"] == "email.send")
    assert email["user_level"] == "approval"
    audit = (await client.get("/v1/guardian/audit?limit=5")).json()
    assert audit["items"][0]["action"] == "auth.passkey.register"
    usage = (await client.get("/v1/guardian/llm-usage")).json()
    assert usage["provider"] == "fake" and usage["month_tokens_used"] == 0


async def test_maintenance_jobs(
    database: Database,
    db: AsyncSession,
    settings: Settings,
    owner: Owner,
    fake_llm: FakeProvider,
    email_action: list[EmailParams],
) -> None:
    from lifeos.core.idempotency import IdempotencyKey, purge_expired_keys

    ctx = await _owner_ctx(database, db, settings, owner, fake_llm)
    space = next(iter(ctx.principal.space_ids))
    approval = await actions.request_approval(
        ctx,
        "email.send",
        space,
        EmailParams(to="a@b.fr", subject="s", body="b"),
        requested_by="t",
    )
    approval.expires_at = utcnow() - timedelta(minutes=1)
    db.add(
        IdempotencyKey(
            user_id=owner.user.id,
            key="k",
            method="POST",
            path="/v1/tasks",
            request_hash="h",
            created_at=utcnow() - timedelta(days=2),
            expires_at=utcnow() - timedelta(days=1),
        )
    )
    await db.flush()
    assert await actions.expire_stale(db) == 1
    assert await purge_expired_keys(db) == 1
    await db.refresh(approval)
    assert approval.status == "expired"
