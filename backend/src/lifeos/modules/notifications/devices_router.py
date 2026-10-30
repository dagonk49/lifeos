"""Web Push routes (ADR 0019): the public key, and the owner's devices.

Included before notifications.router: `/push/config` would otherwise be read as
`/{notification_id}/items`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Header, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from lifeos.api.deps import Auth, CtxDep
from lifeos.core.errors import ProblemError
from lifeos.core.idempotency import idempotent
from lifeos.modules.notifications import devices
from lifeos.modules.notifications.models import PushDevice
from lifeos.modules.notifications.push import PushConfigError, check_config
from lifeos.workers.queue import enqueue

router = APIRouter(prefix="/notifications", tags=["notifications"])


class PushConfigOut(BaseModel):
    enabled: bool
    # applicationServerKey for PushManager.subscribe (base64url, P-256).
    public_key: str


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class DeviceIn(BaseModel):
    """PushSubscription.toJSON() as the browser gives it, plus a label."""

    endpoint: str = Field(min_length=1, max_length=devices.MAX_ENDPOINT)
    keys: SubscriptionKeys
    label: str | None = Field(default=None, max_length=devices.MAX_LABEL)
    previous_endpoint: str | None = Field(default=None, max_length=devices.MAX_ENDPOINT)


class DeviceUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=devices.MAX_LABEL)
    preview: Literal["generic", "full"] | None = None


class DeviceOut(BaseModel):
    """Never the endpoint nor the keys (a capability)."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    label: str
    preview: str
    user_agent: str | None
    created_at: datetime
    consented_at: datetime
    last_success_at: datetime | None
    failure_count: int
    # Registered by the session making this request.
    current: bool = False


class TestQueued(BaseModel):
    queued: bool


def _out(device: PushDevice, session_id: uuid.UUID) -> DeviceOut:
    return DeviceOut.model_validate(device).model_copy(
        update={"current": device.session_id == session_id}
    )


@router.get("/push/config")
async def push_config(ctx: CtxDep) -> PushConfigOut:
    try:
        public_key = check_config(ctx.settings)
    except PushConfigError as exc:  # unreachable: the API checks it at start
        raise ProblemError(503, "push_not_configured", "Push is misconfigured") from exc
    if public_key is None:
        raise ProblemError(503, "push_not_configured", "Push notifications are not configured")
    return PushConfigOut(enabled=True, public_key=public_key)


@router.get("/devices")
async def list_devices(ctx: CtxDep, auth: Auth) -> list[DeviceOut]:
    return [_out(d, auth.session.id) for d in await devices.list_devices(ctx)]


@router.post("/devices", status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceIn,
    request: Request,
    ctx: CtxDep,
    auth: Auth,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Enables Web Push on the calling browser (or renews its subscription)."""

    async def run() -> DeviceOut:
        device = await devices.register_device(
            ctx,
            auth.session,
            devices.Subscription(
                endpoint=body.endpoint,
                p256dh=body.keys.p256dh,
                auth=body.keys.auth,
                label=body.label,
                previous_endpoint=body.previous_endpoint,
            ),
            user_agent=request.headers.get("user-agent"),
        )
        return _out(device, auth.session.id)

    return await idempotent(
        ctx.db,
        user_id=ctx.principal.user_id,
        key=idempotency_key,
        method="POST",
        path="/v1/notifications/devices",
        body=body,
        run=run,
    )


@router.patch("/devices/{device_id}")
async def update_device(
    device_id: uuid.UUID, body: DeviceUpdate, ctx: CtxDep, auth: Auth
) -> DeviceOut:
    device = await devices.update_device(ctx, device_id, label=body.label, preview=body.preview)
    return _out(device, auth.session.id)


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(device_id: uuid.UUID, ctx: CtxDep) -> Response:
    await devices.revoke_device(ctx, device_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/devices/{device_id}/test", status_code=status.HTTP_202_ACCEPTED)
async def test_device(device_id: uuid.UUID, ctx: CtxDep, background: BackgroundTasks) -> TestQueued:
    """Sends a test push through the worker (the periodic job also picks it up within a
    minute if the queue is unreachable)."""
    queued = await devices.request_test(ctx, device_id)
    if queued:
        # After the commit, so that the worker finds the delivery.
        background.add_task(enqueue, devices.SEND_ACTOR, queue=devices.SEND_QUEUE)
    return TestQueued(queued=queued)
