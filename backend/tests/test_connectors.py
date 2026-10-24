"""Connected accounts (task 13, ADR 0012): consent, sealed tokens, refresh, disconnection.

Acceptance criteria: the owner knows which sources are authorised (and where);
disconnecting cuts every access; no token leaks into logs, audit, API responses or
exports. Every test uses the in-memory FakeConnector: no network.
"""

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core import crypto
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.logging import REDACTED, redact_secrets, scrub
from lifeos.main import create_app
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.connectors import service, tokens
from lifeos.modules.connectors.base import ConnectorAuthError, ConnectorUnavailableError
from lifeos.modules.connectors.fake import FakeConnector
from lifeos.modules.connectors.models import (
    ConnectorAccount,
    ConnectorGrant,
    ConnectorSource,
    OAuthState,
)
from lifeos.modules.guardian.models import AgentPermission, Approval
from lifeos.modules.guardian.principal import agent_principal
from lifeos.modules.identity.models import User, UserSession
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.notifications.models import Notification

from .conftest import Owner, make_ctx

CONNECTORS_KEY = base64.b64encode(b"k" * 32).decode()


@pytest.fixture
def fake_connector() -> FakeConnector:
    return FakeConnector()


@pytest.fixture
def connector_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"connectors_provider": "fake", "connectors_key": SecretStr(CONNECTORS_KEY)}
    )


@pytest.fixture
async def app(
    connector_settings: Settings, fake_llm: FakeProvider, fake_connector: FakeConnector
) -> AsyncIterator[FastAPI]:
    application = create_app(connector_settings)
    application.state.llm = fake_llm
    application.state.connectors = {"fake": fake_connector}
    async with application.router.lifespan_context(application):
        yield application


async def start(
    client: AsyncClient,
    space_id: str,
    capabilities: list[str],
    account_id: str | None = None,
    *,
    reconnect: bool = False,
) -> dict[str, str]:
    body: dict[str, Any] = {"provider": "fake", "space_id": space_id, "capabilities": capabilities}
    if account_id:
        body["account_id"] = account_id
    if reconnect:
        body["reconnect"] = True
    response = await client.post("/v1/connectors/oauth/start", json=body)
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    return {"state": query["state"][0], "code": query["code"][0]}


async def connect(
    client: AsyncClient,
    space_id: str,
    capabilities: list[str],
    account_id: str | None = None,
    *,
    reconnect: bool = False,
) -> dict[str, Any]:
    params = await start(client, space_id, capabilities, account_id, reconnect=reconnect)
    response = await client.post("/v1/connectors/oauth/callback", json=params)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def account_row(db: AsyncSession, account_id: str) -> ConnectorAccount:
    db.expire_all()
    account = await db.get(ConnectorAccount, uuid.UUID(account_id))
    assert account is not None
    return account


async def expire_access_token(db: AsyncSession, account_id: str) -> None:
    await db.execute(
        update(ConnectorAccount)
        .where(ConnectorAccount.id == uuid.UUID(account_id))
        .values(access_expires_at=utcnow() - timedelta(minutes=1))
    )
    await db.commit()


async def make_old_login(db: AsyncSession) -> None:
    await db.execute(update(UserSession).values(authenticated_at=utcnow() - timedelta(hours=1)))
    await db.commit()


# --- Connecting ----------------------------------------------------------------------------


async def test_connecting_needs_a_recent_passkey_login(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    await make_old_login(db)
    response = await client.post(
        "/v1/connectors/oauth/start",
        json={"provider": "fake", "space_id": spaces["personal"], "capabilities": ["mail.read"]},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "reauthentication_required"
    denied = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "connector.connect", AuditEvent.outcome == "denied"
        )
    )
    assert denied is not None and denied.details == {"reason": "reauthentication_required"}
    assert await db.scalar(select(func.count()).select_from(OAuthState)) == 0


async def test_the_owner_sees_what_the_provider_actually_granted(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    fake_connector: FakeConnector,
    connector_settings: Settings,
) -> None:
    # The owner unticks Gmail on the provider's consent page.
    fake_connector.decline = {"mail.read"}
    result = await connect(client, spaces["personal"], ["calendar.read", "mail.read"])
    assert result["requested"] == ["calendar.read", "mail.read"]
    assert result["granted"] == ["calendar.read"]
    assert result["missing"] == ["mail.read"]

    consent = fake_connector.requests[0]
    assert consent.redirect_uri == "http://localhost:3000/connexions/retour"
    assert consent.force_consent and consent.login_hint is None
    assert consent.scopes == ("fake/identity", "fake/calendar.read", "fake/mail.read")

    accounts = (await client.get("/v1/connectors/accounts")).json()
    assert len(accounts) == 1
    account = accounts[0]
    assert account["provider"] == "fake" and account["status"] == "active"
    assert account["email"] == "agenda.demo@example.org"
    assert account["capabilities"] == ["calendar.read"]
    assert [(g["space_id"], g["capability"]) for g in account["grants"]] == [
        (spaces["personal"], "calendar.read")
    ]
    assert account["sources"] == []
    assert "refresh_token_enc" not in account and "scopes" not in account

    row = await account_row(db, account["id"])
    assert row.scopes == ["fake/calendar.read", "fake/identity"]
    assert row.refresh_token_enc is not None and row.access_token_enc is not None
    ring = tokens.keyring(connector_settings)
    refresh = tokens.open_token(ring, row.id, "refresh", row.refresh_token_enc)
    assert refresh.startswith("fake-refresh-") and refresh.encode() not in row.refresh_token_enc

    providers = (await client.get("/v1/connectors/providers")).json()
    assert providers == [
        {
            "name": "fake",
            "label": "Démo",
            "capabilities": ["calendar.read", "calendar.write", "mail.read", "mail.send"],
        }
    ]


async def test_incremental_consent_adds_a_space_without_a_new_account(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, fake_connector: FakeConnector
) -> None:
    first = await connect(client, spaces["personal"], ["calendar.read"])
    account_id = first["account"]["id"]
    # Writing the calendar implies reading it.
    second = await connect(client, spaces["professional"], ["calendar.write"], account_id)
    assert second["account"]["id"] == account_id
    assert second["granted"] == ["calendar.read", "calendar.write"]
    hint = fake_connector.requests[1]
    assert hint.login_hint == "agenda.demo@example.org" and not hint.force_consent
    grants = {(g["space_id"], g["capability"]) for g in second["account"]["grants"]}
    assert grants == {
        (spaces["personal"], "calendar.read"),
        (spaces["professional"], "calendar.read"),
        (spaces["professional"], "calendar.write"),
    }
    assert await db.scalar(select(func.count()).select_from(ConnectorAccount)) == 1


async def test_the_state_is_single_use_short_lived_and_the_owners(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, fake_connector: FakeConnector
) -> None:
    params = await start(client, spaces["personal"], ["calendar.read"])
    assert (await client.post("/v1/connectors/oauth/callback", json=params)).status_code == 200
    replay = await client.post("/v1/connectors/oauth/callback", json=params)
    assert replay.status_code == 400 and replay.json()["code"] == "oauth_state_invalid"

    unknown = await client.post(
        "/v1/connectors/oauth/callback", json={"state": "forged", "code": "fake-code-x"}
    )
    assert unknown.json()["code"] == "oauth_state_invalid"

    expired = await start(client, spaces["personal"], ["calendar.read"])
    await db.execute(update(OAuthState).values(expires_at=utcnow() - timedelta(seconds=1)))
    await db.commit()
    response = await client.post("/v1/connectors/oauth/callback", json=expired)
    assert response.json()["code"] == "oauth_state_invalid"

    # A state started by someone else (login CSRF) is refused.
    other = User(email="autre@example.org", display_name="Autre", role="owner")
    db.add(other)
    await db.flush()
    stolen = await start(client, spaces["personal"], ["calendar.read"])
    await db.execute(
        update(OAuthState).where(OAuthState.used_at.is_(None)).values(user_id=other.id)
    )
    await db.commit()
    response = await client.post("/v1/connectors/oauth/callback", json=stolen)
    assert response.json()["code"] == "oauth_state_invalid"

    refused = await db.scalar(
        select(func.count()).where(
            AuditEvent.action == "connector.connect", AuditEvent.outcome == "denied"
        )
    )
    assert refused == 4


async def test_a_wrong_pkce_verifier_or_a_refusal_connects_nothing(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, fake_connector: FakeConnector
) -> None:
    params = await start(client, spaces["personal"], ["calendar.read"])
    # Another verifier than the one of the challenge: the provider refuses the code.
    other_state = await start(client, spaces["personal"], ["calendar.read"])
    response = await client.post(
        "/v1/connectors/oauth/callback",
        json={"state": other_state["state"], "code": params["code"]},
    )
    assert response.status_code == 400 and response.json()["code"] == "oauth_code_invalid"

    denied = await start(client, spaces["personal"], ["mail.read"])
    response = await client.post(
        "/v1/connectors/oauth/callback", json={"state": denied["state"], "error": "access_denied"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "connector_consent_denied"
    assert response.json()["reason"] == "access_denied"
    assert await db.scalar(select(func.count()).select_from(ConnectorAccount)) == 0
    # Both states are spent: nothing can be retried with them.
    used = await db.scalar(select(func.count()).where(OAuthState.used_at.is_not(None)))
    assert used == 2


async def test_connectors_disabled_or_unconfigured(
    client: AsyncClient, app: FastAPI, spaces: dict[str, str]
) -> None:
    app.state.connectors = {}
    assert (await client.get("/v1/connectors/providers")).json() == []
    response = await client.post(
        "/v1/connectors/oauth/start",
        json={"provider": "google", "space_id": spaces["personal"], "capabilities": ["mail.read"]},
    )
    assert response.status_code == 503 and response.json()["code"] == "connectors_unavailable"
    response = await client.post(
        "/v1/connectors/oauth/start",
        json={"provider": "fake", "space_id": str(uuid.uuid4()), "capabilities": ["mail.read"]},
    )
    assert response.status_code == 404 and response.json()["code"] == "space_not_found"
    response = await client.post(
        "/v1/connectors/oauth/start",
        json={"provider": "fake", "space_id": spaces["personal"], "capabilities": ["drive.read"]},
    )
    assert response.status_code == 422 and "drive.read" not in response.text


def test_the_redirect_uri_is_the_web_return_page() -> None:
    # Missing key or client: refused by build_connectors at the API start (test_compose.py).
    configured = Settings(
        connectors_provider="google",
        connectors_key=SecretStr(CONNECTORS_KEY),
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret=SecretStr("client-secret"),
        public_origin="https://lifeos.example.org/",
    )
    assert configured.oauth_redirect_uri == "https://lifeos.example.org/connexions/retour"


# --- Tokens --------------------------------------------------------------------------------


async def test_tokens_are_sealed_per_account_and_kind(connector_settings: Settings) -> None:
    ring = tokens.keyring(connector_settings)
    account, other = uuid.uuid4(), uuid.uuid4()
    blob = tokens.seal_token(ring, account, "refresh", "1//refresh-value")
    assert b"refresh-value" not in blob
    assert tokens.open_token(ring, account, "refresh", blob) == "1//refresh-value"
    with pytest.raises(crypto.CryptoError):
        tokens.open_token(ring, other, "refresh", blob)  # copied onto another account
    with pytest.raises(crypto.CryptoError):
        tokens.open_token(ring, account, "access", blob)  # moved to the access column


async def test_one_refresh_for_concurrent_callers(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
) -> None:
    account_id = (await connect(client, spaces["personal"], ["calendar.read"]))["account"]["id"]
    target = uuid.UUID(account_id)
    # A valid cached token: no refresh at all.
    cached = await tokens.access_token(database, connector_settings, fake_connector, target)
    assert cached.startswith("fake-access-") and fake_connector.refresh_calls == 0

    await expire_access_token(db, account_id)
    fake_connector.refresh_delay = 0.05
    first, second = await asyncio.gather(
        tokens.access_token(database, connector_settings, fake_connector, target),
        tokens.access_token(database, connector_settings, fake_connector, target),
    )
    assert first == second != cached
    assert fake_connector.refresh_calls == 1
    row = await account_row(db, account_id)
    assert row.access_expires_at is not None and row.access_expires_at > utcnow()


async def test_a_revoked_grant_asks_for_reconnection_once(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
) -> None:
    account_id = (await connect(client, spaces["personal"], ["calendar.read"]))["account"]["id"]
    target = uuid.UUID(account_id)
    await expire_access_token(db, account_id)
    fake_connector.fail_next("refresh", ConnectorAuthError("invalid_grant"))
    with pytest.raises(ConnectorAuthError) as error:
        await tokens.access_token(database, connector_settings, fake_connector, target)
    assert error.value.reason == "invalid_grant"

    row = await account_row(db, account_id)
    assert row.status == "reauth_required" and row.last_error == "invalid_grant"
    assert row.refresh_token_enc is None and row.access_token_enc is None
    # Later calls fail at once, without calling the provider or notifying again.
    with pytest.raises(ConnectorAuthError):
        await tokens.access_token(database, connector_settings, fake_connector, target)
    assert fake_connector.refresh_calls == 1
    notifications = list(await db.scalars(select(Notification)))
    assert len(notifications) == 1 and notifications[0].category == "important"
    assert "example.org" not in (notifications[0].title + (notifications[0].body or ""))
    event = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "connector.reauth_required")
    )
    assert event is not None and event.outcome == "failure"
    assert event.details == {"provider": "fake", "reason": "invalid_grant"}

    account = (await client.get("/v1/connectors/accounts")).json()[0]
    assert account["status"] == "reauth_required" and account["last_error"] == "invalid_grant"

    # Reconnecting the same account: consent asked again, account usable again.
    result = await connect(client, spaces["personal"], [], account_id, reconnect=True)
    assert result["account"]["status"] == "active" and result["reconnected"] is True
    assert fake_connector.requests[-1].force_consent
    assert fake_connector.requests[-1].scopes == ("fake/identity", "fake/calendar.read")
    token = await tokens.access_token(database, connector_settings, fake_connector, target)
    assert token.startswith("fake-access-")


async def test_reconnecting_restores_the_tokens_and_never_adds_a_consent(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
) -> None:
    personal, professional = spaces["personal"], spaces["professional"]
    account_id = (await connect(client, personal, ["calendar.read"]))["account"]["id"]
    await connect(client, professional, ["mail.read"], account_id)
    expected = {(personal, "calendar.read"), (professional, "mail.read")}

    async def active_grants() -> set[tuple[str, str]]:
        rows = await db.execute(
            select(ConnectorGrant.space_id, ConnectorGrant.capability).where(
                ConnectorGrant.revoked_at.is_(None)
            )
        )
        return {(str(space), capability) for space, capability in rows.all()}

    assert await active_grants() == expected
    await expire_access_token(db, account_id)
    fake_connector.fail_next("refresh", ConnectorAuthError("invalid_grant"))
    with pytest.raises(ConnectorAuthError):
        await tokens.access_token(
            database, connector_settings, fake_connector, uuid.UUID(account_id)
        )
    account = (await client.get("/v1/connectors/accounts")).json()[0]
    assert account["status"] == "reauth_required"
    assert account["capabilities"] == ["calendar.read", "mail.read"]

    # What the web sends: the accesses held at the provider, from the current space.
    result = await connect(client, personal, account["capabilities"], account_id, reconnect=True)
    assert result["reconnected"] is True and result["account"]["status"] == "active"
    assert result["granted"] == ["calendar.read", "mail.read"] and result["missing"] == []
    # The personal space still cannot read the mailbox: the consents are unchanged.
    assert await active_grants() == expected
    event = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == "connector.connect")
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    assert event is not None
    assert event.details["reconnect"] is True and event.details["grants_added"] == 0

    # Without any consent left anywhere, a reconnection creates none either.
    await db.execute(update(ConnectorGrant).values(revoked_at=utcnow()))
    await db.commit()
    await connect(client, professional, [], account_id, reconnect=True)
    assert await active_grants() == set()

    # A reconnection names its account.
    response = await client.post(
        "/v1/connectors/oauth/start",
        json={"provider": "fake", "space_id": personal, "reconnect": True},
    )
    assert response.status_code == 422 and response.json()["code"] == "invalid_reconnect"
    response = await client.post(
        "/v1/connectors/oauth/start",
        json={"provider": "fake", "space_id": personal, "capabilities": []},
    )
    assert response.status_code == 422 and response.json()["code"] == "invalid_capabilities"


async def test_another_account_returned_by_the_provider_is_refused(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, fake_connector: FakeConnector
) -> None:
    first = await connect(client, spaces["personal"], ["calendar.read"])
    account_id = first["account"]["id"]
    # On the provider's page, the owner picks another account than the hinted one.
    fake_connector.subject, fake_connector.email = "other-account", "autre@example.org"
    params = await start(client, spaces["professional"], ["mail.read"], account_id)
    issued = len(fake_connector.issued)
    response = await client.post("/v1/connectors/oauth/callback", json=params)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "connector_account_mismatch"
    assert "autre@example.org" not in response.text
    # Nothing created or granted; the tokens received for the other account are revoked.
    assert await db.scalar(select(func.count()).select_from(ConnectorAccount)) == 1
    grants = list(await db.scalars(select(ConnectorGrant)))
    assert [(str(g.space_id), g.capability) for g in grants] == [
        (spaces["personal"], "calendar.read")
    ]
    received_refresh = next(t for t in fake_connector.issued[issued:] if "refresh" in t)
    assert fake_connector.revoked == [received_refresh]
    failure = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "connector.connect", AuditEvent.outcome == "failure"
        )
    )
    assert failure is not None and failure.target_id == account_id
    assert failure.details == {"reason": "account_mismatch", "remote_revocation": "done"}

    # The other account is linked and working too: its grant is left alone (revoking
    # would cut it as well), the reconnection of the first one is still refused.
    other = await connect(client, spaces["professional"], ["mail.read"])
    assert other["account"]["id"] != account_id
    params = await start(client, spaces["personal"], [], account_id, reconnect=True)
    response = await client.post("/v1/connectors/oauth/callback", json=params)
    assert response.json()["code"] == "connector_account_mismatch"
    assert len(fake_connector.revoked) == 1
    accounts = {a["email"]: a for a in (await client.get("/v1/connectors/accounts")).json()}
    assert accounts["autre@example.org"]["status"] == "active"
    assert len(accounts["agenda.demo@example.org"]["grants"]) == 1


async def test_a_lost_connectors_key_only_forces_reconnection(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
) -> None:
    account_id = (await connect(client, spaces["personal"], ["calendar.read"]))["account"]["id"]
    await expire_access_token(db, account_id)
    new_key = connector_settings.model_copy(
        update={"connectors_key": SecretStr(base64.b64encode(b"n" * 32).decode())}
    )
    with pytest.raises(ConnectorAuthError) as error:
        await tokens.access_token(database, new_key, fake_connector, uuid.UUID(account_id))
    assert error.value.reason == "token_unreadable"
    assert (await account_row(db, account_id)).status == "reauth_required"


async def test_a_key_rotation_reseals_the_tokens(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
) -> None:
    account_id = (await connect(client, spaces["personal"], ["calendar.read"]))["account"]["id"]
    await expire_access_token(db, account_id)
    new_key = base64.b64encode(b"n" * 32).decode()
    rotated = connector_settings.model_copy(
        update={"connectors_key": SecretStr(f"2:{new_key},1:{CONNECTORS_KEY}")}
    )
    await tokens.access_token(database, rotated, fake_connector, uuid.UUID(account_id))
    row = await account_row(db, account_id)
    assert row.refresh_token_enc is not None and row.access_token_enc is not None
    assert crypto.key_id_of(row.refresh_token_enc) == 2
    assert crypto.key_id_of(row.access_token_enc) == 2


# --- Consent per space and capability ------------------------------------------------------


async def test_use_requires_the_consent_of_the_space(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    connector_settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
    fake_connector: FakeConnector,
) -> None:
    result = await connect(client, spaces["personal"], ["calendar.read"])
    account_id = uuid.UUID(result["account"]["id"])
    personal, professional = uuid.UUID(spaces["personal"]), uuid.UUID(spaces["professional"])
    connectors = {"fake": fake_connector}
    ctx = await make_ctx(database, db, connector_settings, logged_in.user, fake_llm)

    account = await service.require_capability(
        ctx, connectors, account_id, personal, "calendar.read"
    )
    assert account.id == account_id
    for space, capability in ((professional, "calendar.read"), (personal, "mail.read")):
        with pytest.raises(ProblemError) as error:
            await service.require_capability(ctx, connectors, account_id, space, capability)  # type: ignore[arg-type]
        assert error.value.code == "connector_capability_missing"
    with pytest.raises(ProblemError) as error:
        await service.require_capability(ctx, connectors, account_id, uuid.uuid4(), "calendar.read")
    assert error.value.status == 404

    # An agent also needs its own permission on calendar data in that space.
    agent = await agent_principal(db, logged_in.user.id, "planning")
    agent_ctx = await make_ctx(
        database, db, connector_settings, logged_in.user, fake_llm, principal=agent
    )
    with pytest.raises(ProblemError) as error:
        await service.require_capability(
            agent_ctx, connectors, account_id, personal, "calendar.read"
        )
    assert error.value.status == 404  # the agent sees no space at all
    db.add(
        AgentPermission(
            user_id=logged_in.user.id,
            space_id=personal,
            agent="planning",
            resource="tasks",
            access="read",
            purpose="Planifier",
            created_at=utcnow(),
        )
    )
    await db.commit()
    agent = await agent_principal(db, logged_in.user.id, "planning")
    agent_ctx = await make_ctx(
        database, db, connector_settings, logged_in.user, fake_llm, principal=agent
    )
    with pytest.raises(ProblemError) as error:
        await service.require_capability(
            agent_ctx, connectors, account_id, personal, "calendar.read"
        )
    assert error.value.code == "agent_permission_required"
    db.add(
        AgentPermission(
            user_id=logged_in.user.id,
            space_id=personal,
            agent="planning",
            resource="calendar",
            access="read",
            purpose="Planifier",
            created_at=utcnow(),
        )
    )
    await db.commit()
    agent = await agent_principal(db, logged_in.user.id, "planning")
    agent_ctx = await make_ctx(
        database, db, connector_settings, logged_in.user, fake_llm, principal=agent
    )
    await service.require_capability(agent_ctx, connectors, account_id, personal, "calendar.read")

    # Withdrawing the consent is immediate; an expired consent no longer counts either.
    grant_id = result["account"]["grants"][0]["id"]
    assert (await client.delete(f"/v1/connectors/grants/{grant_id}")).status_code == 204
    assert (await client.delete(f"/v1/connectors/grants/{grant_id}")).status_code == 404
    with pytest.raises(ProblemError):
        await service.require_capability(ctx, connectors, account_id, personal, "calendar.read")
    await connect(client, spaces["personal"], ["calendar.read"], str(account_id))
    await db.execute(
        update(ConnectorGrant)
        .where(ConnectorGrant.revoked_at.is_(None))
        .values(expires_at=utcnow() - timedelta(seconds=1))
    )
    await db.commit()
    with pytest.raises(ProblemError):
        await service.require_capability(ctx, connectors, account_id, personal, "calendar.read")
    denials = await db.scalar(
        select(func.count()).where(
            AuditEvent.action == "connector.use", AuditEvent.outcome == "denied"
        )
    )
    assert denials == 5


async def test_consenting_in_another_space_is_reinforced_and_limited_to_granted_scopes(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    result = await connect(client, spaces["personal"], ["calendar.read"])
    account_id = result["account"]["id"]
    url = f"/v1/connectors/accounts/{account_id}/grants"
    body = {"space_id": spaces["professional"], "capabilities": ["calendar.read"]}

    response = await client.put(url, json={**body, "capabilities": ["mail.read"]})
    assert response.status_code == 409 and response.json()["code"] == "connector_scope_missing"
    assert response.json()["capabilities"] == ["mail.read"]

    await make_old_login(db)
    response = await client.put(url, json=body)
    assert response.status_code == 403
    assert response.json()["code"] == "reauthentication_required"
    # Withdrawing needs no passkey.
    response = await client.put(url, json={"space_id": spaces["personal"], "capabilities": []})
    assert response.status_code == 200 and response.json()["grants"] == []

    await db.execute(update(UserSession).values(authenticated_at=utcnow()))
    await db.commit()
    response = await client.put(url, json=body)
    assert response.status_code == 200
    assert [(g["space_id"], g["capability"]) for g in response.json()["grants"]] == [
        (spaces["professional"], "calendar.read")
    ]
    unknown = await client.put(f"/v1/connectors/accounts/{uuid.uuid4()}/grants", json=body)
    assert unknown.status_code == 404 and unknown.json()["code"] == "connector_not_found"


# --- Disconnection -------------------------------------------------------------------------


async def _source_and_approval(db: AsyncSession, owner: Owner, space: str, account: str) -> None:
    db.add(
        ConnectorSource(
            user_id=owner.user.id,
            space_id=uuid.UUID(space),
            account_id=uuid.UUID(account),
            kind="calendar",
            provider_ref="primary",
            name="Agenda",
        )
    )
    db.add(
        Approval(
            user_id=owner.user.id,
            space_id=uuid.UUID(space),
            action="calendar.update_shared_event",
            params={"account_id": account, "event_id": "e1"},
            preview={},
            level="approval",
            requested_by="agent:planning",
            expires_at=utcnow() + timedelta(days=1),
            created_at=utcnow(),
        )
    )
    await db.commit()


async def test_disconnecting_cuts_every_access(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    connector_settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
    fake_connector: FakeConnector,
) -> None:
    result = await connect(client, spaces["personal"], ["calendar.read", "mail.read"])
    account_id = result["account"]["id"]
    await _source_and_approval(db, logged_in, spaces["personal"], account_id)
    row = await account_row(db, account_id)
    assert row.refresh_token_enc is not None
    refresh = tokens.open_token(
        tokens.keyring(connector_settings), row.id, "refresh", row.refresh_token_enc
    )

    response = await client.delete(f"/v1/connectors/accounts/{account_id}")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "account_id": account_id,
        "remote_revocation": "done",
        "purged": False,
    }
    # Revoked at the provider with the refresh token (it ends the whole grant).
    assert fake_connector.revoked == [refresh]

    row = await account_row(db, account_id)
    assert row.status == "revoked" and row.revoked_at is not None
    assert row.refresh_token_enc is None and row.access_token_enc is None
    assert await db.scalar(select(func.count()).where(ConnectorGrant.revoked_at.is_(None))) == 0
    source = await db.scalar(select(ConnectorSource))
    assert source is not None and not source.enabled  # kept (no purge), disabled
    approval = await db.scalar(select(Approval))
    assert approval is not None and approval.status == "expired"

    with pytest.raises(ConnectorAuthError) as error:
        await tokens.access_token(
            database, connector_settings, fake_connector, uuid.UUID(account_id)
        )
    assert error.value.reason == "revoked"
    ctx = await make_ctx(database, db, connector_settings, logged_in.user, fake_llm)
    with pytest.raises(ProblemError) as problem:
        await service.require_capability(
            ctx,
            {"fake": fake_connector},
            uuid.UUID(account_id),
            uuid.UUID(spaces["personal"]),
            "calendar.read",
        )
    assert problem.value.code == "connector_disconnected"
    start_again = await client.put(
        f"/v1/connectors/accounts/{account_id}/grants",
        json={"space_id": spaces["personal"], "capabilities": ["calendar.read"]},
    )
    assert start_again.json()["code"] == "connector_disconnected"

    account = (await client.get("/v1/connectors/accounts")).json()[0]
    assert account["status"] == "revoked" and account["grants"] == []
    assert account["sources"][0]["enabled"] is False

    # Later, the owner deletes what was kept: account, sources (and their synced rows).
    response = await client.delete(f"/v1/connectors/accounts/{account_id}?purge=true")
    assert response.json()["remote_revocation"] == "not_needed"
    assert response.json()["purged"] is True
    assert await db.scalar(select(func.count()).select_from(ConnectorAccount)) == 0
    assert await db.scalar(select(func.count()).select_from(ConnectorSource)) == 0
    assert (await client.get("/v1/connectors/accounts")).json() == []
    events = list(
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "connector.disconnect")
            .order_by(AuditEvent.id)
        )
    )
    assert [e.details["purge"] for e in events] == [False, True]
    assert events[0].details["grants"] == 2 and events[0].details["approvals"] == 1
    missing = await client.delete(f"/v1/connectors/accounts/{account_id}")
    assert missing.status_code == 404


async def test_disconnecting_an_account_without_tokens_asks_for_a_manual_revocation(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
) -> None:
    account_id = (await connect(client, spaces["personal"], ["calendar.read"]))["account"]["id"]
    # The connectors key was replaced: the token cannot be read, it is wiped locally and
    # the grant stays live at the provider.
    await expire_access_token(db, account_id)
    new_key = connector_settings.model_copy(
        update={"connectors_key": SecretStr(base64.b64encode(b"n" * 32).decode())}
    )
    with pytest.raises(ConnectorAuthError):
        await tokens.access_token(database, new_key, fake_connector, uuid.UUID(account_id))
    assert (await account_row(db, account_id)).refresh_token_enc is None

    response = await client.delete(f"/v1/connectors/accounts/{account_id}")
    assert response.status_code == 200
    assert response.json()["remote_revocation"] == "manual_required"
    assert fake_connector.revoked == []
    failure = await db.scalar(select(AuditEvent).where(AuditEvent.action == "connector.revoke"))
    assert failure is not None and failure.outcome == "failure"
    assert failure.details == {"provider": "fake", "reason": "tokens_wiped"}
    # Already disconnected: nothing more to report when purging.
    response = await client.delete(f"/v1/connectors/accounts/{account_id}?purge=true")
    assert response.json()["remote_revocation"] == "not_needed"


async def test_disconnecting_cancels_the_consents_in_progress(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    account_id = (await connect(client, spaces["personal"], ["calendar.read"]))["account"]["id"]
    # Started before the disconnection: a new connection, and one for this account.
    fresh = await start(client, spaces["personal"], ["calendar.read"])
    bound = await start(client, spaces["professional"], ["mail.read"], account_id)
    response = await client.delete(f"/v1/connectors/accounts/{account_id}")
    assert response.status_code == 200
    assert await db.scalar(select(func.count()).where(OAuthState.used_at.is_(None))) == 0
    for params in (fresh, bound):
        response = await client.post("/v1/connectors/oauth/callback", json=params)
        assert response.json()["code"] == "oauth_state_invalid"
    assert (await account_row(db, account_id)).status == "revoked"


async def test_disconnecting_works_when_the_provider_does_not_answer(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, fake_connector: FakeConnector
) -> None:
    account_id = (await connect(client, spaces["personal"], ["mail.read"]))["account"]["id"]
    fake_connector.fail_next("revoke", ConnectorUnavailableError("timeout"))
    response = await client.delete(f"/v1/connectors/accounts/{account_id}?purge=true")
    assert response.status_code == 200
    assert response.json()["remote_revocation"] == "failed"
    assert await db.scalar(select(func.count()).select_from(ConnectorAccount)) == 0
    failure = await db.scalar(select(AuditEvent).where(AuditEvent.action == "connector.revoke"))
    assert failure is not None and failure.outcome == "failure"
    assert failure.details == {"provider": "fake", "reason": "timeout"}


# --- Retention and export ------------------------------------------------------------------


async def test_old_oauth_states_are_purged(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession
) -> None:
    await start(client, spaces["personal"], ["calendar.read"])
    await start(client, spaces["personal"], ["calendar.read"])
    old = await db.scalar(select(OAuthState.id).limit(1))
    await db.execute(
        update(OAuthState)
        .where(OAuthState.id == old)
        .values(expires_at=utcnow() - timedelta(days=2))
    )
    await db.commit()
    assert await service.purge_oauth_states(db) == 1
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(OAuthState)) == 1
    event = await db.scalar(select(AuditEvent).where(AuditEvent.action == "connector.purge_states"))
    assert event is not None and event.details == {"oauth_states": 1}


async def test_the_export_lists_accounts_and_consents_without_tokens(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    await connect(client, spaces["personal"], ["calendar.read"])
    export = (await client.get("/v1/privacy/export")).json()
    assert len(export["connector_accounts"]) == 1
    account = export["connector_accounts"][0]
    assert account["email"] == "agenda.demo@example.org"
    assert "refresh_token_enc" not in account and "access_token_enc" not in account
    assert [g["capability"] for g in export["connector_grants"]] == ["calendar.read"]
    assert export["connector_sources"] == []


# --- No token anywhere ---------------------------------------------------------------------


def test_log_redaction_masks_credentials() -> None:
    assert scrub("POST /token?code=4/0AbC-dEf&state=xyz ok") == (
        f"POST /token?code={REDACTED}&state={REDACTED} ok"
    )
    assert scrub("Authorization: Bearer ya29.a0AfB_byC-123") == (
        f"Authorization: Bearer {REDACTED}"
    )
    assert scrub("token ya29.a0AfB_byC-123 and 1//0gXyZabcdefghijk") == (
        f"token {REDACTED} and {REDACTED}"
    )
    assert REDACTED in scrub("id eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln")
    assert scrub("{'refresh_token': 'abc'}") == f"{{'refresh_token': '{REDACTED}'}}"
    # Error codes and ordinary fields are kept.
    assert scrub("error_code=llm_quota status_code=429") == "error_code=llm_quota status_code=429"
    event = redact_secrets(
        None, "info", {"event": "x", "client_secret": "s3cr3t", "code": "too_large", "n": 1}
    )
    assert event == {"event": "x", "client_secret": REDACTED, "code": "too_large", "n": 1}


async def test_no_token_leaks_into_logs_audit_responses_or_exports(
    connector_settings: Settings,
    fake_llm: FakeProvider,
    fake_connector: FakeConnector,
    owner: Owner,
    database: Database,
    db: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from .conftest import ORIGIN, enroll

    debug = connector_settings.model_copy(update={"log_level": "DEBUG", "log_format": "json"})
    application = create_app(debug)
    application.state.llm = fake_llm
    application.state.connectors = {"fake": fake_connector}
    responses: list[Response] = []
    async with application.router.lifespan_context(application):
        from httpx import ASGITransport

        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
            headers={"Origin": ORIGIN, "User-Agent": "pytest"},
        ) as client:
            await enroll(client, owner)
            me = (await client.get("/v1/auth/me")).json()
            space = next(s["id"] for s in me["spaces"] if s["kind"] == "personal")
            params = await start(client, space, ["calendar.read", "mail.read"])
            responses.append(await client.post("/v1/connectors/oauth/callback", json=params))
            responses.append(await client.post("/v1/connectors/oauth/callback", json=params))
            account_id = responses[0].json()["account"]["id"]
            target = uuid.UUID(account_id)
            await expire_access_token(db, account_id)
            await tokens.access_token(database, debug, fake_connector, target)
            await expire_access_token(db, account_id)
            fake_connector.fail_next("refresh", ConnectorAuthError("invalid_grant"))
            with pytest.raises(ConnectorAuthError):
                await tokens.access_token(database, debug, fake_connector, target)
            await connect(client, space, ["calendar.read"], account_id)
            responses.append(await client.get("/v1/connectors/accounts"))
            responses.append(await client.get("/v1/privacy/export"))
            responses.append(await client.get("/v1/notifications"))
            responses.append(await client.get("/v1/guardian/audit?limit=200"))
            fake_connector.fail_next("revoke", ConnectorUnavailableError("timeout"))
            responses.append(await client.delete(f"/v1/connectors/accounts/{account_id}"))
            responses.append(await client.get("/v1/guardian/audit?limit=200"))

    logs = capsys.readouterr().out
    assert "connector_reauth_required" in logs  # the logs did capture the flow
    secrets = [*fake_connector.issued, *params.values()]
    assert len(fake_connector.issued) >= 6
    audit_details = json.dumps(
        [e.details for e in await db.scalars(select(AuditEvent))], default=str
    )
    approvals = json.dumps([a.params for a in await db.scalars(select(Approval))])
    bodies = "\n".join(r.text for r in responses)
    for secret in secrets:
        assert secret not in logs
        assert secret not in audit_details
        assert secret not in bodies
        assert secret not in approvals
    # Nor the account's address in the logs or the audit trail.
    assert "agenda.demo@example.org" not in logs
    assert "agenda.demo@example.org" not in audit_details
