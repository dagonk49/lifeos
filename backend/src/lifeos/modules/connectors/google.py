"""Google OAuth 2.0 for Google Calendar and Gmail: authorization code flow with PKCE, from
a confidential "Web application" client (ADR 0012).

Only the hosts of the allow-list are called (threat T18), with explicit timeouts and no
redirects. Codes and tokens travel in POST bodies, never in URLs (proxies and error
messages show URLs). Response bodies are never logged nor copied into errors: at most
Google's `error` code, when it is a plain identifier.

Scope names and their classification (sensitive, restricted) must be checked against
Google's current documentation before going live (docs/guide-google.md).
"""

import base64
import binascii
import json
import re
import time
from collections.abc import Iterable
from dataclasses import replace
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.modules.connectors.base import (
    CAPABILITIES,
    AccountIdentity,
    AuthorizationRequest,
    Capability,
    ConnectorAuthError,
    ConnectorError,
    ConnectorQuotaError,
    ConnectorUnavailableError,
    TokenSet,
)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105  (URL, not a secret)
REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
# Hosts LifeOS may call for this provider (the calendar and mail tasks add theirs).
ALLOWED_HOSTS = frozenset({"oauth2.googleapis.com"})
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

_AUTH = "https://www.googleapis.com/auth/"
# Who the account is: OpenID subject and address (shown to the owner).
IDENTITY_SCOPES = ("openid", _AUTH + "userinfo.email")
# Minimal scopes per capability. Calendar reading uses the granular scopes (list of
# calendars, events) rather than the broad calendar.readonly; gmail.readonly is a
# "restricted" scope (gmail.metadata cannot search).
SCOPES: dict[Capability, tuple[str, ...]] = {
    "calendar.read": (_AUTH + "calendar.calendarlist.readonly", _AUTH + "calendar.events.readonly"),
    "calendar.write": (_AUTH + "calendar.events",),
    "mail.read": (_AUTH + "gmail.readonly",),
    "mail.send": (_AUTH + "gmail.send",),
}
# Short names Google may return instead of the full scope.
_ALIASES = {"email": _AUTH + "userinfo.email", "profile": _AUTH + "userinfo.profile"}
_ERROR_CODE = re.compile(r"^[a-z_]{1,64}$")
# Errors of the token endpoint that mean "this grant is gone": connect again.
_AUTH_ERRORS = frozenset({"invalid_grant"})


def _normalize(scopes: Iterable[str]) -> frozenset[str]:
    return frozenset(_ALIASES.get(scope, scope) for scope in scopes)


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) and _ERROR_CODE.match(error) else None


def _failure(response: httpx.Response) -> ConnectorError:
    status = response.status_code
    error = _error_code(response)
    if error in _AUTH_ERRORS:
        return ConnectorAuthError(error or "invalid_grant")
    if status == 429:
        retry_after = response.headers.get("retry-after", "")
        return ConnectorQuotaError(
            "rate_limited", retry_after=int(retry_after) if retry_after.isdigit() else None
        )
    if status >= 500:
        return ConnectorUnavailableError(f"http_{status}")
    return ConnectorError(error or f"http_{status}")


class GoogleConnector:
    name = "google"
    label = "Google"

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        secret = settings.google_client_secret
        if not settings.google_client_id or secret is None or not secret.get_secret_value():
            raise ValueError("Google OAuth client id and secret are required")
        self._client_id = settings.google_client_id
        self._client_secret = secret
        self._timeout = settings.connectors_timeout_seconds
        # Tests inject an httpx.MockTransport: no request ever reaches Google.
        self._transport = transport

    # --- Scopes ------------------------------------------------------------------------------

    def scopes_for(self, capabilities: Iterable[Capability]) -> tuple[str, ...]:
        wanted = set(capabilities)
        scopes = list(IDENTITY_SCOPES)
        for capability in CAPABILITIES:
            if capability in wanted:
                scopes.extend(s for s in SCOPES[capability] if s not in scopes)
        return tuple(scopes)

    def capabilities_in(self, scopes: Iterable[str]) -> frozenset[Capability]:
        granted = _normalize(scopes)
        return frozenset(c for c in CAPABILITIES if set(SCOPES[c]) <= granted)

    # --- Flow --------------------------------------------------------------------------------

    def authorization_url(self, request: AuthorizationRequest) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": request.redirect_uri,
            "response_type": "code",
            "scope": " ".join(request.scopes),
            "state": request.state,
            "code_challenge": request.code_challenge,
            "code_challenge_method": "S256",
            # A refresh token, and incremental consent: earlier grants are kept.
            "access_type": "offline",
            "include_granted_scopes": "true",
        }
        if request.login_hint:
            params["login_hint"] = request.login_hint
        if request.force_consent:
            # A new connection lets the owner pick the account; consent yields a refresh token.
            params["prompt"] = "consent" if request.login_hint else "select_account consent"
        return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params, quote_via=quote)}"

    async def exchange_code(self, code: str, *, code_verifier: str, redirect_uri: str) -> TokenSet:
        body = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            }
        )
        return replace(self._token_set(body), identity=self._identity(body.get("id_token")))

    async def refresh(self, refresh_token: str) -> TokenSet:
        body = await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )
        return self._token_set(body)

    async def revoke(self, token: str) -> None:
        # Revoking either token ends the whole grant of this client (every scope).
        response = await self._post(REVOCATION_ENDPOINT, {"token": token})
        if response.status_code == 200:
            return
        if response.status_code == 400 and _error_code(response) == "invalid_token":
            return  # already revoked or expired: nothing left to revoke
        raise _failure(response)

    # --- HTTP --------------------------------------------------------------------------------

    async def _post(self, url: str, data: dict[str, str]) -> httpx.Response:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ConnectorError("host_not_allowed")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                return await client.post(url, data=data, headers={"Accept": "application/json"})
        except httpx.TimeoutException:
            raise ConnectorUnavailableError("timeout") from None
        except httpx.HTTPError:
            raise ConnectorUnavailableError("network") from None

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        form = {
            **data,
            "client_id": self._client_id,
            "client_secret": self._client_secret.get_secret_value(),
        }
        response = await self._post(TOKEN_ENDPOINT, form)
        if response.status_code != 200:
            raise _failure(response)
        try:
            body = response.json()
        except ValueError:
            raise ConnectorUnavailableError("invalid_response") from None
        if not isinstance(body, dict):
            raise ConnectorUnavailableError("invalid_response")
        return body

    @staticmethod
    def _token_set(body: dict[str, Any]) -> TokenSet:
        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ConnectorUnavailableError("invalid_response")
        expires_in = body.get("expires_in")
        seconds = int(expires_in) if isinstance(expires_in, int | float) else 3600
        refresh_token = body.get("refresh_token")
        scope = body.get("scope")
        return TokenSet(
            access_token=access_token,
            expires_at=utcnow() + timedelta(seconds=max(seconds, 0)),
            scopes=_normalize(scope.split() if isinstance(scope, str) else ()),
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        )

    def _identity(self, id_token: object) -> AccountIdentity:
        """Subject and address from the ID token.

        It comes straight from the token endpoint over TLS: OpenID Connect Core (§3.1.3.7)
        then allows skipping the signature check. Issuer, audience and expiry are checked.
        """
        if not isinstance(id_token, str) or id_token.count(".") != 2:
            raise ConnectorError("identity_missing")
        payload = id_token.split(".")[1]
        try:
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (ValueError, binascii.Error):
            raise ConnectorError("identity_invalid") from None
        if not isinstance(claims, dict):
            raise ConnectorError("identity_invalid")
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        expiry = claims.get("exp")
        subject = claims.get("sub")
        if (
            claims.get("iss") not in ISSUERS
            or self._client_id not in audiences
            or not isinstance(expiry, int | float)
            or expiry < time.time() - 300
            or not isinstance(subject, str)
            or not subject
        ):
            raise ConnectorError("identity_invalid")
        email = claims.get("email")
        return AccountIdentity(subject=subject, email=email if isinstance(email, str) else None)
