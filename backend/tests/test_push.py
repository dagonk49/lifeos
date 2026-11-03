"""Web Push to the owner's phones (task 19, ADR 0019): encryption and VAPID by the RFCs,
devices over the API, what is pushed (and what never is), retries and revocation."""

import base64
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.main import create_app
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.identity import service as identity
from lifeos.modules.identity.models import PasskeyCredential, User, UserSession
from lifeos.modules.llm.fake import FakeProvider
from lifeos.modules.notifications import devices, webpush
from lifeos.modules.notifications.models import Notification, PushDelivery, PushDevice
from lifeos.modules.notifications.push import (
    Content,
    FakePushSender,
    PushConfigError,
    PushMessage,
    WebPushSender,
    build_push_sender,
    check_config,
    endpoint_allowed,
    payload,
    retry_after,
)
from lifeos.modules.notifications.service import build_digests
from lifeos.modules.notifications.webpush import VapidKey, WebPushError

from .conftest import Owner, login

FCM = "https://fcm.googleapis.com/fcm/send/"
SECRET_TITLE = "Rappel : Rendez-vous chez le cardiologue"
SECRET_BODY = "Apporter les résultats d'analyse"


# --- RFC 8291: message encryption --------------------------------------------------------

# RFC 8291 Appendix A.
RFC_PLAINTEXT = b"When I grow up, I want to be a watermelon"
RFC_AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC_UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
RFC_UA_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS"
    "6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulc"
    "y4a-fN"
)


def _private(value: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(
        int.from_bytes(webpush.b64url_decode(value), "big"), ec.SECP256R1()
    )


def test_encryption_matches_the_rfc_8291_test_vector() -> None:
    body = webpush.encrypt(
        RFC_PLAINTEXT,
        RFC_UA_PUBLIC,
        RFC_AUTH,
        sender_key=_private(RFC_AS_PRIVATE),
        salt=webpush.b64url_decode(RFC_SALT),
    )
    assert webpush.b64url_encode(body) == RFC_BODY
    # Header: salt, record size 4096, the sender's key as key id.
    assert body[16:21] == b"\x00\x00\x10\x00\x41"
    assert webpush.decrypt(body, _private(RFC_UA_PRIVATE), RFC_AUTH) == RFC_PLAINTEXT


def test_encryption_is_fresh_every_time_and_refuses_bad_keys() -> None:
    ua = Browser()
    first = webpush.encrypt(b"x", ua.p256dh, ua.auth)
    second = webpush.encrypt(b"x", ua.p256dh, ua.auth)
    assert first[:16] != second[:16] and first[21:86] != second[21:86]  # salt, sender key
    assert ua.decrypt(first) == b"x"
    with pytest.raises(WebPushError):
        webpush.encrypt(b"x" * (webpush.MAX_PLAINTEXT + 1), ua.p256dh, ua.auth)
    with pytest.raises(WebPushError):
        webpush.encrypt(b"x", webpush.b64url_encode(b"\x04" + b"\x01" * 64), ua.auth)
    with pytest.raises(WebPushError):
        webpush.encrypt(b"x", ua.p256dh, webpush.b64url_encode(b"short"))
    # The largest payload still fits in one 4096-byte record.
    assert len(webpush.encrypt(b"x" * webpush.MAX_PLAINTEXT, ua.p256dh, ua.auth)) == 4096


# --- RFC 8292: VAPID ---------------------------------------------------------------------


def test_vapid_token_verifies_with_the_public_key() -> None:
    key = VapidKey.generate()
    now = 1_800_000_000
    token = webpush.vapid_token(key, FCM + "abc?x=1", "mailto:owner@example.org", now=now)
    claims = webpush.verify_token(token, key.public_key)
    assert claims == {
        "aud": "https://fcm.googleapis.com",
        "exp": now + 12 * 3600,
        "sub": "mailto:owner@example.org",
    }
    assert int(claims["exp"]) - now <= webpush.MAX_TOKEN_SECONDS  # type: ignore[call-overload]
    with pytest.raises(WebPushError):
        webpush.verify_token(token, VapidKey.generate().public_key)
    with pytest.raises(WebPushError):
        webpush.vapid_token(key, FCM, "mailto:a@b", ttl=24 * 3600 + 1)
    header = webpush.vapid_authorization(key, FCM + "abc", "mailto:a@b")
    assert header.startswith("vapid t=") and header.endswith(f", k={key.public_key}")
    assert len(webpush.b64url_decode(key.public_key)) == 65


def _pem(curve: ec.EllipticCurve) -> str:
    return (
        ec.generate_private_key(curve)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


def test_vapid_key_must_be_a_p256_pem(settings: Settings) -> None:
    pem = _pem(ec.SECP256R1())
    assert VapidKey.from_pem(pem).public_key
    for bad in (_pem(ec.SECP384R1()), "not a key"):
        with pytest.raises(WebPushError):
            VapidKey.from_pem(bad)
    webpush_settings = settings.model_copy(update={"push_provider": "webpush"})
    with pytest.raises(PushConfigError):
        check_config(webpush_settings)  # no key: the API refuses to start
    configured = webpush_settings.model_copy(update={"vapid_private_key": SecretStr(pem)})
    assert check_config(configured) == VapidKey.from_pem(pem).public_key
    assert isinstance(build_push_sender(configured), WebPushSender)
    with pytest.raises(PushConfigError):
        check_config(configured.model_copy(update={"vapid_subject": "owner@example.org"}))
    assert check_config(settings) is None and build_push_sender(settings) is None


async def test_the_sender_posts_an_encrypted_signed_message() -> None:
    key = VapidKey.generate()
    ua = Browser()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    sender = WebPushSender(key, "mailto:o@example.org", transport=httpx.MockTransport(handler))
    message = PushMessage(
        endpoint=ua.endpoint,
        p256dh=ua.p256dh,
        auth=ua.auth,
        payload=b'{"title":"LifeOS"}',
        ttl=3600,
        urgency="high",
        topic=uuid.UUID(int=7).hex,
    )
    result = await sender.send(message)
    assert result.status == 201
    [request] = seen
    assert str(request.url) == ua.endpoint
    headers = request.headers
    assert headers["content-encoding"] == "aes128gcm"
    assert (headers["ttl"], headers["urgency"], headers["topic"]) == (
        "3600",
        "high",
        uuid.UUID(int=7).hex,
    )
    token = headers["authorization"].removeprefix("vapid t=").split(",")[0]
    assert webpush.verify_token(token, key.public_key)["aud"] == "https://fcm.googleapis.com"
    # The push service sees only the encrypted record.
    assert b"LifeOS" not in request.content
    assert ua.decrypt(request.content) == b'{"title":"LifeOS"}'

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    timeout = await WebPushSender(key, "mailto:o@x", transport=httpx.MockTransport(slow)).send(
        message
    )
    assert (timeout.status, timeout.error) == (None, "timeout")

    def busy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"})

    limited = await WebPushSender(key, "mailto:o@x", transport=httpx.MockTransport(busy)).send(
        message
    )
    assert (limited.status, limited.retry_after) == (429, 120.0)


def test_retry_after_and_endpoint_allow_list() -> None:
    assert retry_after("30") == 30.0
    assert retry_after(None) is None and retry_after("soon") is None
    later = (utcnow() + timedelta(minutes=5)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 200 < (retry_after(later) or 0) <= 300
    allowed = ["fcm.googleapis.com", "push.apple.com"]
    assert endpoint_allowed(FCM + "x", allowed)
    assert endpoint_allowed("https://web.push.apple.com/abc", allowed)
    for bad in (
        "http://fcm.googleapis.com/x",
        "https://fcm.googleapis.com:8443/x",
        "https://evil.example/x",
        "https://fcm.googleapis.com.evil.example/x",
        "https://user:pw@fcm.googleapis.com/x",
        "https://10.0.0.1/x",
        "file:///etc/passwd",
    ):
        assert not endpoint_allowed(bad, allowed), bad


# --- Payload: what the lock screen may show ----------------------------------------------


def _content(**fields: Any) -> Content:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "category": "important",
        "title": SECRET_TITLE,
        "source_type": "task",
        "dedup_key": f"task:{uuid.uuid4()}:remind:2026",
    }
    return Content(**(values | fields))


def test_generic_payload_never_holds_the_title_full_adds_only_the_title() -> None:
    content = _content()
    generic = json.loads(payload(content, "generic"))
    assert generic == {
        "title": "LifeOS",
        "body": "Nouveau rappel",
        "url": "/tasks",
        "tag": f"lifeos-{content.id}",
    }
    full = json.loads(payload(content, "full"))
    assert full["body"] == SECRET_TITLE and full["title"] == "LifeOS"
    assert set(full) == {"title", "body", "url", "tag"}
    digest = json.loads(payload(_content(category="suggestion", source_type="digest"), "generic"))
    assert (digest["body"], digest["url"]) == ("Nouvelles suggestions", "/reviews")
    critical = json.loads(payload(_content(category="critical", source_type=None), "generic"))
    assert (critical["body"], critical["url"]) == ("Alerte", "/")
    long = json.loads(payload(_content(title="x" * 5000), "full"))
    assert len(long["body"]) == 200
    assert json.loads(payload(None, "full"))["url"] == "/settings"


# --- Fixtures ------------------------------------------------------------------------------


class Browser:
    """A browser's push subscription (its private key decrypts what it receives)."""

    def __init__(self, endpoint: str | None = None) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        raw = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.p256dh = webpush.b64url_encode(raw)
        self.auth = webpush.b64url_encode(os.urandom(16))
        self.endpoint = endpoint or FCM + base64.urlsafe_b64encode(os.urandom(12)).decode()

    def subscription(self, **extra: Any) -> dict[str, Any]:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}} | (
            extra
        )

    def decrypt(self, body: bytes) -> bytes:
        return webpush.decrypt(body, self.key, self.auth)


@pytest.fixture
def push_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"push_provider": "fake"})


@pytest.fixture
async def app(push_settings: Settings, fake_llm: FakeProvider) -> AsyncIterator[FastAPI]:
    application = create_app(push_settings)
    application.state.llm = fake_llm
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def sender() -> FakePushSender:
    return FakePushSender()


async def register(client: AsyncClient, browser: Browser, **extra: Any) -> dict[str, Any]:
    response = await client.post(
        "/v1/notifications/devices",
        json=browser.subscription(**extra),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def notification(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    deliver_after: datetime | None = None,
    category: str = "important",
    title: str = SECRET_TITLE,
    source_type: str | None = "task",
    status: str = "pending",
) -> Notification:
    now = utcnow()
    row = Notification(
        user_id=user_id,
        category=category,
        title=title,
        body=SECRET_BODY,
        dedup_key=f"task:{uuid.uuid4()}:remind:{now.isoformat()}",
        deliver_after=deliver_after or now,
        status=status,
        source_type=source_type,
        source_id=uuid.uuid4(),
        created_at=now,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def deliveries(db: AsyncSession) -> list[PushDelivery]:
    db.expire_all()
    return list(await db.scalars(select(PushDelivery).order_by(PushDelivery.created_at)))


async def run(database: Database, settings: Settings, sender: FakePushSender) -> dict[str, int]:
    return await devices.send_pending_pushes(database, settings, sender)


async def make_due(db: AsyncSession) -> None:
    await db.execute(
        update(PushDelivery)
        .where(PushDelivery.status == "pending")
        .values(next_attempt_at=utcnow() - timedelta(seconds=1))
    )
    await db.commit()


# --- Devices over the API --------------------------------------------------------------------


async def test_push_config_is_503_when_disabled(
    settings: Settings, fake_llm: FakeProvider, owner: Owner
) -> None:
    from httpx import ASGITransport

    from .conftest import ORIGIN, enroll

    application = create_app(settings)
    application.state.llm = fake_llm
    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
            headers={"Origin": ORIGIN},
        ) as client,
    ):
        await enroll(client, owner)
        response = await client.get("/v1/notifications/push/config")
        assert response.status_code == 503
        assert response.json()["code"] == "push_not_configured"
        refused = await client.post("/v1/notifications/devices", json=Browser().subscription())
        assert refused.status_code == 503


async def test_register_list_update_revoke_a_device(
    client: AsyncClient, logged_in: Owner, db: AsyncSession
) -> None:
    config = (await client.get("/v1/notifications/push/config")).json()
    assert config["enabled"] is True and len(webpush.b64url_decode(config["public_key"])) == 65

    browser = Browser()
    device = await register(client, browser, label="Pixel 9")
    assert device["label"] == "Pixel 9" and device["preview"] == "generic"
    assert device["current"] is True and device["user_agent"] == "pytest"
    assert "endpoint" not in device and "keys" not in device and "p256dh" not in device
    # Same subscription again: the same device (upsert by endpoint).
    again = await register(client, browser)
    assert again["id"] == device["id"] and again["label"] == "Pixel 9"
    listed = (await client.get("/v1/notifications/devices")).json()
    assert [d["id"] for d in listed] == [device["id"]]

    renamed = await client.patch(
        f"/v1/notifications/devices/{device['id']}", json={"label": "Téléphone"}
    )
    assert renamed.status_code == 200 and renamed.json()["label"] == "Téléphone"
    full = await client.patch(f"/v1/notifications/devices/{device['id']}", json={"preview": "full"})
    assert full.status_code == 200 and full.json()["preview"] == "full"

    deleted = await client.delete(f"/v1/notifications/devices/{device['id']}")
    assert deleted.status_code == 204
    assert (await client.get("/v1/notifications/devices")).json() == []
    gone = await client.delete(f"/v1/notifications/devices/{device['id']}")
    assert gone.status_code == 404 and gone.json()["code"] == "device_not_found"
    row = await db.get_one(PushDevice, uuid.UUID(device["id"]))
    assert row.revoked_reason == "user"

    # Enabled again on the same browser: a new consent, back to the generic preview.
    back = await register(client, browser)
    assert back["id"] == device["id"] and back["preview"] == "generic"

    # The audit never holds the endpoint nor the keys (capability).
    events = list(await db.scalars(select(AuditEvent)))
    actions = {e.action for e in events}
    assert {"notification.device.register", "notification.device.update"} <= actions
    assert "notification.device.revoke" in actions
    dumped = json.dumps([[e.action, e.details] for e in events])
    for secret in (browser.endpoint, browser.p256dh, browser.auth, "fcm.googleapis.com"):
        assert secret not in dumped


async def test_showing_titles_on_the_lock_screen_is_reinforced(
    client: AsyncClient, logged_in: Owner, db: AsyncSession
) -> None:
    device = await register(client, Browser())
    await db.execute(update(UserSession).values(authenticated_at=utcnow() - timedelta(hours=1)))
    await db.commit()
    refused = await client.patch(
        f"/v1/notifications/devices/{device['id']}", json={"preview": "full"}
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "reauthentication_required"
    # Back to generic, or a new name: no passkey needed.
    ok = await client.patch(f"/v1/notifications/devices/{device['id']}", json={"label": "A"})
    assert ok.status_code == 200 and ok.json()["preview"] == "generic"


async def test_subscriptions_are_checked_and_never_echoed(
    client: AsyncClient, logged_in: Owner
) -> None:
    browser = Browser("https://evil.example/collect")
    response = await client.post("/v1/notifications/devices", json=browser.subscription())
    assert response.status_code == 422 and response.json()["code"] == "push_endpoint_not_allowed"
    assert "evil.example" not in response.text
    bad = Browser()
    bad.p256dh = webpush.b64url_encode(b"\x04" + b"\x00" * 64)
    response = await client.post("/v1/notifications/devices", json=bad.subscription())
    assert response.status_code == 422 and response.json()["code"] == "push_subscription_invalid"
    # The Origin check of every unsafe request applies.
    forged = await client.post(
        "/v1/notifications/devices",
        json=Browser().subscription(),
        headers={"Origin": "https://evil.example"},
    )
    assert forged.status_code == 403 and forged.json()["code"] == "origin_mismatch"
    client.cookies.clear()
    assert (await client.get("/v1/notifications/devices")).status_code == 401


async def test_another_users_device_is_not_found(
    client: AsyncClient, logged_in: Owner, db: AsyncSession
) -> None:
    other = User(email="autre@example.org", display_name="Autre")
    db.add(other)
    await db.flush()
    foreign = PushDevice(
        user_id=other.id,
        label="Autre",
        endpoint=FCM + "other",
        p256dh=Browser().p256dh,
        auth_secret=Browser().auth,
        consented_at=utcnow(),
        created_at=utcnow(),
    )
    db.add(foreign)
    await db.flush()
    foreign_id = foreign.id
    await db.commit()
    for method, path, body in (
        ("PATCH", f"/v1/notifications/devices/{foreign_id}", {"label": "x"}),
        ("DELETE", f"/v1/notifications/devices/{foreign_id}", None),
        ("POST", f"/v1/notifications/devices/{foreign_id}/test", None),
    ):
        response = await client.request(method, path, json=body)
        assert response.status_code == 404, path
        assert response.json()["code"] == "device_not_found"
    assert (await client.get("/v1/notifications/devices")).json() == []
    db.expire_all()
    assert (await db.get_one(PushDevice, foreign_id)).revoked_at is None


async def test_a_renewed_subscription_keeps_the_device_never_revives_it(
    client: AsyncClient, logged_in: Owner
) -> None:
    old, new = Browser(), Browser()
    device = await register(client, old, label="Pixel")
    renewed = await register(client, new, previous_endpoint=old.endpoint)
    assert renewed["id"] == device["id"] and renewed["label"] == "Pixel"
    await client.delete(f"/v1/notifications/devices/{device['id']}")
    # The service worker renews after a revocation from another device: refused.
    response = await client.post(
        "/v1/notifications/devices",
        json=Browser().subscription(previous_endpoint=new.endpoint),
    )
    assert response.status_code == 404 and response.json()["code"] == "device_not_found"
    assert (await client.get("/v1/notifications/devices")).json() == []


# --- What is pushed ----------------------------------------------------------------------------


async def test_due_notifications_are_pushed_once_per_device(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    phone, laptop = Browser(), Browser()
    await register(client, phone)
    await register(client, laptop)
    n = await notification(db, logged_in.user.id)
    async with database.sessionmaker() as session, session.begin():
        assert await devices.queue_push_deliveries(session, push_settings) == 2
    async with database.sessionmaker() as session, session.begin():
        assert await devices.queue_push_deliveries(session, push_settings) == 0  # no duplicate
    counts = await run(database, push_settings, sender)
    assert counts["sent"] == 2
    assert {m.endpoint for m in sender.sent} == {phone.endpoint, laptop.endpoint}
    for message in sender.sent:
        data = json.loads(message.payload)
        assert data["body"] == "Nouveau rappel" and data["tag"] == f"lifeos-{n.id}"
        assert SECRET_TITLE not in message.payload.decode()
        assert SECRET_BODY not in message.payload.decode()
        assert (message.urgency, message.topic, message.ttl) == (
            "normal",
            n.id.hex,
            push_settings.push_ttl_seconds,
        )
    assert {d.status for d in await deliveries(db)} == {"sent"}
    # Nothing new: nothing sent again.
    assert (await run(database, push_settings, sender))["sent"] == 0
    assert len(sender.sent) == 2
    for device in await db.scalars(select(PushDevice)):
        assert device.last_success_at is not None
    events = list(await db.scalars(select(AuditEvent).where(AuditEvent.action.like("%push%"))))
    assert events and phone.endpoint not in json.dumps([e.details for e in events])


async def test_full_preview_shows_the_title_never_the_body(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    device = await register(client, Browser())
    await client.patch(f"/v1/notifications/devices/{device['id']}", json={"preview": "full"})
    await notification(db, logged_in.user.id)
    await run(database, push_settings, sender)
    [message] = sender.sent
    assert json.loads(message.payload)["body"] == SECRET_TITLE
    assert SECRET_BODY not in message.payload.decode()


async def test_held_by_quiet_hours_pushed_only_when_due(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    await register(client, Browser())
    held = await notification(db, logged_in.user.id, deliver_after=utcnow() + timedelta(hours=2))
    held_id = held.id
    await run(database, push_settings, sender)
    assert sender.sent == [] and await deliveries(db) == []
    await db.execute(
        update(Notification).where(Notification.id == held_id).values(deliver_after=utcnow())
    )
    await db.commit()
    assert (await run(database, push_settings, sender))["sent"] == 1


async def test_old_suggestions_and_handled_notifications_are_not_pushed(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    before = utcnow() - timedelta(minutes=1)
    await register(client, Browser())
    user_id = logged_in.user.id
    # Delivered before the device subscribed, or too old: in-app only.
    await notification(db, user_id, deliver_after=before)
    await notification(db, user_id, status="read")
    # Suggestions are never pushed one by one: only their digest is.
    for _ in range(2):
        await notification(db, user_id, category="suggestion", source_type="review")
    await run(database, push_settings, sender)
    assert sender.sent == []
    async with database.sessionmaker() as session, session.begin():
        assert (await build_digests(session))["digests"] == 1
    await run(database, push_settings, sender)
    [message] = sender.sent
    assert json.loads(message.payload)["body"] == "Nouvelles suggestions"
    digest = await db.scalar(select(Notification).where(Notification.source_type == "digest"))
    assert digest is not None and message.topic == digest.id.hex


async def test_a_read_notification_is_cancelled_before_sending(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    await register(client, Browser())
    n = await notification(db, logged_in.user.id)
    async with database.sessionmaker() as session, session.begin():
        await devices.queue_push_deliveries(session, push_settings)
    read = await client.patch(f"/v1/notifications/{n.id}", json={"status": "read"})
    assert read.status_code == 200
    counts = await run(database, push_settings, sender)
    assert counts["cancelled"] == 1 and sender.sent == []
    [delivery] = await deliveries(db)
    assert (delivery.status, delivery.error_code) == ("cancelled", "notification_handled")


async def test_a_revoked_device_gets_nothing(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    device = await register(client, Browser())
    await notification(db, logged_in.user.id)
    async with database.sessionmaker() as session, session.begin():
        assert await devices.queue_push_deliveries(session, push_settings) == 1
    await client.delete(f"/v1/notifications/devices/{device['id']}")
    [pending] = await deliveries(db)
    assert (pending.status, pending.error_code) == ("cancelled", "device_revoked")
    await notification(db, logged_in.user.id)
    counts = await run(database, push_settings, sender)
    assert sender.sent == [] and counts["queued"] == 0
    assert len(await deliveries(db)) == 1
    # A test push is refused too.
    response = await client.post(f"/v1/notifications/devices/{device['id']}/test")
    assert response.status_code == 404


async def test_a_delivery_claimed_after_revocation_is_cancelled(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    device = await register(client, Browser())
    await notification(db, logged_in.user.id)
    async with database.sessionmaker() as session, session.begin():
        await devices.queue_push_deliveries(session, push_settings)
    # Revoked behind the service's back (no cancellation): the claim still checks.
    await db.execute(
        update(PushDevice)
        .where(PushDevice.id == uuid.UUID(device["id"]))
        .values(revoked_at=utcnow(), revoked_reason="user")
    )
    await db.commit()
    assert (await run(database, push_settings, sender))["cancelled"] == 1
    assert sender.sent == []


# --- Push service answers ----------------------------------------------------------------------


async def test_gone_subscription_expires_the_device(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    device = await register(client, Browser())
    await notification(db, logged_in.user.id)
    await notification(db, logged_in.user.id)
    sender.respond(410)
    counts = await run(database, push_settings, sender)
    # The second message is not sent to a subscription known to be gone.
    assert counts["expired"] == 1 and len(sender.sent) == 1
    statuses = sorted(d.status for d in await deliveries(db))
    assert statuses == ["cancelled", "expired"]
    row = await db.get_one(PushDevice, uuid.UUID(device["id"]))
    assert row.revoked_reason == "expired" and row.revoked_at is not None
    assert (await client.get("/v1/notifications/devices")).json() == []


async def test_rate_limit_is_honoured_and_errors_retried_then_failed(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    await register(client, Browser())
    await notification(db, logged_in.user.id)
    sender.respond(429, retry_after=900)
    start = utcnow()
    assert (await run(database, push_settings, sender))["retried"] == 1
    [delivery] = await deliveries(db)
    assert delivery.status == "pending" and delivery.error_code == "http_429"
    assert delivery.next_attempt_at >= start + timedelta(seconds=899)
    # Not due yet: nothing sent.
    await run(database, push_settings, sender)
    assert len(sender.sent) == 1

    settings = push_settings.model_copy(update={"push_max_attempts": 4})
    for status in (503, None):
        await make_due(db)
        sender.respond(status)
        assert (await run(database, settings, sender))["retried"] == 1
    [delivery] = await deliveries(db)
    assert (delivery.attempts, delivery.error_code) == (3, "timeout")
    assert delivery.next_attempt_at > utcnow() + timedelta(minutes=3)  # 4 min backoff
    await make_due(db)
    sender.respond(500)
    assert (await run(database, settings, sender))["failed"] == 1
    [delivery] = await deliveries(db)
    assert (delivery.status, delivery.attempts, delivery.error_code) == ("failed", 4, "http_500")
    await make_due(db)
    await run(database, settings, sender)
    assert len(sender.sent) == 4  # never again
    device = await db.scalar(select(PushDevice))
    assert device is not None and device.failure_count == 1 and device.revoked_at is None


async def test_backoff_grows_and_is_bounded() -> None:
    assert devices._backoff(1, None) == timedelta(minutes=1)
    assert devices._backoff(3, None) == timedelta(minutes=4)
    assert devices._backoff(20, None) == devices.BACKOFF_MAX
    assert devices._backoff(1, 600) == timedelta(minutes=10)
    assert devices._backoff(1, 10**9) == devices.RETRY_AFTER_MAX


async def test_urgency_is_high_only_for_a_critical_alert_through_quiet_hours(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    await register(client, Browser())
    now = utcnow()
    # Quiet hours around now (UTC owner).
    quiet = {
        "timezone": "UTC",
        "quiet_hours_start": (now - timedelta(hours=1)).time().replace(microsecond=0),
        "quiet_hours_end": (now + timedelta(hours=1)).time().replace(microsecond=0),
    }
    await db.execute(
        update(User)
        .where(User.id == logged_in.user.id)
        .values(**quiet, critical_bypass_quiet_hours=True)
    )
    await db.commit()
    await notification(db, logged_in.user.id, category="critical", source_type=None)
    await notification(db, logged_in.user.id)
    await run(database, push_settings, sender)
    urgencies = sorted(m.urgency for m in sender.sent)
    assert urgencies == ["high", "normal"]
    [critical] = [m for m in sender.sent if m.urgency == "high"]
    assert json.loads(critical.payload)["body"] == "Alerte"

    # Without the permission (or outside quiet hours), normal like everything else.
    user = await db.get_one(User, logged_in.user.id)
    n = Notification(category="critical")
    assert devices.urgency(n, user, now) == "high"
    user.critical_bypass_quiet_hours = False
    assert devices.urgency(n, user, now) == "normal"
    user.critical_bypass_quiet_hours = True
    assert devices.urgency(n, user, now + timedelta(hours=3)) == "normal"
    assert devices.urgency(Notification(category="important"), user, now) == "normal"
    await db.rollback()


async def test_a_test_push_goes_through_the_worker(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    from lifeos.workers.broker import broker

    def drain() -> list[str]:
        import dramatiq

        queue = broker.queues.get("realtime")  # type: ignore[attr-defined]
        names = []
        while queue is not None and not queue.empty():
            message = dramatiq.Message.decode(queue.get_nowait())  # type: ignore[var-annotated]
            names.append(message.actor_name)
        return names

    drain()
    browser = Browser()
    device = await register(client, browser)
    response = await client.post(f"/v1/notifications/devices/{device['id']}/test")
    assert response.status_code == 202 and response.json() == {"queued": True}
    # One pending test at a time.
    again = await client.post(f"/v1/notifications/devices/{device['id']}/test")
    assert again.json() == {"queued": False}
    assert drain() == ["send_push"]
    await run(database, push_settings, sender)
    [message] = sender.sent
    assert json.loads(message.payload)["tag"] == "lifeos-test"
    assert message.topic is None and message.ttl == devices.TEST_TTL


# --- Revocation with sessions and passkeys -----------------------------------------------------


async def _device_reasons(db: AsyncSession) -> list[str | None]:
    db.expire_all()
    return [d.revoked_reason for d in await db.scalars(select(PushDevice))]


async def test_revoking_the_session_revokes_its_device(
    client: AsyncClient, logged_in: Owner, db: AsyncSession
) -> None:
    await register(client, Browser())
    # A re-authentication (passkey prompt for a reinforced operation) keeps it.
    await login(client, logged_in.authenticator)
    [device] = (await client.get("/v1/notifications/devices")).json()
    assert device["current"] is True
    assert await _device_reasons(db) == [None]
    assert (await client.post("/v1/auth/logout")).status_code == 204
    assert await _device_reasons(db) == ["logout"]


async def test_revoking_other_sessions_revokes_their_devices(
    client: AsyncClient, logged_in: Owner, db: AsyncSession
) -> None:
    await register(client, Browser())
    current = await db.scalar(select(UserSession))
    assert current is not None
    # A phone whose session is gone (purged), and one with another open session.
    other = UserSession(
        user_id=logged_in.user.id,
        token_hash=os.urandom(32),
        authenticated_at=utcnow(),
        last_seen_at=utcnow(),
        expires_at=utcnow() + timedelta(days=1),
        created_at=utcnow(),
    )
    db.add(other)
    await db.flush()
    for session_id in (None, other.id):
        b = Browser()
        db.add(
            PushDevice(
                user_id=logged_in.user.id,
                session_id=session_id,
                label="Autre",
                endpoint=b.endpoint,
                p256dh=b.p256dh,
                auth_secret=b.auth,
                consented_at=utcnow(),
                created_at=utcnow(),
            )
        )
    await db.commit()
    response = await client.post("/v1/auth/sessions/revoke-others")
    assert response.status_code == 200
    listed = (await client.get("/v1/notifications/devices")).json()
    assert len(listed) == 1 and listed[0]["current"] is True
    assert sorted(r or "" for r in await _device_reasons(db)) == ["", "others", "others"]

    # One session revoked from the list: its device with it.
    b = Browser()
    third = UserSession(
        user_id=logged_in.user.id,
        token_hash=os.urandom(32),
        authenticated_at=utcnow(),
        last_seen_at=utcnow(),
        expires_at=utcnow() + timedelta(days=1),
        created_at=utcnow(),
    )
    db.add(third)
    await db.flush()
    db.add(
        PushDevice(
            user_id=logged_in.user.id,
            session_id=third.id,
            label="Tablette",
            endpoint=b.endpoint,
            p256dh=b.p256dh,
            auth_secret=b.auth,
            consented_at=utcnow(),
            created_at=utcnow(),
        )
    )
    await db.commit()
    assert (await client.delete(f"/v1/auth/sessions/{third.id}")).status_code == 204
    revoked = await db.scalar(select(PushDevice).where(PushDevice.label == "Tablette"))
    assert revoked is not None and revoked.revoked_reason == "session"


async def test_revoking_a_passkey_or_every_session_revokes_devices(
    client: AsyncClient, logged_in: Owner, db: AsyncSession, database: Database
) -> None:
    await register(client, Browser())
    passkey = PasskeyCredential(
        user_id=logged_in.user.id,
        credential_id=os.urandom(16),
        public_key=b"k",
        sign_count=0,
        transports=[],
        aaguid="0" * 32,
        backup_eligible=False,
        backup_state=False,
        label="Tablette",
        created_at=utcnow(),
    )
    db.add(passkey)
    await db.flush()
    b = Browser()
    db.add(
        PushDevice(
            user_id=logged_in.user.id,
            credential_id=passkey.id,
            label="Tablette",
            endpoint=b.endpoint,
            p256dh=b.p256dh,
            auth_secret=b.auth,
            consented_at=utcnow(),
            created_at=utcnow(),
        )
    )
    await db.commit()
    async with database.sessionmaker() as session, session.begin():
        await identity.revoke_passkey(session, user_id=logged_in.user.id, passkey_id=passkey.id)
    assert sorted(r or "" for r in await _device_reasons(db)) == ["", "passkey"]
    # lifeos-admin revoke-sessions (a compromise): every device.
    async with database.sessionmaker() as session, session.begin():
        await identity.revoke_all_sessions(session, user_id=logged_in.user.id, reason="cli")
    assert sorted(r or "" for r in await _device_reasons(db)) == ["all", "passkey"]


# --- Retention, export, schedule ---------------------------------------------------------------


async def test_old_deliveries_and_revoked_devices_are_purged(
    client: AsyncClient,
    logged_in: Owner,
    db: AsyncSession,
    database: Database,
    push_settings: Settings,
    sender: FakePushSender,
) -> None:
    kept = await register(client, Browser())
    dropped = await register(client, Browser())
    await notification(db, logged_in.user.id)
    await run(database, push_settings, sender)
    await client.delete(f"/v1/notifications/devices/{dropped['id']}")
    later = utcnow() + devices.RETENTION + timedelta(minutes=1)
    async with database.sessionmaker() as session, session.begin():
        counts = await devices.purge_push(session, now=later)
    assert counts == {"deliveries": 2, "devices": 1}
    db.expire_all()
    remaining = list(await db.scalars(select(PushDevice.id)))
    assert remaining == [uuid.UUID(kept["id"])]
    assert await db.scalar(select(func.count()).select_from(PushDelivery)) == 0


async def test_the_export_lists_devices_without_their_subscription(
    client: AsyncClient, logged_in: Owner
) -> None:
    browser = Browser()
    await register(client, browser, label="Pixel")
    await login(client, logged_in.authenticator)  # export: recent passkey
    response = await client.get("/v1/privacy/export")
    assert response.status_code == 200
    [device] = response.json()["push_devices"]
    assert device["label"] == "Pixel"
    assert not {"endpoint", "p256dh", "auth_secret"} & set(device)
    for secret in (browser.endpoint, browser.p256dh, browser.auth):
        assert secret not in response.text


def test_the_scheduler_sends_pushes_every_minute(push_settings: Settings) -> None:
    from unittest.mock import patch

    from lifeos.workers.scheduler import build_scheduler

    with patch("lifeos.workers.scheduler.get_settings", return_value=push_settings):
        jobs = {job.id: job for job in build_scheduler().get_jobs()}
    assert jobs["send_push"].trigger.interval == timedelta(minutes=1)
    assert "purge_push_records" in jobs
    assert "send_push" not in {job.id for job in build_scheduler().get_jobs()}


def test_rfc_values_are_consistent() -> None:
    # The key id of the RFC's body is the sender's public key.
    sender = _private(RFC_AS_PRIVATE).public_key()
    raw = sender.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert webpush.b64url_decode(RFC_BODY)[21:86] == raw
