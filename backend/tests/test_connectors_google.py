"""Google OAuth adapter, against httpx.MockTransport: no request ever reaches Google."""

import base64
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.main import create_app
from lifeos.modules.connectors import build_connectors
from lifeos.modules.connectors.base import (
    AuthorizationRequest,
    ConnectorAuthError,
    ConnectorError,
    ConnectorQuotaError,
    ConnectorUnavailableError,
)
from lifeos.modules.connectors.google import (
    REVOCATION_ENDPOINT,
    TOKEN_ENDPOINT,
    GoogleConnector,
)
from lifeos.modules.connectors.models import ConnectorAccount
from lifeos.modules.llm.fake import FakeProvider

CLIENT_ID = "1234-test.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-test-client-secret"
AUTH = "https://www.googleapis.com/auth/"
Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def google_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "connectors_provider": "google",
            "connectors_key": SecretStr(base64.b64encode(b"g" * 32).decode()),
            "google_client_id": CLIENT_ID,
            "google_client_secret": SecretStr(CLIENT_SECRET),
        }
    )


def google(settings: Settings, handler: Handler) -> GoogleConnector:
    return GoogleConnector(settings, transport=httpx.MockTransport(handler))


def id_token(**overrides: Any) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "109876543210",
        "email": "agenda.demo@example.org",
        "exp": int(time.time()) + 3600,
        **overrides,
    }

    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2lnbmF0dXJl"


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def token_response(**overrides: Any) -> httpx.Response:
    body = {
        "access_token": "ya29.test-access",
        "expires_in": 3599,
        "refresh_token": "1//test-refresh",
        "scope": f"openid {AUTH}userinfo.email {AUTH}calendar.calendarlist.readonly "
        f"{AUTH}calendar.events.readonly",
        "token_type": "Bearer",
        "id_token": id_token(),
        **overrides,
    }
    return httpx.Response(200, json={k: v for k, v in body.items() if v is not None})


def test_the_consent_url_asks_for_minimal_scopes_with_pkce(google_settings: Settings) -> None:
    connector = google(google_settings, lambda r: httpx.Response(500))
    scopes = connector.scopes_for(["calendar.read", "mail.read"])
    assert scopes == (
        "openid",
        f"{AUTH}userinfo.email",
        f"{AUTH}calendar.calendarlist.readonly",
        f"{AUTH}calendar.events.readonly",
        f"{AUTH}gmail.readonly",
    )
    url = connector.authorization_url(
        AuthorizationRequest(
            state="opaque-state",
            code_challenge="challenge",
            redirect_uri="https://lifeos.example.org/connexions/retour",
            scopes=scopes,
        )
    )
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert query == {
        "client_id": CLIENT_ID,
        "redirect_uri": "https://lifeos.example.org/connexions/retour",
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": "opaque-state",
        "code_challenge": "challenge",
        "code_challenge_method": "S256",
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "select_account consent",
    }
    assert CLIENT_SECRET not in url
    incremental = connector.authorization_url(
        AuthorizationRequest(
            state="s",
            code_challenge="c",
            redirect_uri="https://lifeos.example.org/connexions/retour",
            scopes=scopes,
            login_hint="agenda.demo@example.org",
            force_consent=False,
        )
    )
    query = {k: v[0] for k, v in parse_qs(urlsplit(incremental).query).items()}
    assert query["login_hint"] == "agenda.demo@example.org" and "prompt" not in query


def test_capabilities_follow_the_granted_scopes(google_settings: Settings) -> None:
    connector = google(google_settings, lambda r: httpx.Response(500))
    assert connector.capabilities_in(["openid", "email", f"{AUTH}gmail.readonly"]) == {"mail.read"}
    # One of the two calendar reading scopes unticked: no calendar reading.
    assert connector.capabilities_in([f"{AUTH}calendar.events.readonly"]) == frozenset()
    assert connector.capabilities_in(
        [f"{AUTH}calendar.calendarlist.readonly", f"{AUTH}calendar.events.readonly"]
    ) == {"calendar.read"}


async def test_code_exchange_sends_secrets_in_the_body_only(google_settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return token_response()

    tokens = await google(google_settings, handler).exchange_code(
        "4/0Ab-code", code_verifier="verifier-123", redirect_uri="https://h/connexions/retour"
    )
    request = seen[0]
    assert request.method == "POST" and str(request.url) == TOKEN_ENDPOINT
    assert request.url.query == b""
    assert form(request) == {
        "grant_type": "authorization_code",
        "code": "4/0Ab-code",
        "code_verifier": "verifier-123",
        "redirect_uri": "https://h/connexions/retour",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    assert tokens.access_token == "ya29.test-access"
    assert tokens.refresh_token == "1//test-refresh"
    assert tokens.identity is not None and tokens.identity.subject == "109876543210"
    assert tokens.identity.email == "agenda.demo@example.org"
    assert f"{AUTH}userinfo.email" in tokens.scopes
    assert "ya29" not in repr(tokens) and "1//" not in repr(tokens)


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "another-client.apps.googleusercontent.com"},
        {"iss": "https://evil.example.org"},
        {"exp": int(time.time()) - 3600},
        {"sub": ""},
    ],
)
async def test_the_id_token_claims_are_checked(
    google_settings: Settings, claims: dict[str, Any]
) -> None:
    connector = google(google_settings, lambda r: token_response(id_token=id_token(**claims)))
    with pytest.raises(ConnectorError) as error:
        await connector.exchange_code("code", code_verifier="v", redirect_uri="https://h/r")
    assert error.value.reason == "identity_invalid"


async def test_provider_errors_are_mapped_without_their_bodies(google_settings: Settings) -> None:
    def respond(response: httpx.Response) -> GoogleConnector:
        return google(google_settings, lambda r: response)

    revoked = httpx.Response(
        400, json={"error": "invalid_grant", "error_description": "Token has been revoked."}
    )
    with pytest.raises(ConnectorAuthError) as auth_error:
        await respond(revoked).refresh("1//test-refresh")
    assert str(auth_error.value) == "invalid_grant"

    with pytest.raises(ConnectorQuotaError) as quota:
        await respond(httpx.Response(429, headers={"Retry-After": "30"})).refresh("1//x")
    assert quota.value.retry_after == 30 and quota.value.retryable

    with pytest.raises(ConnectorUnavailableError) as unavailable:
        await respond(httpx.Response(503, text="<html>backend error</html>")).refresh("1//x")
    assert unavailable.value.retryable and "html" not in str(unavailable.value)

    # A misconfigured client is not the account's fault: no reconnection asked.
    misconfigured = httpx.Response(401, json={"error": "invalid_client"})
    with pytest.raises(ConnectorError) as error:
        await respond(misconfigured).refresh("1//x")
    assert not isinstance(error.value, ConnectorAuthError)
    assert error.value.reason == "invalid_client"

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(ConnectorUnavailableError) as timed_out:
        await google(google_settings, timeout).refresh("1//x")
    assert timed_out.value.reason == "timeout" and timed_out.value.__cause__ is None


async def test_revocation_puts_the_token_in_the_body(google_settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    await google(google_settings, handler).revoke("1//test-refresh")
    assert str(seen[0].url) == REVOCATION_ENDPOINT and seen[0].url.query == b""
    assert form(seen[0]) == {"token": "1//test-refresh"}

    # Already revoked or expired: nothing left to do.
    already = httpx.Response(400, json={"error": "invalid_token"})
    await google(google_settings, lambda r: already).revoke("1//x")
    with pytest.raises(ConnectorUnavailableError):
        await google(google_settings, lambda r: httpx.Response(500)).revoke("1//x")


def test_only_configured_providers_are_built(google_settings: Settings) -> None:
    assert list(build_connectors(google_settings)) == ["google"]
    disabled = google_settings.model_copy(update={"connectors_provider": "disabled"})
    assert build_connectors(disabled) == {}
    missing = google_settings.model_copy(update={"google_client_secret": SecretStr("")})
    with pytest.raises(ValueError, match="client id and secret"):
        GoogleConnector(missing)


# --- The whole flow through the API, with the real adapter -------------------------------


class FakeGoogle:
    """Token endpoint double: checks PKCE (S256 of the verifier against the challenge of
    the consent URL) and the redirect URI, like Google does."""

    def __init__(self) -> None:
        self.challenges: dict[str, str] = {}
        self.redirect_uri = ""
        self.revoked: list[str] = []

    def consent(self, url: str) -> str:
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert query["code_challenge_method"] == "S256"
        self.redirect_uri = query["redirect_uri"]
        code = f"4/0A{uuid.uuid4().hex}"
        self.challenges[code] = query["code_challenge"]
        return code

    def __call__(self, request: httpx.Request) -> httpx.Response:
        data = form(request)
        if str(request.url) == REVOCATION_ENDPOINT:
            self.revoked.append(data["token"])
            return httpx.Response(200)
        assert data["client_secret"] == CLIENT_SECRET
        challenge = self.challenges.pop(data["code"], None)
        digest = hashlib.sha256(data["code_verifier"].encode()).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if challenge != computed or data["redirect_uri"] != self.redirect_uri:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return token_response()


@pytest.fixture
def fake_google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
async def app(
    google_settings: Settings, fake_llm: FakeProvider, fake_google: FakeGoogle
) -> AsyncIterator[FastAPI]:
    application = create_app(google_settings)
    application.state.llm = fake_llm
    application.state.connectors = {"google": google(google_settings, fake_google)}
    async with application.router.lifespan_context(application):
        yield application


async def test_connecting_a_google_account_end_to_end(
    client: AsyncClient, spaces: dict[str, str], db: AsyncSession, fake_google: FakeGoogle
) -> None:
    response = await client.post(
        "/v1/connectors/oauth/start",
        json={
            "provider": "google",
            "space_id": spaces["personal"],
            "capabilities": ["calendar.read"],
        },
    )
    assert response.status_code == 200, response.text
    url = response.json()["authorization_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    code = fake_google.consent(url)
    assert fake_google.redirect_uri == "http://localhost:3000/connexions/retour"

    result = await client.post("/v1/connectors/oauth/callback", json={"state": state, "code": code})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["granted"] == ["calendar.read"] and body["missing"] == []
    assert body["account"]["email"] == "agenda.demo@example.org"
    account = await db.scalar(select(ConnectorAccount))
    assert account is not None and account.subject == "109876543210"
    assert b"ya29" not in (account.access_token_enc or b"")
    assert "ya29" not in result.text and "1//" not in result.text

    response = await client.delete(f"/v1/connectors/accounts/{account.id}")
    assert response.json()["remote_revocation"] == "done"
    assert fake_google.revoked == ["1//test-refresh"]
