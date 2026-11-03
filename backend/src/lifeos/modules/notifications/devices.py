"""Phones and browsers that receive notifications by Web Push (ADR 0019), and the jobs that
push the due notifications to them.

What is pushed follows `notifications.service.due(now)` (delivered, not inside a digest,
never a suggestion on its own) and the in-app status (only pending notifications); each
(notification, device) pair gets one `push_deliveries` row, the per-device sent marker. A
revoked device gets nothing: no delivery is created for it, and its pending ones are
cancelled at revocation and again when they are claimed.

Endpoints and subscription keys are capabilities: never logged, audited nor exported.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.identity.models import User, UserSession
from lifeos.modules.notifications import webpush
from lifeos.modules.notifications.models import Notification, PushDelivery, PushDevice
from lifeos.modules.notifications.policy import DeliveryPolicy, in_quiet_hours
from lifeos.modules.notifications.push import (
    Content,
    Preview,
    PushMessage,
    PushResult,
    PushSender,
    Urgency,
    endpoint_allowed,
    payload,
    topic,
)
from lifeos.modules.notifications.revocation import revoke
from lifeos.modules.notifications.service import due
from lifeos.observability.metrics import PUSH_SENDS

log = get_logger("lifeos.push")

# Revoked devices and deliveries are kept this long (docs/politique-de-donnees.md §1).
RETENTION = timedelta(days=30)
# A claimed delivery is retried after this long if its run died during the send.
LEASE = timedelta(minutes=2)
BACKOFF_BASE = timedelta(minutes=1)
BACKOFF_MAX = timedelta(hours=1)
# A Retry-After longer than this is capped (the TTL makes later sends useless anyway).
RETRY_AFTER_MAX = timedelta(hours=6)
TEST_TTL = 600
MAX_ENDPOINT = 2048
MAX_LABEL = 100
MAX_USER_AGENT = 300
SEND_ACTOR = "send_push"
SEND_QUEUE = "realtime"
_SENT = frozenset({200, 201, 202})
_GONE = frozenset({404, 410})


def _invalid(code: str, title: str) -> ProblemError:
    return ProblemError(422, code, title)


def check_subscription(settings: Settings, endpoint: str, p256dh: str, auth: str) -> None:
    """Refuses an endpoint outside the push services (the worker would call it) and keys
    that cannot encrypt. The values are never echoed back."""
    if len(endpoint) > MAX_ENDPOINT or not endpoint_allowed(endpoint, settings.push_allowed_hosts):
        raise _invalid("push_endpoint_not_allowed", "Push endpoint not allowed")
    try:
        webpush.load_subscription_key(p256dh)
        webpush.load_auth_secret(auth)
    except webpush.WebPushError as exc:
        raise _invalid("push_subscription_invalid", "Invalid push subscription keys") from exc


def _require_enabled(settings: Settings) -> None:
    if settings.push_provider == "disabled":
        raise ProblemError(503, "push_not_configured", "Push notifications are not configured")


async def _audit(
    ctx: Ctx, action: str, device_id: uuid.UUID, details: dict[str, Any] | None = None
) -> None:
    await audit.record(
        ctx.db,
        action=action,
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        target_type="push_device",
        target_id=device_id,
        details=details,
    )


# --- Owner operations ---------------------------------------------------------------------


@dataclass(frozen=True)
class Subscription:
    endpoint: str
    p256dh: str
    auth: str
    label: str | None = None
    # pushsubscriptionchange: the endpoint this one replaces (an active device only).
    previous_endpoint: str | None = None


async def register_device(
    ctx: Ctx, current: UserSession, data: Subscription, *, user_agent: str | None
) -> PushDevice:
    """Upserts by endpoint: a new device, the same one again (keys and session refreshed),
    or a revoked one enabled again (new consent, generic preview)."""
    settings = ctx.settings
    _require_enabled(settings)
    check_subscription(settings, data.endpoint, data.p256dh, data.auth)
    user_id = ctx.principal.user_id
    now = utcnow()
    agent = user_agent[:MAX_USER_AGENT] if user_agent else None
    link = {"session_id": current.id, "credential_id": current.credential_id}
    keys = {"p256dh": data.p256dh, "auth_secret": data.auth, "user_agent": agent}
    if data.previous_endpoint is not None:
        return await _renew(ctx, data, keys | link)
    existing = await ctx.db.scalar(
        select(PushDevice).where(PushDevice.endpoint == data.endpoint).with_for_update()
    )
    if existing is not None and existing.user_id != user_id:
        # Another account subscribed this browser before: the browser now belongs here.
        await ctx.db.delete(existing)
        await ctx.db.flush()
        existing = None
    if existing is None:
        device_id = await ctx.db.scalar(
            insert(PushDevice)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                label=(data.label or "Appareil")[:MAX_LABEL],
                endpoint=data.endpoint,
                preview="generic",
                consented_at=now,
                failure_count=0,
                created_at=now,
                **keys,
                **link,
            )
            .on_conflict_do_nothing(index_elements=[PushDevice.endpoint])
            .returning(PushDevice.id)
        )
        if device_id is None:  # a concurrent registration of the same endpoint
            raise ProblemError(409, "push_device_conflict", "Registration already in progress")
        device = await ctx.db.get_one(PushDevice, device_id)
        await _audit(ctx, "notification.device.register", device.id, {"reactivated": False})
        return device
    reactivated = existing.revoked_at is not None
    for name, value in (keys | link).items():
        setattr(existing, name, value)
    if data.label:
        existing.label = data.label[:MAX_LABEL]
    if reactivated:
        existing.revoked_at = None
        existing.revoked_reason = None
        existing.preview = "generic"
        existing.consented_at = now
        existing.failure_count = 0
    await _audit(ctx, "notification.device.register", existing.id, {"reactivated": reactivated})
    return existing


async def _renew(ctx: Ctx, data: Subscription, values: dict[str, Any]) -> PushDevice:
    """The browser replaced its subscription: the active device keeps its label, preview
    and consent. Never creates nor re-enables a device (it may have been revoked)."""
    device = await ctx.db.scalar(
        select(PushDevice)
        .where(
            PushDevice.user_id == ctx.principal.user_id,
            PushDevice.endpoint == data.previous_endpoint,
            PushDevice.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if device is None:
        raise not_found("device_not_found", "Device not found")
    if data.endpoint != device.endpoint:
        await ctx.db.execute(
            delete(PushDevice).where(
                PushDevice.endpoint == data.endpoint, PushDevice.id != device.id
            )
        )
        device.endpoint = data.endpoint
    for name, value in values.items():
        setattr(device, name, value)
    device.failure_count = 0
    await _audit(ctx, "notification.device.renew", device.id)
    return device


def _active(user_id: uuid.UUID) -> list[ColumnElement[bool]]:
    return [PushDevice.user_id == user_id, PushDevice.revoked_at.is_(None)]


async def list_devices(ctx: Ctx) -> list[PushDevice]:
    rows = await ctx.db.scalars(
        select(PushDevice)
        .where(*_active(ctx.principal.user_id))
        .order_by(PushDevice.created_at, PushDevice.id)
    )
    return list(rows)


async def get_device(ctx: Ctx, device_id: uuid.UUID) -> PushDevice:
    device = await ctx.db.scalar(
        select(PushDevice).where(PushDevice.id == device_id, *_active(ctx.principal.user_id))
    )
    if device is None:
        raise not_found("device_not_found", "Device not found")
    return device


async def update_device(
    ctx: Ctx, device_id: uuid.UUID, *, label: str | None, preview: Preview | None
) -> PushDevice:
    """Renames a device or changes what its lock screen shows. Showing titles there is a
    consent: reinforced (recent passkey), refusal audited by the guardian."""
    device = await get_device(ctx, device_id)
    changed: list[str] = []
    if preview is not None and preview != device.preview:
        if preview == "full":
            await authorize(ctx, "notification.push_preview", None)
        device.preview = preview
        changed.append("preview")
    if label is not None and label.strip() and label.strip()[:MAX_LABEL] != device.label:
        device.label = label.strip()[:MAX_LABEL]
        changed.append("label")
    if changed:
        await _audit(
            ctx,
            "notification.device.update",
            device.id,
            {"fields": ",".join(changed), "preview": device.preview},
        )
    return device


async def revoke_device(ctx: Ctx, device_id: uuid.UUID) -> None:
    device = await get_device(ctx, device_id)
    await revoke(ctx.db, [PushDevice.id == device.id], "user")
    await _audit(ctx, "notification.device.revoke", device.id, {"reason": "user"})


async def request_test(ctx: Ctx, device_id: uuid.UUID) -> bool:
    """Queues one test push for the worker (the API never calls the push services). One
    pending test per device at most; returns whether a new one was queued."""
    _require_enabled(ctx.settings)
    device = await get_device(ctx, device_id)
    pending = await ctx.db.scalar(
        select(PushDelivery.id).where(
            PushDelivery.device_id == device.id,
            PushDelivery.notification_id.is_(None),
            PushDelivery.status == "pending",
        )
    )
    if pending is not None:
        return False
    now = utcnow()
    ctx.db.add(
        PushDelivery(
            device_id=device.id,
            notification_id=None,
            status="pending",
            attempts=0,
            next_attempt_at=now,
            created_at=now,
        )
    )
    await _audit(ctx, "notification.device.test", device.id)
    return True


# --- Jobs -----------------------------------------------------------------------------------


async def queue_push_deliveries(
    session: AsyncSession, settings: Settings, *, now: datetime | None = None
) -> int:
    """One pending delivery per (due notification, active device), never twice (unique
    pair, ON CONFLICT DO NOTHING). A notification held by quiet hours is queued once its
    `deliver_after` passes; one delivered before the device subscribed, or more than
    `push_max_age_minutes` ago, is not pushed (it stays in the app)."""
    now = now or utcnow()
    oldest = now - timedelta(minutes=settings.push_max_age_minutes)
    candidates: Select[Any] = (
        select(
            func.gen_random_uuid(),
            Notification.id,
            PushDevice.id,
            literal("pending"),
            literal(0),
            literal(now),
            literal(now),
        )
        .select_from(Notification)
        .join(PushDevice, PushDevice.user_id == Notification.user_id)
        .where(
            *due(now),
            Notification.status == "pending",
            Notification.deliver_after >= oldest,
            Notification.deliver_after >= PushDevice.consented_at,
            PushDevice.revoked_at.is_(None),
        )
    )
    result = await session.execute(
        insert(PushDelivery)
        .from_select(
            [
                "id",
                "notification_id",
                "device_id",
                "status",
                "attempts",
                "next_attempt_at",
                "created_at",
            ],
            candidates,
        )
        .on_conflict_do_nothing(constraint="uq_push_deliveries_pair")
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class _Claim:
    delivery_id: uuid.UUID
    device_id: uuid.UUID
    message: PushMessage


def urgency(notification: Notification, user: User, now: datetime) -> Urgency:
    """High only for a critical alert that goes through quiet hours with the owner's
    permission (ADR 0015); everything else is normal (Android may delay it in Doze)."""
    if notification.category != "critical" or not user.critical_bypass_quiet_hours:
        return "normal"
    policy = DeliveryPolicy(
        timezone=user.timezone,
        quiet_start=user.quiet_hours_start,
        quiet_end=user.quiet_hours_end,
        critical_bypass=True,
    )
    return "high" if in_quiet_hours(now, policy) else "normal"


async def _claim(
    session: AsyncSession, settings: Settings, now: datetime, batch: int, counts: dict[str, int]
) -> list[_Claim]:
    rows = (
        await session.execute(
            select(PushDelivery, PushDevice, Notification, User)
            .join(PushDevice, PushDevice.id == PushDelivery.device_id)
            .join(User, User.id == PushDevice.user_id)
            .outerjoin(Notification, Notification.id == PushDelivery.notification_id)
            .where(PushDelivery.status == "pending", PushDelivery.next_attempt_at <= now)
            .order_by(PushDelivery.next_attempt_at, PushDelivery.id)
            .with_for_update(of=PushDelivery, skip_locked=True)
            .limit(batch)
        )
    ).all()
    claims: list[_Claim] = []
    for delivery, device, notification, user in rows:
        if device.revoked_at is not None:
            delivery.status, delivery.error_code = "cancelled", "device_revoked"
            counts["cancelled"] += 1
            continue
        if delivery.notification_id is not None and (
            notification is None or notification.status != "pending"
        ):
            # Read or dismissed in the app meanwhile: nothing to show any more.
            delivery.status, delivery.error_code = "cancelled", "notification_handled"
            counts["cancelled"] += 1
            continue
        delivery.attempts += 1
        delivery.next_attempt_at = now + LEASE
        content: Content | None = None
        level: Urgency = "normal"
        ttl = TEST_TTL
        if notification is not None:
            content = Content(
                id=notification.id,
                category=notification.category,
                title=notification.title,
                source_type=notification.source_type,
                dedup_key=notification.dedup_key,
            )
            level, ttl = urgency(notification, user, now), settings.push_ttl_seconds
        preview: Preview = "full" if device.preview == "full" else "generic"
        claims.append(
            _Claim(
                delivery.id,
                device.id,
                PushMessage(
                    endpoint=device.endpoint,
                    p256dh=device.p256dh,
                    auth=device.auth_secret,
                    payload=payload(content, preview),
                    ttl=ttl,
                    urgency=level,
                    topic=topic(delivery.notification_id),
                ),
            )
        )
    return claims


def _backoff(attempts: int, retry_after: float | None) -> timedelta:
    delay: timedelta = min(BACKOFF_BASE * (2 ** max(attempts - 1, 0)), BACKOFF_MAX)
    if retry_after is not None:
        delay = max(delay, min(timedelta(seconds=retry_after), RETRY_AFTER_MAX))
    return delay


async def _record(
    session: AsyncSession,
    settings: Settings,
    claim: _Claim,
    result: PushResult,
    now: datetime,
    counts: dict[str, int],
) -> None:
    delivery = await session.scalar(
        select(PushDelivery).where(PushDelivery.id == claim.delivery_id).with_for_update()
    )
    if delivery is None or delivery.status != "pending":
        return  # erased with its notification, or cancelled by a revocation meanwhile
    device = await session.get_one(PushDevice, claim.device_id, with_for_update=True)
    status = result.status
    if status in _SENT:
        delivery.status, delivery.sent_at, delivery.error_code = "sent", now, None
        device.last_success_at, device.failure_count = now, 0
        outcome = "sent"
    elif status in _GONE:
        # The subscription no longer exists (uninstalled, permission withdrawn).
        delivery.status, delivery.error_code = "expired", f"http_{status}"
        await revoke(session, [PushDevice.id == device.id], "expired")
        outcome = "expired"
    elif status is None or status == 429 or status >= 500:
        delivery.error_code = result.error or f"http_{status}"
        if delivery.attempts >= settings.push_max_attempts:
            delivery.status = "failed"
            device.failure_count += 1
            outcome = "failed"
        else:
            delivery.next_attempt_at = now + _backoff(delivery.attempts, result.retry_after)
            outcome = "retried"
    else:
        # Refused for good (bad request, VAPID key not accepted, too large).
        delivery.status, delivery.error_code = "failed", f"http_{status}"
        device.failure_count += 1
        outcome = "failed"
    counts[outcome] += 1
    PUSH_SENDS.labels(outcome).inc()


async def send_pending_pushes(
    database: Database, settings: Settings, sender: PushSender, *, batch: int = 100
) -> dict[str, int]:
    """Queues the newly due notifications, then sends the due deliveries: claimed in a
    short locked transaction (SKIP LOCKED, lease), sent outside any transaction, outcome
    written in another one. Audited by counts when anything happened."""
    counts = dict.fromkeys(("queued", "sent", "retried", "failed", "expired", "cancelled"), 0)
    async with database.sessionmaker() as session, session.begin():
        counts["queued"] = await queue_push_deliveries(session, settings)
    async with database.sessionmaker() as session, session.begin():
        claims = await _claim(session, settings, utcnow(), batch, counts)
    gone: set[uuid.UUID] = set()
    for claim in claims:
        if claim.device_id in gone:
            continue  # expired during this run: its deliveries were cancelled with it
        result = await sender.send(claim.message)
        if result.status in _GONE:
            gone.add(claim.device_id)
        async with database.sessionmaker() as session, session.begin():
            await _record(session, settings, claim, result, utcnow(), counts)
    if any(counts.values()):
        async with database.sessionmaker() as session, session.begin():
            await audit.record(
                session,
                action="notification.push.send",
                actor_type="system",
                actor_id="send_push",
                details=dict(counts),
            )
        log.info("push_sent", **counts)
    return counts


async def purge_push(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Deliveries older than RETENTION and devices revoked that long ago (with their
    subscription keys); audited by counts."""
    cutoff = (now or utcnow()) - RETENTION
    deliveries = await session.execute(delete(PushDelivery).where(PushDelivery.created_at < cutoff))
    devices = await session.execute(
        delete(PushDevice).where(
            and_(PushDevice.revoked_at.is_not(None), PushDevice.revoked_at < cutoff)
        )
    )
    counts = {
        "deliveries": int(deliveries.rowcount or 0),  # type: ignore[attr-defined]
        "devices": int(devices.rowcount or 0),  # type: ignore[attr-defined]
    }
    if any(counts.values()):
        await audit.record(
            session,
            action="notification.push.purge",
            actor_type="system",
            actor_id="purge_push",
            details=dict(counts),
        )
    return counts
