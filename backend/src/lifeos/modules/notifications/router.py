import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, or_, select, update

from lifeos.api.deps import CtxDep
from lifeos.core.errors import not_found
from lifeos.core.security import utcnow
from lifeos.modules.notifications.models import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID | None
    category: str
    title: str
    body: str | None
    status: str
    source_type: str | None
    source_id: uuid.UUID | None
    deliver_after: datetime
    created_at: datetime
    read_at: datetime | None


class NotificationUpdate(BaseModel):
    status: Literal["read", "dismissed"]


def _visible(ctx: CtxDep) -> list[ColumnElement[bool]]:
    return [
        Notification.user_id == ctx.principal.user_id,
        Notification.deliver_after <= utcnow(),
        or_(Notification.space_id.is_(None), Notification.space_id.in_(ctx.principal.space_ids)),
    ]


@router.get("")
async def list_notifications(
    ctx: CtxDep,
    include_read: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[NotificationOut]:
    stmt = select(Notification).where(*_visible(ctx), Notification.status != "dismissed")
    if not include_read:
        stmt = stmt.where(Notification.status == "pending")
    rows = await ctx.db.scalars(stmt.order_by(Notification.deliver_after.desc()).limit(limit))
    return [NotificationOut.model_validate(n) for n in rows]


@router.patch("/{notification_id}")
async def update_notification(
    notification_id: uuid.UUID, body: NotificationUpdate, ctx: CtxDep
) -> NotificationOut:
    notification = await ctx.db.scalar(
        select(Notification).where(Notification.id == notification_id, *_visible(ctx))
    )
    if notification is None:
        raise not_found("notification_not_found", "Notification not found")
    notification.status = body.status
    notification.read_at = notification.read_at or utcnow()
    return NotificationOut.model_validate(notification)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all(ctx: CtxDep) -> None:
    await ctx.db.execute(
        update(Notification)
        .where(*_visible(ctx), Notification.status == "pending")
        .values(status="read", read_at=utcnow())
        .execution_options(synchronize_session=False)
    )
