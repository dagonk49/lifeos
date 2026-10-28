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
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.modules.connectors.base import (
    CAPABILITIES,
    AccountIdentity,
    AuthorizationRequest,
    Capability,
    ConnectorAuthError,
    ConnectorConflictError,
    ConnectorError,
    ConnectorGoneError,
    ConnectorQuotaError,
    ConnectorUnavailableError,
    EventDraft,
    EventPage,
    EventTimes,
    OutcomeUnknownError,
    RemoteCollection,
    RemoteEvent,
    SendUpdates,
    TokenSet,
)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105  (URL, not a secret)
REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
# Hosts LifeOS may call for this provider (the mail tasks add theirs).
ALLOWED_HOSTS = frozenset({"oauth2.googleapis.com", "www.googleapis.com"})
# Fields asked for (smaller responses, and nothing LifeOS would not use).
_EVENT_FIELDS = (
    "nextPageToken,items(id,etag,status,summary,description,location,start,end,"
    "recurringEventId,transparency,updated,organizer(self),attendees(self,responseStatus))"
)
_CALENDAR_FIELDS = "nextPageToken,items(id,summary,summaryOverride,primary,accessRole)"
# One event, for the exact preview of a change: the other attendees' addresses too.
_ONE_EVENT_FIELDS = (
    "id,etag,status,summary,description,location,start,end,recurringEventId,transparency,"
    "updated,organizer(self),attendees(email,self,responseStatus)"
)
_MAX_GUESTS = 100
# Third-party text is cut: an event is a line in an agenda, not a document.
_MAX_SUMMARY = 300
_MAX_DESCRIPTION = 2000
_MAX_LOCATION = 300
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


# Reasons of a Google API 403 that are about quotas, not permissions.
_QUOTA_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded"}
)


def _api_failure(response: httpx.Response) -> ConnectorError:
    """Errors of the Calendar and Gmail APIs (JSON `error.errors[].reason`)."""
    status = response.status_code
    if status == 401:
        # The access token was refused: the next call refreshes it.
        return ConnectorAuthError("access_token_rejected")
    if status in (404, 410):
        return ConnectorGoneError(f"http_{status}")
    reasons: set[str] = set()
    try:
        error = response.json().get("error")
        if isinstance(error, dict):
            for item in error.get("errors") or []:
                reason = item.get("reason") if isinstance(item, dict) else None
                if isinstance(reason, str) and _ERROR_CODE.match(reason.lower()):
                    reasons.add(reason)
    except (ValueError, AttributeError):
        pass
    if status == 429 or (status == 403 and reasons & _QUOTA_REASONS):
        retry_after = response.headers.get("retry-after", "")
        return ConnectorQuotaError(
            "rate_limited", retry_after=int(retry_after) if retry_after.isdigit() else None
        )
    if status >= 500:
        return ConnectorUnavailableError(f"http_{status}")
    return ConnectorError("forbidden" if status == 403 else f"http_{status}")


def _items(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = body.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _token(value: object) -> str | None:
    return value if isinstance(value, str) and value and len(value) <= 4096 else None


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _rfc3339(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("aware datetime required")
    return moment.isoformat()


def _instant(value: object) -> datetime | None:
    """An RFC 3339 date-time with its offset, as an aware datetime (never naive)."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _day(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _zone(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return value


def parse_event(item: dict[str, Any]) -> RemoteEvent | None:
    """One occurrence from the Calendar API, or None when unusable (no id, no time)."""
    provider_id = item.get("id")
    if not isinstance(provider_id, str) or not provider_id or len(provider_id) > 1024:
        return None
    status = item.get("status")
    if status == "cancelled":
        return None
    raw_start, raw_end = item.get("start"), item.get("end")
    start: dict[str, Any] = raw_start if isinstance(raw_start, dict) else {}
    end: dict[str, Any] = raw_end if isinstance(raw_end, dict) else {}
    starts_at, ends_at = _instant(start.get("dateTime")), _instant(end.get("dateTime"))
    start_date, end_date = _day(start.get("date")), _day(end.get("date"))
    if starts_at is not None:
        start_date = end_date = None
        if ends_at is None or ends_at < starts_at:
            ends_at = starts_at
    elif start_date is not None:
        if end_date is None or end_date <= start_date:
            end_date = start_date + timedelta(days=1)
    else:
        return None
    attendees = [a for a in item.get("attendees") or [] if isinstance(a, dict)]
    me = next((a for a in attendees if a.get("self") is True), None)
    response = me.get("responseStatus") if me else None
    organizer = item.get("organizer")
    guests = tuple(
        a["email"].strip().lower()
        for a in attendees
        if a.get("self") is not True
        and isinstance(a.get("email"), str)
        and 3 <= len(a["email"].strip()) <= 254
    )[:_MAX_GUESTS]
    return RemoteEvent(
        provider_id=provider_id,
        status="tentative" if status == "tentative" else "confirmed",
        summary=_text(item.get("summary"), _MAX_SUMMARY) or "",
        starts_at=starts_at,
        ends_at=ends_at,
        start_date=start_date,
        end_date=end_date,
        timezone=_zone(start.get("timeZone")),
        description=_text(item.get("description"), _MAX_DESCRIPTION),
        location=_text(item.get("location"), _MAX_LOCATION),
        series_id=_token(item.get("recurringEventId")),
        busy=item.get("transparency") != "transparent",
        organizer_self=isinstance(organizer, dict) and organizer.get("self") is True,
        response=response
        if isinstance(response, str) and _ERROR_CODE.match(response.lower())
        else None,
        attendees=len(attendees),
        etag=_token(item.get("etag")),
        updated_at=_instant(item.get("updated")),
        guests=guests,
    )


def _when(times: EventTimes, *, clear: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """`start` and `end` of the Calendar API. `clear` (PATCH): the other form is nulled,
    so that a timed event may become an all-day one and back."""
    if times.starts_at is not None and times.ends_at is not None:
        start: dict[str, Any] = {"dateTime": _rfc3339(times.starts_at)}
        end: dict[str, Any] = {"dateTime": _rfc3339(times.ends_at)}
        if times.timezone:
            start["timeZone"] = end["timeZone"] = times.timezone
        if clear:
            start["date"] = end["date"] = None
        return start, end
    if times.start_date is None or times.end_date is None:
        raise ValueError("times without instants nor dates")
    start, end = {"date": times.start_date.isoformat()}, {"date": times.end_date.isoformat()}
    if clear:
        start["dateTime"] = end["dateTime"] = None
        start["timeZone"] = end["timeZone"] = None
    return start, end


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

    # --- Calendar (read) ----------------------------------------------------------------------

    async def list_calendars(self, access_token: str) -> list[RemoteCollection]:
        calendars: list[RemoteCollection] = []
        page: str | None = None
        for _ in range(20):  # 250 calendars per page: far more than an account holds
            params = {"maxResults": "250", "fields": _CALENDAR_FIELDS, "minAccessRole": "reader"}
            if page:
                params["pageToken"] = page
            body = await self._api_get(
                f"{CALENDAR_API}/users/me/calendarList", access_token, params
            )
            for item in _items(body):
                ref = item.get("id")
                if not isinstance(ref, str) or not ref:
                    continue
                name = item.get("summaryOverride") or item.get("summary") or ref
                calendars.append(
                    RemoteCollection(
                        provider_ref=ref,
                        name=_text(name, _MAX_SUMMARY) or ref,
                        primary=item.get("primary") is True,
                        read_only=item.get("accessRole") not in ("owner", "writer"),
                        owned=item.get("accessRole") == "owner",
                    )
                )
            page = _token(body.get("nextPageToken"))
            if page is None:
                break
        return calendars

    async def list_events(
        self,
        access_token: str,
        calendar_ref: str,
        *,
        time_min: datetime,
        time_max: datetime,
        page: str | None = None,
    ) -> EventPage:
        # singleEvents: Google expands each series in its own time zone (DST included),
        # every occurrence keeping a stable id; cancelled occurrences are left out.
        params = {
            "singleEvents": "true",
            "showDeleted": "false",
            "maxResults": "250",
            "timeMin": _rfc3339(time_min),
            "timeMax": _rfc3339(time_max),
            "fields": _EVENT_FIELDS,
        }
        if page:
            params["pageToken"] = page
        url = f"{CALENDAR_API}/calendars/{quote(calendar_ref, safe='')}/events"
        body = await self._api_get(url, access_token, params)
        events = [e for e in (parse_event(item) for item in _items(body)) if e is not None]
        return EventPage(events=events, next_page=_token(body.get("nextPageToken")))

    # --- Calendar (writes, task 16) ------------------------------------------------------------
    # Each write is idempotent (ADR 0016): the client chooses the id of a new event (a
    # replay answers 409 and the event is read back), changes carry If-Match with the
    # etag of the preview (412 when the event changed: read back and compared), and
    # deleting an event already gone (404, 410) is done. `sendUpdates` is always explicit.

    def _event_url(self, calendar_ref: str, event_id: str | None = None) -> str:
        url = f"{CALENDAR_API}/calendars/{quote(calendar_ref, safe='')}/events"
        return f"{url}/{quote(event_id, safe='')}" if event_id is not None else url

    async def get_event(
        self, access_token: str, calendar_ref: str, event_id: str
    ) -> RemoteEvent | None:
        try:
            body = await self._api_get(
                self._event_url(calendar_ref, event_id),
                access_token,
                {"fields": _ONE_EVENT_FIELDS},
            )
        except ConnectorGoneError:
            return None
        return parse_event(body)

    async def create_event(
        self,
        access_token: str,
        calendar_ref: str,
        draft: EventDraft,
        *,
        event_id: str,
        send_updates: SendUpdates,
    ) -> RemoteEvent:
        start, end = _when(draft.times)
        body: dict[str, Any] = {
            "id": event_id,
            "summary": draft.summary,
            "start": start,
            "end": end,
        }
        if draft.description:
            body["description"] = draft.description
        if draft.location:
            body["location"] = draft.location
        if draft.guests:
            body["attendees"] = [{"email": guest} for guest in draft.guests]
        response = await self._api_send(
            "POST",
            self._event_url(calendar_ref),
            access_token,
            params={"sendUpdates": send_updates, "fields": _ONE_EVENT_FIELDS},
            body=body,
        )
        if response.status_code == 409:
            # This id exists: the creation was already done (a replay).
            existing = await self.get_event(access_token, calendar_ref, event_id)
            if existing is None:
                raise ConnectorConflictError("event_id_taken")
            return existing
        return self._written(response)

    async def move_event(
        self,
        access_token: str,
        calendar_ref: str,
        event_id: str,
        times: EventTimes,
        *,
        etag: str | None,
        send_updates: SendUpdates,
    ) -> RemoteEvent:
        start, end = _when(times, clear=True)
        response = await self._api_send(
            "PATCH",
            self._event_url(calendar_ref, event_id),
            access_token,
            params={"sendUpdates": send_updates, "fields": _ONE_EVENT_FIELDS},
            body={"start": start, "end": end},
            if_match=etag,
        )
        if response.status_code == 412:
            current = await self.get_event(access_token, calendar_ref, event_id)
            if current is None:
                raise ConnectorGoneError("http_404")
            if current.times.same_as(times):
                return current  # moved by an earlier attempt
            raise ConnectorConflictError("event_changed")
        return self._written(response)

    async def cancel_event(
        self,
        access_token: str,
        calendar_ref: str,
        event_id: str,
        *,
        etag: str | None,
        send_updates: SendUpdates,
    ) -> None:
        response = await self._api_send(
            "DELETE",
            self._event_url(calendar_ref, event_id),
            access_token,
            params={"sendUpdates": send_updates},
            if_match=etag,
        )
        if response.status_code in (200, 204, 404, 410):
            return
        if response.status_code == 412:
            if await self.get_event(access_token, calendar_ref, event_id) is None:
                return
            raise ConnectorConflictError("event_changed")
        raise self._write_failure(response)

    @staticmethod
    def _write_failure(response: httpx.Response) -> ConnectorError:
        if response.status_code >= 500:
            # The provider may have applied it before failing: reconcile, never resend.
            return OutcomeUnknownError(f"http_{response.status_code}")
        return _api_failure(response)

    def _written(self, response: httpx.Response) -> RemoteEvent:
        if response.status_code != 200:
            raise self._write_failure(response)
        try:
            body = response.json()
        except ValueError:
            raise OutcomeUnknownError("invalid_response") from None
        event = parse_event(body) if isinstance(body, dict) else None
        if event is None:
            raise OutcomeUnknownError("invalid_response")
        return event

    # --- HTTP --------------------------------------------------------------------------------

    async def _api_send(
        self,
        method: str,
        url: str,
        access_token: str,
        *,
        params: dict[str, str],
        body: dict[str, Any] | None = None,
        if_match: str | None = None,
    ) -> httpx.Response:
        """A write on a Google API. A failure before anything was sent (connection) is
        ConnectorUnavailableError; after sending (timeout, broken connection), the write
        may have happened: OutcomeUnknownError."""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ConnectorError("host_not_allowed")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {access_token}"}
        if if_match:
            headers["If-Match"] = if_match
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                return await client.request(method, url, params=params, json=body, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise ConnectorUnavailableError("network") from None
        except httpx.TimeoutException:
            raise OutcomeUnknownError("timeout") from None
        except httpx.HTTPError:
            raise OutcomeUnknownError("network") from None

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

    async def _api_get(self, url: str, access_token: str, params: dict[str, str]) -> dict[str, Any]:
        """GET on a Google API with the access token in the Authorization header."""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ConnectorError("host_not_allowed")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {access_token}",
                    },
                )
        except httpx.TimeoutException:
            raise ConnectorUnavailableError("timeout") from None
        except httpx.HTTPError:
            raise ConnectorUnavailableError("network") from None
        if response.status_code != 200:
            raise _api_failure(response)
        try:
            body = response.json()
        except ValueError:
            raise ConnectorUnavailableError("invalid_response") from None
        if not isinstance(body, dict):
            raise ConnectorUnavailableError("invalid_response")
        return body

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
