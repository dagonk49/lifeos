"""Web Push senders (ADR 0019): the real one (RFC 8291 + RFC 8292 over httpx), a fake for
tests and end-to-end runs, and what a message holds.

Only the worker sends (it is the process with egress to the push services). Nothing here
logs an endpoint, a key or a payload: the endpoint is a capability, the payload may hold a
title (lock screen preview chosen by the owner).
"""

import json
import uuid
from collections import deque
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.modules.notifications import webpush
from lifeos.modules.notifications.webpush import VapidKey, WebPushError

Urgency = Literal["very-low", "low", "normal", "high"]
Preview = Literal["generic", "full"]

APP_NAME = "LifeOS"
MAX_TITLE = 200
# Generic texts (what the lock screen shows by default): never a title, never a body.
GENERIC_BODIES = {
    "reminder": "Nouveau rappel",
    "task": "Une tâche a changé",
    "digest": "Nouvelles suggestions",
    "connector_account": "Un compte connecté demande votre attention",
    "critical": "Alerte",
    "other": "Nouvelle notification",
}
TEST_BODY = "Notification de test : tout fonctionne."
# Same-origin paths opened when the notification is tapped.
_URLS = {
    "task": "/tasks",
    "digest": "/reviews",
    "review": "/reviews",
    "connector_account": "/settings#connexions",
}


class PushConfigError(ValueError):
    """Web Push chosen without a usable VAPID key: the API refuses to start."""


@dataclass(frozen=True)
class PushMessage:
    endpoint: str
    p256dh: str
    auth: str
    payload: bytes
    ttl: int
    urgency: Urgency
    # Replaces an undelivered message with the same topic at the push service.
    topic: str | None = None


@dataclass(frozen=True)
class PushResult:
    """`status` None: no answer (network error, timeout)."""

    status: int | None
    retry_after: float | None = None
    error: str | None = None


class PushSender(Protocol):
    async def send(self, message: PushMessage) -> PushResult: ...


# --- Payload ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Content:
    """What a notification may show: only what the in-app notification holds."""

    id: uuid.UUID | None
    category: str
    title: str
    source_type: str | None
    dedup_key: str


def generic_body(content: Content) -> str:
    if content.category == "critical":
        return GENERIC_BODIES["critical"]
    if content.source_type == "task":
        return GENERIC_BODIES["reminder" if ":remind:" in content.dedup_key else "task"]
    return GENERIC_BODIES.get(content.source_type or "", GENERIC_BODIES["other"])


def push_url(content: Content) -> str:
    return _URLS.get(content.source_type or "", "/")


def payload(content: Content | None, preview: Preview) -> bytes:
    """The JSON the service worker shows as is (it never calls the API: VPN-only setups).

    Generic by default; with `preview == 'full'` the body is the notification's title. The
    notification's own body is never sent. None: a test push.
    """
    if content is None:
        data = {"title": APP_NAME, "body": TEST_BODY, "url": "/settings", "tag": "lifeos-test"}
    else:
        text = content.title[:MAX_TITLE] if preview == "full" else generic_body(content)
        data = {
            "title": APP_NAME,
            "body": text,
            "url": push_url(content),
            "tag": f"lifeos-{content.id}",
        }
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(encoded) <= webpush.MAX_PLAINTEXT
    return encoded


def topic(notification_id: uuid.UUID | None) -> str | None:
    """RFC 8030 §5.4: at most 32 base64url characters."""
    return notification_id.hex if notification_id else None


# --- Senders ------------------------------------------------------------------------------


def retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header (delay or HTTP date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (when - utcnow()).total_seconds())


class WebPushSender:
    """POSTs the encrypted record to the subscription's endpoint, signed with VAPID."""

    def __init__(
        self,
        key: VapidKey,
        subject: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.key = key
        self.subject = subject
        self._timeout = timeout
        self._transport = transport

    async def send(self, message: PushMessage) -> PushResult:
        body = webpush.encrypt(message.payload, message.p256dh, message.auth)
        headers = {
            "Authorization": webpush.vapid_authorization(self.key, message.endpoint, self.subject),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(message.ttl),
            "Urgency": message.urgency,
        }
        if message.topic:
            headers["Topic"] = message.topic
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False, transport=self._transport
            ) as client:
                response = await client.post(message.endpoint, content=body, headers=headers)
        except httpx.TimeoutException:
            return PushResult(None, error="timeout")
        except httpx.HTTPError:
            return PushResult(None, error="network")
        return PushResult(response.status_code, retry_after(response.headers.get("retry-after")))


@dataclass
class FakePushSender:
    """Records what would be sent (decrypted payload included: tests only); answers 201
    unless a status was scripted with `respond`."""

    sent: list[PushMessage] = field(default_factory=list)
    _script: deque[PushResult] = field(default_factory=deque)

    def respond(self, status: int | None, retry_after: float | None = None) -> None:
        self._script.append(PushResult(status, retry_after, None if status else "timeout"))

    async def send(self, message: PushMessage) -> PushResult:
        self.sent.append(message)
        return self._script.popleft() if self._script else PushResult(201)

    def payloads(self) -> list[dict[str, str]]:
        return [json.loads(m.payload) for m in self.sent]


def load_vapid_key(settings: Settings) -> VapidKey:
    """The configured key (webpush), or a key of this process (fake: nothing is sent)."""
    secret = settings.vapid_private_key.get_secret_value() if settings.vapid_private_key else ""
    if not secret.strip():
        if settings.push_provider == "fake":
            return _fake_key()
        raise PushConfigError(
            "LIFEOS_VAPID_PRIVATE_KEY (secret lifeos_vapid_private_key) is required when "
            "LIFEOS_PUSH_PROVIDER=webpush"
        )
    try:
        return VapidKey.from_pem(secret)
    except WebPushError as exc:
        raise PushConfigError(f"lifeos_vapid_private_key: {exc}") from exc


_FAKE_KEY: VapidKey | None = None


def _fake_key() -> VapidKey:
    global _FAKE_KEY
    if _FAKE_KEY is None:
        _FAKE_KEY = VapidKey.generate()
    return _FAKE_KEY


def check_config(settings: Settings) -> str | None:
    """The public key browsers subscribe with (None: push disabled). Raises
    PushConfigError for a webpush setup without a usable key or subject."""
    if settings.push_provider == "disabled":
        return None
    if not settings.vapid_subject.startswith(("mailto:", "https://")):
        raise PushConfigError("LIFEOS_VAPID_SUBJECT must be a mailto: or https: URL")
    return load_vapid_key(settings).public_key


def build_push_sender(settings: Settings) -> PushSender | None:
    if settings.push_provider == "disabled":
        return None
    if settings.push_provider == "fake":
        return FakePushSender()
    check_config(settings)
    return WebPushSender(
        load_vapid_key(settings), settings.vapid_subject, timeout=settings.push_timeout_seconds
    )


def endpoint_allowed(endpoint: str, allowed_hosts: list[str]) -> bool:
    """HTTPS on the default port, towards one of the push services (or a subdomain)."""
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or port not in (None, 443) or parts.username or parts.password:
        return False
    return any(host == h or host.endswith("." + h) for h in (a.lower() for a in allowed_hosts))
