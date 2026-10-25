"""In-memory connector for tests and end-to-end runs: no network, no real account.

Its consent page is skipped: the authorization URL is the redirect URI itself, carrying a
code, as if the owner had agreed at once (minus the capabilities listed in `decline`).
It checks PKCE like a real provider, keeps earlier grants (incremental consent), and can
be scripted to fail (`fail_next`).

Its calendars (`calendars`, `events`) are in memory: tests and the end-to-end journey add,
move and cancel occurrences, then resync. Pages are `page_size` events long.
"""

import asyncio
import base64
import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from lifeos.core.security import utcnow
from lifeos.modules.connectors.base import (
    CAPABILITIES,
    AccountIdentity,
    AuthorizationRequest,
    Capability,
    ConnectorAuthError,
    ConnectorGoneError,
    EventPage,
    RemoteCollection,
    RemoteEvent,
    TokenSet,
)

IDENTITY_SCOPES = ("fake/identity",)
SCOPES: dict[Capability, tuple[str, ...]] = {c: (f"fake/{c}",) for c in CAPABILITIES}


@dataclass
class _PendingCode:
    challenge: str
    redirect_uri: str
    scopes: frozenset[str]


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class FakeConnector:
    name = "fake"
    label = "Démo"

    def __init__(
        self,
        *,
        subject: str = "fake-account-1",
        email: str = "agenda.demo@example.org",
        token_ttl: timedelta = timedelta(hours=1),
    ) -> None:
        self.subject = subject
        self.email = email
        self.token_ttl = token_ttl
        # Capabilities the "owner" unticks on the consent page.
        self.decline: set[Capability] = set()
        self.refresh_delay = 0.0
        self.refresh_calls = 0
        self.revoked: list[str] = []
        self.requests: list[AuthorizationRequest] = []
        # Every code and token handed out (tests check that none of them leaks).
        self.issued: list[str] = []
        self._codes: dict[str, _PendingCode] = {}
        self._refresh_tokens: set[str] = set()
        # Scopes granted so far (include_granted_scopes): kept until revoked.
        self._granted: set[str] = set()
        self._errors: dict[str, list[Exception]] = {}
        self.calendars: list[RemoteCollection] = [
            RemoteCollection("primary", "Agenda de démonstration", primary=True),
            RemoteCollection("family", "Famille", read_only=True),
        ]
        # Occurrences by calendar ref, by id.
        self.events: dict[str, dict[str, RemoteEvent]] = {"primary": {}, "family": {}}
        self.page_size = 50
        self.event_calls = 0
        self.access_tokens_seen: list[str] = []

    def fail_next(self, method: str, error: Exception) -> None:
        """The next call of `method` (exchange_code, refresh, revoke) raises `error`."""
        self._errors.setdefault(method, []).append(error)

    def _maybe_fail(self, method: str) -> None:
        pending = self._errors.get(method)
        if pending:
            raise pending.pop(0)

    def scopes_for(self, capabilities: Iterable[Capability]) -> tuple[str, ...]:
        wanted = set(capabilities)
        return IDENTITY_SCOPES + tuple(SCOPES[c][0] for c in CAPABILITIES if c in wanted)

    def capabilities_in(self, scopes: Iterable[str]) -> frozenset[Capability]:
        granted = set(scopes)
        return frozenset(c for c in CAPABILITIES if set(SCOPES[c]) <= granted)

    def authorization_url(self, request: AuthorizationRequest) -> str:
        self.requests.append(request)
        declined = {SCOPES[c][0] for c in self.decline}
        code = "fake-code-" + secrets.token_urlsafe(16)
        self.issued.append(code)
        self._codes[code] = _PendingCode(
            challenge=request.code_challenge,
            redirect_uri=request.redirect_uri,
            scopes=frozenset(s for s in request.scopes if s not in declined),
        )
        return f"{request.redirect_uri}?{urlencode({'state': request.state, 'code': code})}"

    def _tokens(self, *, refresh: bool) -> TokenSet:
        refresh_token = "fake-refresh-" + secrets.token_urlsafe(16) if refresh else None
        if refresh_token:
            self._refresh_tokens.add(refresh_token)
            self.issued.append(refresh_token)
        access_token = "fake-access-" + secrets.token_urlsafe(16)
        self.issued.append(access_token)
        return TokenSet(
            access_token=access_token,
            expires_at=utcnow() + self.token_ttl,
            scopes=frozenset(self._granted),
            refresh_token=refresh_token,
        )

    async def exchange_code(self, code: str, *, code_verifier: str, redirect_uri: str) -> TokenSet:
        self._maybe_fail("exchange_code")
        pending = self._codes.pop(code, None)
        if (
            pending is None
            or pending.redirect_uri != redirect_uri
            or s256(code_verifier) != pending.challenge
        ):
            raise ConnectorAuthError("invalid_grant")
        self._granted |= pending.scopes
        tokens = self._tokens(refresh=True)
        return TokenSet(
            access_token=tokens.access_token,
            expires_at=tokens.expires_at,
            scopes=tokens.scopes,
            refresh_token=tokens.refresh_token,
            identity=AccountIdentity(subject=self.subject, email=self.email),
        )

    async def refresh(self, refresh_token: str) -> TokenSet:
        self.refresh_calls += 1
        if self.refresh_delay:
            await asyncio.sleep(self.refresh_delay)
        self._maybe_fail("refresh")
        if refresh_token not in self._refresh_tokens:
            raise ConnectorAuthError("invalid_grant")
        return self._tokens(refresh=False)

    async def revoke(self, token: str) -> None:
        self._maybe_fail("revoke")
        self.revoked.append(token)
        # Like Google: revoking one token ends the whole grant.
        self._refresh_tokens.clear()
        self._granted.clear()

    # --- Calendar ---------------------------------------------------------------------------

    def add_event(self, calendar_ref: str, event: RemoteEvent) -> None:
        self.events.setdefault(calendar_ref, {})[event.provider_id] = event

    def remove_event(self, calendar_ref: str, provider_id: str) -> None:
        self.events.get(calendar_ref, {}).pop(provider_id, None)

    def add_demo_week(self, today: date, zone: str = "Europe/Paris") -> None:
        """A few occurrences around `today` (end-to-end journey)."""
        tz = ZoneInfo(zone)

        def at(day: date, hour: int, minutes: int = 0) -> datetime:
            return datetime.combine(day, time(hour, minutes), tzinfo=tz)

        self.add_event(
            "primary",
            RemoteEvent(
                provider_id="demo-standup",
                status="confirmed",
                summary="Point d'équipe",
                starts_at=at(today, 9, 30),
                ends_at=at(today, 10),
                timezone=zone,
            ),
        )
        self.add_event(
            "primary",
            RemoteEvent(
                provider_id="demo-dentist",
                status="confirmed",
                summary="Dentiste",
                starts_at=at(today + timedelta(days=1), 14),
                ends_at=at(today + timedelta(days=1), 14, 45),
                location="Cabinet du centre",
                timezone=zone,
            ),
        )
        self.add_event(
            "family",
            RemoteEvent(
                provider_id="demo-holiday",
                status="confirmed",
                summary="Anniversaire de Léa",
                starts_at=None,
                ends_at=None,
                start_date=today + timedelta(days=2),
                end_date=today + timedelta(days=3),
                busy=False,
            ),
        )

    async def list_calendars(self, access_token: str) -> list[RemoteCollection]:
        self._maybe_fail("list_calendars")
        self.access_tokens_seen.append(access_token)
        return list(self.calendars)

    async def list_events(
        self,
        access_token: str,
        calendar_ref: str,
        *,
        time_min: datetime,
        time_max: datetime,
        page: str | None = None,
    ) -> EventPage:
        self.event_calls += 1
        self._maybe_fail("list_events")
        self.access_tokens_seen.append(access_token)
        if calendar_ref not in self.events:
            raise ConnectorGoneError("http_404")

        def overlaps(event: RemoteEvent) -> bool:
            if event.starts_at is not None and event.ends_at is not None:
                return event.starts_at < time_max and event.ends_at > time_min
            assert event.start_date is not None and event.end_date is not None
            return event.start_date < time_max.date() + timedelta(
                days=1
            ) and event.end_date > time_min.date() - timedelta(days=1)

        matching = sorted(
            (e for e in self.events[calendar_ref].values() if overlaps(e)),
            key=lambda e: e.provider_id,
        )
        offset = int(page or 0)
        chunk = matching[offset : offset + self.page_size]
        more = offset + self.page_size < len(matching)
        return EventPage(events=chunk, next_page=str(offset + self.page_size) if more else None)
