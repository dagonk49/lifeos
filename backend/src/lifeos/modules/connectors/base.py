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

    @property
    def all_day(self) -> bool:
        return self.start_date is not None


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


class MailAdapter(Protocol):
    """Mail access (tasks 17 and 18): reading, then sending behind an approval."""

    async def list_labels(self, access_token: str) -> list[RemoteCollection]: ...


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


class OutcomeUnknownError(ConnectorError):
    """A write may or may not have happened (timeout after sending): reconcile before any
    retry, never resend blindly."""
