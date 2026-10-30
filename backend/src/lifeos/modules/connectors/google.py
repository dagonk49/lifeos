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
import hashlib
import json
import re
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.modules.connectors.base import (
    CAPABILITIES,
    EXCLUDED_LABELS,
    AccountIdentity,
    AttachmentTooLargeError,
    AuthorizationRequest,
    Capability,
    ConnectorAuthError,
    ConnectorConflictError,
    ConnectorError,
    ConnectorGoneError,
    ConnectorQuotaError,
    ConnectorUnavailableError,
    CursorExpiredError,
    EventDraft,
    EventPage,
    EventTimes,
    MailChanges,
    MailIdPage,
    OutcomeUnknownError,
    RemoteAttachment,
    RemoteCollection,
    RemoteEvent,
    RemoteMail,
    SendUpdates,
    SentLookup,
    SentMail,
    TokenSet,
)
from lifeos.modules.mail import sanitize

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105  (URL, not a secret)
REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
# Media upload of a message (multipart: metadata, then the raw message), for large ones.
GMAIL_UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send"
# Up to this size the raw message goes base64url-encoded in a JSON body; beyond it, the
# upload endpoint (Gmail accepts messages of 35 MB there; to be checked by a real test).
SIMPLE_SEND_BYTES = 4 * 1024 * 1024
# Sent messages compared one by one by the reconciliation, at most.
_MAX_SENT_SCAN = 100
_MESSAGE_ID = re.compile(r"^<[^<>\s]{1,900}>$")
# Hosts LifeOS may call for this provider.
ALLOWED_HOSTS = frozenset({"oauth2.googleapis.com", "www.googleapis.com", "gmail.googleapis.com"})
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


# --- Gmail ----------------------------------------------------------------------------------

# System labels offered to the owner, with their French names; user labels are all offered.
SYSTEM_LABELS = {
    "INBOX": "Boîte de réception",
    "STARRED": "Messages suivis",
    "IMPORTANT": "Importants",
    "SENT": "Messages envoyés",
}
_MAX_PARTS = 200
_MAX_DEPTH = 20
_MAX_ATTACHMENTS = 50
# A text part Gmail serves apart (large body): fetched if no bigger than this.
_MAX_REMOTE_TEXT = 1024 * 1024
_MAX_REMOTE_TEXT_PARTS = 3
_HISTORY_TYPES = ("messageAdded", "messageDeleted", "labelAdded", "labelRemoved")
_CHARSET = re.compile(r'charset\s*=\s*"?([\w.:-]{1,40})', re.IGNORECASE)


def _b64url(value: object) -> bytes:
    if not isinstance(value, str):
        return b""
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error):
        return b""


def _headers(part: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for header in part.get("headers") or []:
        if isinstance(header, dict):
            name, value = header.get("name"), header.get("value")
            if isinstance(name, str) and isinstance(value, str):
                found.setdefault(name.lower(), value)
    return found


def _walk(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every part of a payload, depth first, bounded (a hostile tree stops there)."""
    stack: list[tuple[dict[str, Any], int]] = [(payload, 0)]
    seen = 0
    while stack and seen < _MAX_PARTS:
        current, level = stack.pop()
        seen += 1
        yield current
        children = current.get("parts")
        if level < _MAX_DEPTH and isinstance(children, list):
            stack.extend((c, level + 1) for c in reversed(children) if isinstance(c, dict))


def _is_attachment(part: dict[str, Any]) -> bool:
    name = part.get("filename")
    if isinstance(name, str) and name.strip():
        return True
    disposition = _headers(part).get("content-disposition", "")
    return disposition.strip().lower().startswith("attachment")


def _part_text(part: dict[str, Any]) -> str:
    mime = str(part.get("mimeType") or "").lower()
    body = part.get("body")
    data = _b64url(body.get("data")) if isinstance(body, dict) else b""
    match = _CHARSET.search(_headers(part).get("content-type", ""))
    text = sanitize.decode_bytes(data, match.group(1) if match else None)
    return sanitize.html_to_text(text) if mime == "text/html" else text


_PREFERENCE = {"text/plain": 0, "text/html": 2}


def _body_of(part: dict[str, Any], depth: int = 0) -> str | None:
    """The readable text of a part: text/plain preferred within an alternative, HTML
    converted otherwise, the inline parts of a mixed message one after the other."""
    if depth > _MAX_DEPTH or _is_attachment(part):
        return None
    mime = str(part.get("mimeType") or "").lower()
    children = [c for c in part.get("parts") or [] if isinstance(c, dict)][:_MAX_PARTS]
    if mime == "multipart/alternative":
        ranked = sorted(
            children, key=lambda c: _PREFERENCE.get(str(c.get("mimeType") or "").lower(), 1)
        )
        for child in ranked:
            text = _body_of(child, depth + 1)
            if text and text.strip():
                return text
        return None
    if mime.startswith("multipart/"):
        texts = [t for t in (_body_of(c, depth + 1) for c in children) if t and t.strip()]
        return "\n\n".join(texts) if texts else None
    if mime in ("text/plain", "text/html"):
        return _part_text(part)
    return None


def _attachments(payload: dict[str, Any]) -> tuple[RemoteAttachment, ...]:
    found: list[RemoteAttachment] = []
    for part in _walk(payload):
        if not _is_attachment(part):
            continue
        part_id = part.get("partId")
        body = part.get("body")
        size = body.get("size") if isinstance(body, dict) else None
        if not isinstance(part_id, str) or not part_id or len(part_id) > 64:
            continue
        mime = str(part.get("mimeType") or "application/octet-stream").lower()
        found.append(
            RemoteAttachment(
                part_id=part_id,
                filename=sanitize.filename(str(part.get("filename") or "")),
                mime=sanitize.one_line(mime, 100),
                size=size if isinstance(size, int) and size >= 0 else 0,
            )
        )
        if len(found) >= _MAX_ATTACHMENTS:
            break
    return tuple(found)


def parse_message(item: dict[str, Any]) -> RemoteMail | None:
    """A Gmail message (format=full) as plain text, or None when unusable."""
    provider_id = _token(item.get("id"))
    payload = item.get("payload")
    if provider_id is None or not isinstance(payload, dict):
        return None
    try:
        received_at = datetime.fromtimestamp(int(str(item.get("internalDate"))) / 1000, tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None
    headers = _headers(payload)
    sender = sanitize.addresses(headers.get("from"))
    body, truncated = sanitize.clean_text(_body_of(payload) or "")
    labels = item.get("labelIds")
    return RemoteMail(
        provider_id=provider_id,
        received_at=received_at,
        label_ids=frozenset(
            x for x in (labels if isinstance(labels, list) else []) if isinstance(x, str)
        ),
        subject=sanitize.subject(headers.get("subject")),
        from_address=sender[0][1] if sender else None,
        from_name=sender[0][0] if sender else None,
        to=tuple(address for _, address in sanitize.addresses(headers.get("to"))),
        cc=tuple(address for _, address in sanitize.addresses(headers.get("cc"))),
        body_text=body,
        body_truncated=truncated,
        thread_id=_token(item.get("threadId")),
        attachments=_attachments(payload),
        message_id_header=message_id_header(headers.get("message-id")),
    )


def message_id_header(value: str | None) -> str | None:
    """A Message-ID header ("<local@domain>") on one line, or None when malformed."""
    if not value:
        return None
    cleaned = sanitize.one_line(value, 998).strip()
    return cleaned if _MESSAGE_ID.match(cleaned) else None


def _ids(entries: object) -> list[str]:
    """Message ids of a list of {"message": {"id": ...}} entries."""
    ids: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        message = entry.get("message") if isinstance(entry, dict) else None
        if isinstance(message, dict) and (ref := _token(message.get("id"))):
            ids.append(ref)
    return ids


def _label_ids(entry: object) -> set[str]:
    labels = entry.get("labelIds") if isinstance(entry, dict) else None
    return {x for x in labels if isinstance(x, str)} if isinstance(labels, list) else set()


def _message_labels(entry: object) -> set[str] | None:
    message = entry.get("message") if isinstance(entry, dict) else None
    labels = message.get("labelIds") if isinstance(message, dict) else None
    return {x for x in labels if isinstance(x, str)} if isinstance(labels, list) else None


def parse_history(body: dict[str, Any], label_ref: str) -> MailChanges:
    """One page of users.history.list, for one label, in order: the last change of a
    message wins. Changes of other labels are left out."""
    state: dict[str, bool] = {}
    for record in body.get("history") or []:
        if not isinstance(record, dict):
            continue
        for entry in record.get("messagesAdded") or []:
            labels = _message_labels(entry)
            if labels is None or label_ref in labels:
                for ref in _ids([entry]):
                    state[ref] = True
        for ref in _ids(record.get("messagesDeleted")):
            state[ref] = False
        for entry in record.get("labelsAdded") or []:
            added = _label_ids(entry)
            # Moved to the spam or the bin: no longer the owner's mail.
            if added & EXCLUDED_LABELS:
                for ref in _ids([entry]):
                    state[ref] = False
            elif label_ref in added:
                for ref in _ids([entry]):
                    state[ref] = True
        for entry in record.get("labelsRemoved") or []:
            removed = _label_ids(entry)
            if label_ref in removed:
                for ref in _ids([entry]):
                    state[ref] = False
            elif removed & EXCLUDED_LABELS:
                # Back from the spam or the bin: read again, kept if it has the label.
                for ref in _ids([entry]):
                    state[ref] = True
    next_page = _token(body.get("nextPageToken"))
    history_id = body.get("historyId")
    return MailChanges(
        added=[ref for ref, present in state.items() if present],
        removed=[ref for ref, present in state.items() if not present],
        next_page=next_page,
        cursor=str(history_id) if next_page is None and history_id is not None else None,
    )


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

    # --- Gmail (read) -------------------------------------------------------------------------

    async def list_labels(self, access_token: str) -> list[RemoteCollection]:
        body = await self._api_get(f"{GMAIL_API}/labels", access_token, {})
        labels: list[RemoteCollection] = []
        for item in body.get("labels") or []:
            ref = _token(item.get("id")) if isinstance(item, dict) else None
            if ref is None:
                continue
            if item.get("type") == "system":
                if ref not in SYSTEM_LABELS:
                    continue  # spam, bin, drafts, categories, unread...
                name = SYSTEM_LABELS[ref]
            else:
                name = _text(item.get("name"), _MAX_SUMMARY) or ref
            labels.append(
                RemoteCollection(
                    provider_ref=ref, name=name, primary=ref == "INBOX", read_only=True
                )
            )
        return labels

    async def mail_cursor(self, access_token: str) -> str:
        body = await self._api_get(f"{GMAIL_API}/profile", access_token, {"fields": "historyId"})
        history_id = str(body.get("historyId") or "")
        if not history_id.isdigit():
            raise ConnectorUnavailableError("invalid_response")
        return history_id

    async def list_mail_ids(
        self, access_token: str, label_ref: str, *, newer_than_days: int, page: str | None
    ) -> MailIdPage:
        params = {
            "labelIds": label_ref,
            "q": f"newer_than:{int(newer_than_days)}d",
            "maxResults": "100",
            "includeSpamTrash": "false",
            "fields": "nextPageToken,messages(id)",
        }
        if page:
            params["pageToken"] = page
        body = await self._api_get(f"{GMAIL_API}/messages", access_token, params)
        return MailIdPage(
            ids=_ids([{"message": m} for m in body.get("messages") or []]),
            next_page=_token(body.get("nextPageToken")),
        )

    async def get_mail(self, access_token: str, message_id: str) -> RemoteMail | None:
        url = f"{GMAIL_API}/messages/{quote(message_id, safe='')}"
        try:
            item = await self._api_get(url, access_token, {"format": "full"})
        except ConnectorGoneError:
            return None
        payload = item.get("payload")
        if isinstance(payload, dict):
            await self._inline_large_texts(access_token, url, payload)
        return parse_message(item)

    async def _inline_large_texts(
        self, access_token: str, message_url: str, payload: dict[str, Any]
    ) -> None:
        """Gmail serves a large text body apart, like an attachment: fetched (bounded) so
        that the message reads as a whole."""
        fetched = 0
        for part in _walk(payload):
            body = part.get("body")
            if (
                fetched >= _MAX_REMOTE_TEXT_PARTS
                or _is_attachment(part)
                or str(part.get("mimeType") or "").lower() not in ("text/plain", "text/html")
                or not isinstance(body, dict)
                or body.get("data")
                or not isinstance(body.get("attachmentId"), str)
                or not isinstance(body.get("size"), int)
                or body["size"] > _MAX_REMOTE_TEXT
            ):
                continue
            fetched += 1
            remote = await self._api_get(
                f"{message_url}/attachments/{quote(body['attachmentId'], safe='')}",
                access_token,
                {},
            )
            body["data"] = remote.get("data")

    async def list_mail_changes(
        self, access_token: str, cursor: str, *, label_ref: str, page: str | None
    ) -> MailChanges:
        params: list[tuple[str, str]] = [
            ("startHistoryId", cursor),
            ("maxResults", "500"),
            *(("historyTypes", kind) for kind in _HISTORY_TYPES),
        ]
        if page:
            params.append(("pageToken", page))
        try:
            body = await self._api_get(f"{GMAIL_API}/history", access_token, tuple(params))
        except ConnectorGoneError:
            # The history no longer goes back to this cursor: a full pass is needed.
            raise CursorExpiredError("history_expired") from None
        return parse_history(body, label_ref)

    async def get_attachment(
        self, access_token: str, message_id: str, part_id: str, *, max_bytes: int
    ) -> bytes:
        # Gmail's attachment ids change from one read to the next: looked up again.
        url = f"{GMAIL_API}/messages/{quote(message_id, safe='')}"
        item = await self._api_get(url, access_token, {"format": "full"})
        payload = item.get("payload")
        parts = _walk(payload) if isinstance(payload, dict) else iter(())
        part = next((p for p in parts if p.get("partId") == part_id), None)
        body = part.get("body") if part else None
        if not isinstance(body, dict):
            raise ConnectorGoneError("attachment_gone")
        size = body.get("size")
        if isinstance(size, int) and size > max_bytes:
            raise AttachmentTooLargeError(size)
        data = body.get("data")
        attachment_id = body.get("attachmentId")
        if not data and isinstance(attachment_id, str):
            remote = await self._api_get(
                f"{url}/attachments/{quote(attachment_id, safe='')}", access_token, {}
            )
            data = remote.get("data")
        content = _b64url(data)
        if len(content) > max_bytes:
            raise AttachmentTooLargeError(len(content))
        return content

    # --- Gmail (send, task 18) ------------------------------------------------------------------
    # users.messages.send has no idempotency: a timeout or a 5xx after sending is an unknown
    # outcome (reconciled with find_sent_mail, never resent blindly).

    async def send_mail(
        self, access_token: str, raw: bytes, *, thread_id: str | None = None
    ) -> SentMail:
        metadata: dict[str, Any] = {"threadId": thread_id} if thread_id else {}
        params = {"fields": "id,threadId"}
        if len(raw) <= SIMPLE_SEND_BYTES:
            body = {**metadata, "raw": base64.urlsafe_b64encode(raw).decode().rstrip("=")}
            response = await self._api_send(
                "POST", f"{GMAIL_API}/messages/send", access_token, params=params, body=body
            )
        else:
            boundary = "lifeos_upload_" + hashlib.sha256(raw).hexdigest()[:32]
            content = (
                (
                    f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{json.dumps(metadata)}\r\n--{boundary}\r\n"
                    "Content-Type: message/rfc822\r\n\r\n"
                ).encode()
                + raw
                + f"\r\n--{boundary}--\r\n".encode()
            )
            response = await self._api_send(
                "POST",
                GMAIL_UPLOAD,
                access_token,
                params={**params, "uploadType": "multipart"},
                content=content,
                content_type=f"multipart/related; boundary={boundary}",
            )
        if response.status_code != 200:
            raise self._write_failure(response)
        try:
            result = response.json()
        except ValueError:
            raise OutcomeUnknownError("invalid_response") from None
        provider_id = _token(result.get("id")) if isinstance(result, dict) else None
        if provider_id is None:
            raise OutcomeUnknownError("invalid_response")
        return SentMail(provider_id=provider_id, thread_id=_token(result.get("threadId")))

    async def find_sent_mail(self, access_token: str, lookup: SentLookup) -> str | None:
        """Search by Message-ID first; then, in case Gmail rewrote it, compare the sent
        messages since the proposal (subject and visible recipients). Too many to compare:
        no conclusion (ConnectorError), never "not sent"."""
        bare = lookup.message_id_header.strip("<>")
        found = await self._api_get(
            f"{GMAIL_API}/messages",
            access_token,
            {
                "q": f"rfc822msgid:{bare}",
                "includeSpamTrash": "true",
                "maxResults": "5",
                "fields": "messages(id)",
            },
        )
        ids = _ids([{"message": m} for m in found.get("messages") or []])
        if ids:
            return ids[0]
        since = int(lookup.since.timestamp()) - 60
        listing = await self._api_get(
            f"{GMAIL_API}/messages",
            access_token,
            {
                "q": f"in:sent after:{since}",
                "includeSpamTrash": "true",
                "maxResults": str(_MAX_SENT_SCAN),
                "fields": "nextPageToken,messages(id)",
            },
        )
        if listing.get("nextPageToken"):
            raise ConnectorUnavailableError("too_many_sent")
        for candidate in _ids([{"message": m} for m in listing.get("messages") or []]):
            try:
                item = await self._api_get(
                    f"{GMAIL_API}/messages/{quote(candidate, safe='')}",
                    access_token,
                    (
                        ("format", "metadata"),
                        ("metadataHeaders", "Message-ID"),
                        ("metadataHeaders", "Subject"),
                        ("metadataHeaders", "To"),
                        ("metadataHeaders", "Cc"),
                        ("fields", "id,payload/headers"),
                    ),
                )
            except ConnectorGoneError:
                continue
            payload = item.get("payload")
            headers = _headers(payload) if isinstance(payload, dict) else {}
            if message_id_header(headers.get("message-id")) == lookup.message_id_header:
                return candidate
            recipients = frozenset(
                address
                for field in ("to", "cc")
                for _, address in sanitize.addresses(headers.get(field))
            )
            if (
                sanitize.subject(headers.get("subject")) == sanitize.subject(lookup.subject)
                and recipients == lookup.recipients
            ):
                return candidate
        return None

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
        content: bytes | None = None,
        content_type: str | None = None,
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
        if content_type:
            headers["Content-Type"] = content_type
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                if content is not None:
                    return await client.request(
                        method, url, params=params, content=content, headers=headers
                    )
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

    async def _api_get(
        self,
        url: str,
        access_token: str,
        params: Mapping[str, str] | tuple[tuple[str, str], ...],
    ) -> dict[str, Any]:
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
