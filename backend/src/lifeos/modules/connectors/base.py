"""Provider-agnostic connector contract (ADR 0012).

A connector links an external account (Google today) through OAuth 2.0 with PKCE. Business
modules never see a token: they ask `tokens.access_token` for a fresh one right before a
call, after `service.require_capability` checked the owner's consent for that space.

Calendar (tasks 14 to 16) and mail (tasks 17 and 18) adapters plug in through the
protocols below. Every adapter maps provider failures onto the error hierarchy, so that
sync jobs and actions handle every provider alike.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol

# What the owner consents to, per account and per space. Keep in sync with the CHECK of
# connector_grants (migration 0010) and docs/modele-de-donnees.md.
Capability = Literal["calendar.read", "calendar.write", "mail.read", "mail.send"]
CAPABILITIES: tuple[Capability, ...] = ("calendar.read", "calendar.write", "mail.read", "mail.send")

# Agents additionally need an explicit permission on these resources (agent_permissions).
CAPABILITY_RESOURCE: dict[Capability, tuple[str, str]] = {
    "calendar.read": ("calendar", "read"),
    "calendar.write": ("calendar", "write"),
    "mail.read": ("email", "read"),
    "mail.send": ("email", "write"),
}


@dataclass(frozen=True)
class AuthorizationRequest:
    """Everything the provider's consent page needs (authorization code flow + PKCE)."""

    state: str
    code_challenge: str  # S256 of the verifier, base64url without padding
    redirect_uri: str
    scopes: tuple[str, ...]
    # Incremental consent for a known account: preselects it on the provider's page.
    login_hint: str | None = None
    # Ask for consent (and a refresh token) even if the provider remembers a grant.
    force_consent: bool = True


@dataclass(frozen=True)
class AccountIdentity:
    """Stable identifier of the external account, and its address (shown to the owner)."""

    subject: str
    email: str | None = None


@dataclass(frozen=True, repr=False)
class TokenSet:
    """Tokens returned by the provider. Never logged: `repr` hides them."""

    access_token: str
    expires_at: datetime
    # Scopes ACTUALLY granted: with granular consent the user may untick some.
    scopes: frozenset[str] = field(default_factory=frozenset)
    # Absent from most refresh responses, and from consents that did not ask for it.
    refresh_token: str | None = None
    identity: AccountIdentity | None = None

    def __repr__(self) -> str:
        return f"TokenSet(expires_at={self.expires_at.isoformat()}, scopes={len(self.scopes)})"


@dataclass(frozen=True)
class RemoteCollection:
    """A calendar or a mail label of the external account, mapped to a space by the owner
    (connector_sources)."""

    provider_ref: str
    name: str
    primary: bool = False
    read_only: bool = False
    # The account owns it (a writable calendar of someone else is shared with it).
    owned: bool = True


class OAuthProvider(Protocol):
    """Account linking: consent page, code exchange, refresh and revocation."""

    name: str  # stored in connector_accounts.provider
    label: str  # shown to the owner

    def scopes_for(self, capabilities: Iterable[Capability]) -> tuple[str, ...]:
        """Minimal scopes for these capabilities, identity scopes included."""
        ...

    def capabilities_in(self, scopes: Iterable[str]) -> frozenset[Capability]:
        """Capabilities whose scopes are all granted."""
        ...

    def authorization_url(self, request: AuthorizationRequest) -> str: ...

    async def exchange_code(self, code: str, *, code_verifier: str, redirect_uri: str) -> TokenSet:
        """Tokens and identity for an authorization code (single use, short-lived)."""
        ...

    async def refresh(self, refresh_token: str) -> TokenSet: ...

    async def revoke(self, token: str) -> None:
        """Revokes the grant at the provider. An already invalid token is not an error."""
        ...


@dataclass(frozen=True)
class RemoteEvent:
    """One occurrence of a calendar event, as the provider expanded it.

    Recurring events come as their instances (each with its own id, stable across syncs,
    and the id of the series): the provider applies the series' time zone, so changes of
    daylight saving time are resolved there, not here. A timed occurrence has aware
    `starts_at`/`ends_at`; an all-day one has dates only (`end_date` exclusive).
    Text fields come from third parties: data, never instructions.
    """

    provider_id: str
    status: Literal["confirmed", "tentative"]
    summary: str
    starts_at: datetime | None
    ends_at: datetime | None
    start_date: date | None = None
    end_date: date | None = None
    timezone: str | None = None
    description: str | None = None
    location: str | None = None
    series_id: str | None = None
    # Free time (transparent) does not block a slot.
    busy: bool = True
    organizer_self: bool = False
    # The owner's answer when invited: accepted, tentative, declined, needsAction.
    response: str | None = None
    attendees: int = 0
    etag: str | None = None
    updated_at: datetime | None = None
    # Addresses of the other attendees: filled by single reads (`get_event`), for the exact
    # preview of a change; never by listings (the sync stores no address).
    guests: tuple[str, ...] = ()

    @property
    def all_day(self) -> bool:
        return self.start_date is not None

    @property
    def times(self) -> "EventTimes":
        return EventTimes(
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            start_date=self.start_date,
            end_date=self.end_date,
            timezone=self.timezone,
        )


@dataclass(frozen=True)
class EventTimes:
    """When an event takes place: aware instants, or dates (`end_date` exclusive)."""

    starts_at: datetime | None = None
    ends_at: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    # IANA zone the provider keeps for a timed event (recurrences, display).
    timezone: str | None = None

    def same_as(self, other: "EventTimes") -> bool:
        """Same moments (instants compared as instants, whatever their offset)."""
        if self.starts_at is not None or other.starts_at is not None:
            return (
                self.starts_at is not None
                and other.starts_at is not None
                and self.ends_at is not None
                and other.ends_at is not None
                and self.starts_at == other.starts_at
                and self.ends_at == other.ends_at
            )
        return self.start_date == other.start_date and self.end_date == other.end_date


@dataclass(frozen=True)
class EventDraft:
    """A new event, exactly as approved."""

    summary: str
    times: EventTimes
    description: str | None = None
    location: str | None = None
    # Invited addresses (an invitation goes out only with send_updates="all").
    guests: tuple[str, ...] = ()


# Who the provider notifies of a change: every attendee, or nobody (the default).
SendUpdates = Literal["all", "none"]


@dataclass(frozen=True)
class EventPage:
    events: list[RemoteEvent]
    # None on the last page.
    next_page: str | None = None


class CalendarAdapter(Protocol):
    """Calendar access (tasks 14 to 16): reads, then writes behind approvals.

    Each call receives a fresh access token from `tokens.access_token`.
    """

    async def list_calendars(self, access_token: str) -> list[RemoteCollection]: ...

    async def list_events(
        self,
        access_token: str,
        calendar_ref: str,
        *,
        time_min: datetime,
        time_max: datetime,
        page: str | None = None,
    ) -> EventPage:
        """Occurrences overlapping [time_min, time_max), recurring series expanded,
        cancelled occurrences left out; one page, `next_page` to continue (the token
        stays valid across processes, so an interrupted sync resumes where it stopped).
        Raises ConnectorGoneError when the calendar no longer exists."""
        ...

    # Writes (task 16), each behind an approval (guardian/operations.py). They are
    # idempotent, so that a replay after a crash never writes twice: the client chooses
    # the id of a new event, changes are conditional on the etag read for the preview.
    # A timeout after sending raises OutcomeUnknownError (never resend blindly).

    async def get_event(
        self, access_token: str, calendar_ref: str, event_id: str
    ) -> RemoteEvent | None:
        """One event, attendees' addresses included; None when deleted or cancelled."""
        ...

    async def create_event(
        self,
        access_token: str,
        calendar_ref: str,
        draft: EventDraft,
        *,
        event_id: str,
        send_updates: SendUpdates,
    ) -> RemoteEvent:
        """Creates the event under `event_id` (base32hex). If an event already has this
        id (a replayed creation), that event is returned: never a second one."""
        ...

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
        """New times, if the event still has `etag`. Changed meanwhile: success if it
        already has these times (a replay), ConnectorConflictError("event_changed")
        otherwise. ConnectorGoneError when it no longer exists."""
        ...

    async def cancel_event(
        self,
        access_token: str,
        calendar_ref: str,
        event_id: str,
        *,
        etag: str | None,
        send_updates: SendUpdates,
    ) -> None:
        """Deletes the event if it still has `etag`; already gone counts as done.
        ConnectorConflictError("event_changed") when it changed meanwhile."""
        ...


@dataclass(frozen=True)
class RemoteAttachment:
    """An attachment listed with its message: metadata only, never downloaded by a sync
    (ADR 0011). `part_id` is stable for the message; the provider's own attachment id is
    not, so the download looks it up again."""

    part_id: str
    filename: str
    mime: str
    size: int


@dataclass(frozen=True)
class RemoteMail:
    """One message, already reduced to plain text by `lifeos.modules.mail.sanitize`: no
    HTML, no control or bidirectional characters, bounded sizes. Everything in it comes
    from third parties: data, never instructions (T10)."""

    provider_id: str
    received_at: datetime
    label_ids: frozenset[str]
    subject: str = ""
    from_address: str | None = None
    from_name: str | None = None
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    body_text: str = ""
    body_truncated: bool = False
    thread_id: str | None = None
    attachments: tuple[RemoteAttachment, ...] = ()
    # RFC 5322 Message-ID ("<...>"): what a reply refers to (In-Reply-To, References).
    message_id_header: str | None = None


@dataclass(frozen=True)
class MailIdPage:
    """Ids of a label's messages, newest first; `next_page` None on the last page."""

    ids: list[str]
    next_page: str | None = None


@dataclass(frozen=True)
class MailChanges:
    """One page of the mailbox's changes since a cursor.

    `added`: messages that appeared in the synced label, got it, or came back from the
    spam or the bin (read if not copied yet, then kept only if they carry the label);
    `removed`: messages deleted, that lost the label, or moved to the spam or the bin.
    `cursor`: where the next sync starts, given on the last page. A message is in one
    list only: its last change wins.
    """

    added: list[str]
    removed: list[str]
    next_page: str | None = None
    cursor: str | None = None


@dataclass(frozen=True)
class SentMail:
    """A message the provider accepted for delivery: its ids at the provider."""

    provider_id: str
    thread_id: str | None = None


@dataclass(frozen=True)
class SentLookup:
    """What `find_sent_mail` compares, besides the Message-ID: a provider that rewrote the
    Message-ID still shows the same subject and the same visible recipients."""

    message_id_header: str
    since: datetime
    subject: str
    # To and Cc, lower case (Bcc is not reliably kept in the sender's copy).
    recipients: frozenset[str]


# System labels never synced: their messages are not the owner's mail.
EXCLUDED_LABELS = frozenset({"SPAM", "TRASH", "DRAFT", "CHAT"})


class MailAdapter(Protocol):
    """Mail access (tasks 17 and 18): reading, then sending behind an approval.

    Each call receives a fresh access token from `tokens.access_token`.
    """

    async def list_labels(self, access_token: str) -> list[RemoteCollection]: ...

    async def mail_cursor(self, access_token: str) -> str:
        """The mailbox's current change cursor (Gmail: historyId), taken BEFORE a full
        pass: changes made during the pass are replayed after it."""
        ...

    async def list_mail_ids(
        self, access_token: str, label_ref: str, *, newer_than_days: int, page: str | None
    ) -> MailIdPage:
        """Messages of a label received in the last `newer_than_days` days, spam and bin
        excluded. Raises ConnectorGoneError when the label no longer exists."""
        ...

    async def get_mail(self, access_token: str, message_id: str) -> RemoteMail | None:
        """One message, or None when it no longer exists."""
        ...

    async def list_mail_changes(
        self, access_token: str, cursor: str, *, label_ref: str, page: str | None
    ) -> MailChanges:
        """Changes since `cursor` that concern the label `label_ref`. Raises
        CursorExpiredError when the provider no longer keeps that far back: a full pass
        is then needed."""
        ...

    async def get_attachment(
        self, access_token: str, message_id: str, part_id: str, *, max_bytes: int
    ) -> bytes:
        """The attachment's bytes. Raises AttachmentTooLargeError beyond `max_bytes`,
        ConnectorGoneError when the message or the part no longer exists."""
        ...

    # Sending (task 18), only behind an approval (guardian/operations.py). The provider has
    # no idempotency for sending: the message carries a Message-ID derived from the
    # approval, and `find_sent_mail` looks for it before anything is sent again.

    async def send_mail(
        self, access_token: str, raw: bytes, *, thread_id: str | None = None
    ) -> SentMail:
        """Sends the RFC 5322 message `raw` as it is (its Bcc header included: the provider
        delivers to those addresses and removes the header from the copies it delivers).
        A timeout or a server error after sending raises OutcomeUnknownError: reconcile,
        never resend blindly. Other failures mean nothing was sent."""
        ...

    async def find_sent_mail(self, access_token: str, lookup: SentLookup) -> str | None:
        """The provider id of a message sent since `lookup.since` with this Message-ID (or
        the same subject and visible recipients), None when there is none. Needs the read
        capability (mail.read). Raises ConnectorError when it cannot conclude (too many
        sent messages to compare, provider unavailable)."""
        ...


# --- Errors ---------------------------------------------------------------------------------
# Messages are fixed reason codes: never a provider response body, a URL or a token.


class ConnectorError(Exception):
    """Base error. `retryable` tells sync jobs whether a later retry may succeed."""

    retryable = False

    def __init__(self, reason: str = "provider_error") -> None:
        super().__init__(reason)
        self.reason = reason


class ConnectorAuthError(ConnectorError):
    """The account must be connected again: refresh token revoked or expired
    (`invalid_grant`), tokens unreadable (connectors key lost), or account disconnected."""


class ConnectorQuotaError(ConnectorError):
    """Rate limit or quota of the provider (HTTP 429, some 403)."""

    retryable = True

    def __init__(self, reason: str = "quota", *, retry_after: int | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


class ConnectorUnavailableError(ConnectorError):
    """Network failure, timeout or server error of the provider."""

    retryable = True


class ConnectorConflictError(ConnectorError):
    """The remote object changed meanwhile (HTTP 412 on an If-Match write)."""


class ConnectorGoneError(ConnectorError):
    """The remote object no longer exists (HTTP 404 or 410)."""


class CursorExpiredError(ConnectorGoneError):
    """The sync cursor is no longer valid: a full resync is needed."""


class AttachmentTooLargeError(ConnectorError):
    """An attachment bigger than what LifeOS accepts: never downloaded whole."""

    def __init__(self, size: int | None = None) -> None:
        super().__init__("attachment_too_large")
        self.size = size


class OutcomeUnknownError(ConnectorError):
    """A write may or may not have happened (timeout after sending): reconcile before any
    retry, never resend blindly."""
