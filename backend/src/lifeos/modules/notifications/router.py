import uuid
from datetime import datetime, time
from itertools import pairwise
from typing import Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from lifeos.api.deps import CtxDep
from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.notifications.models import Notification, NotificationPreference
from lifeos.modules.notifications.policy import CONFIGURABLE, CategoryPolicy
from lifeos.modules.notifications.service import DIGEST, due, load_policy, retime_pending

router = APIRouter(prefix="/notifications", tags=["notifications"])

MAX_WINDOWS = 4
MAX_DIGEST_TIMES = 4


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
    # A digest (source_type 'digest'): how many suggestions it groups.
    items_count: int = 0


class NotificationUpdate(BaseModel):
    status: Literal["read", "dismissed"]


class WindowIn(BaseModel):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class CategoryPreference(BaseModel):
    category: Literal["important", "suggestion"]
    enabled: bool = True
    max_per_day: int | None = None
    windows: list[WindowIn] = Field(default_factory=list)
    digest_times: list[str] = Field(default_factory=list)


class PreferencesOut(BaseModel):
    categories: list[CategoryPreference]
    # Read only here: granted or revoked through PATCH /v1/settings (reinforced).
    critical_bypass_quiet_hours: bool


class PreferencesUpdate(BaseModel):
    categories: list[CategoryPreference] = Field(min_length=1, max_length=len(CONFIGURABLE))


def _visible(ctx: CtxDep) -> list[ColumnElement[bool]]:
    return [
        Notification.user_id == ctx.principal.user_id,
        Notification.deliver_after <= utcnow(),
        or_(Notification.space_id.is_(None), Notification.space_id.in_(ctx.principal.space_ids)),
    ]


def _listed(ctx: CtxDep) -> list[ColumnElement[bool]]:
    """What the menu lists: due notifications and digests, not the items of a digest."""
    return [*_visible(ctx), *due(utcnow())]


async def _out(ctx: CtxDep, rows: list[Notification]) -> list[NotificationOut]:
    digests = [n.id for n in rows if n.source_type == DIGEST]
    counts: dict[uuid.UUID, int] = {}
    if digests:
        item = aliased(Notification)
        result = await ctx.db.execute(
            select(item.digest_id, func.count())
            .where(item.digest_id.in_(digests), item.status != "dismissed")
            .group_by(item.digest_id)
        )
        counts = {digest_id: int(count) for digest_id, count in result if digest_id}
    return [
        NotificationOut.model_validate(n).model_copy(update={"items_count": counts.get(n.id, 0)})
        for n in rows
    ]


def _invalid(field: str) -> ProblemError:
    return ProblemError(
        422,
        "notification_preferences_invalid",
        "Invalid notification preferences",
        extra={"field": field},
    )


def _time(value: str, field: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise _invalid(field) from exc
    return parsed


def _validate(pref: CategoryPreference) -> CategoryPreference:
    """Checks one category's rules; times are normalised to HH:MM."""
    prefix = pref.category
    if pref.max_per_day is not None and not 1 <= pref.max_per_day <= 100:
        raise _invalid(f"{prefix}.max_per_day")
    if pref.category == "important":
        if pref.digest_times:
            raise _invalid(f"{prefix}.digest_times")
        if len(pref.windows) > MAX_WINDOWS:
            raise _invalid(f"{prefix}.windows")
        spans = sorted(
            (_time(w.start, f"{prefix}.windows"), _time(w.end, f"{prefix}.windows"))
            for w in pref.windows
        )
        for start, end in spans:
            if start >= end:
                raise _invalid(f"{prefix}.windows")
        for (_, previous_end), (next_start, _) in pairwise(spans):
            if next_start < previous_end:
                raise _invalid(f"{prefix}.windows")
        windows = [WindowIn(start=s.strftime("%H:%M"), end=e.strftime("%H:%M")) for s, e in spans]
        return pref.model_copy(update={"windows": windows})
    if pref.windows:
        raise _invalid(f"{prefix}.windows")
    times = sorted({_time(t, f"{prefix}.digest_times") for t in pref.digest_times})
    # Suggestions always arrive grouped: at least one digest a day.
    if not times or len(times) > MAX_DIGEST_TIMES:
        raise _invalid(f"{prefix}.digest_times")
    return pref.model_copy(update={"digest_times": [t.strftime("%H:%M") for t in times]})


def _preference(category: str, rules: CategoryPolicy) -> CategoryPreference:
    return CategoryPreference(
        category=category,
        enabled=rules.enabled,
        max_per_day=rules.max_per_day,
        windows=[
            WindowIn(start=w.start.strftime("%H:%M"), end=w.end.strftime("%H:%M"))
            for w in rules.windows
        ],
        digest_times=[t.strftime("%H:%M") for t in rules.digest_times],
    )


async def _preferences(ctx: CtxDep) -> PreferencesOut:
    policy = await load_policy(ctx.db, ctx.user)
    return PreferencesOut(
        categories=[_preference(c, policy.category(c)) for c in CONFIGURABLE],
        critical_bypass_quiet_hours=ctx.user.critical_bypass_quiet_hours,
    )


@router.get("/preferences")
async def get_preferences(ctx: CtxDep) -> PreferencesOut:
    return await _preferences(ctx)


@router.put("/preferences")
async def update_preferences(body: PreferencesUpdate, ctx: CtxDep) -> PreferencesOut:
    """Sets the rules of the categories given; fields left out (and the other category)
    keep their value."""
    current = await _preferences(ctx)
    before = {p.category: p for p in current.categories}
    seen: set[str] = set()
    checked: list[CategoryPreference] = []
    for pref in body.categories:
        if pref.category in seen:
            raise _invalid("categories")
        seen.add(pref.category)
        # Fields left out keep their current value.
        given = {name: getattr(pref, name) for name in pref.model_fields_set}
        checked.append(_validate(before[pref.category].model_copy(update=given)))
    changed: list[str] = []
    for pref in checked:
        old = before[pref.category]
        changed += [
            f"{pref.category}.{name}"
            for name in ("enabled", "max_per_day", "windows", "digest_times")
            if getattr(old, name) != getattr(pref, name)
        ]
        values = {
            "enabled": pref.enabled,
            "max_per_day": pref.max_per_day,
            "windows": [w.model_dump() for w in pref.windows],
            "digest_times": pref.digest_times,
            "updated_at": utcnow(),
        }
        await ctx.db.execute(
            insert(NotificationPreference)
            .values(user_id=ctx.principal.user_id, category=pref.category, **values)
            .on_conflict_do_update(
                index_elements=[NotificationPreference.user_id, NotificationPreference.category],
                set_=values,
            )
        )
    counts = await retime_pending(ctx.db, ctx.user) if changed else {}
    await audit.record(
        ctx.db,
        action="notification.preferences.update",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        details={
            "fields": ",".join(changed),
            "retimed": counts.get("retimed", 0),
            "dismissed": counts.get("dismissed", 0),
        },
    )
    return await _preferences(ctx)


@router.get("")
async def list_notifications(
    ctx: CtxDep,
    include_read: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[NotificationOut]:
    stmt = select(Notification).where(*_listed(ctx), Notification.status != "dismissed")
    if not include_read:
        stmt = stmt.where(Notification.status == "pending")
    rows = await ctx.db.scalars(stmt.order_by(Notification.deliver_after.desc()).limit(limit))
    return await _out(ctx, list(rows))


async def _get(ctx: CtxDep, notification_id: uuid.UUID) -> Notification:
    notification = await ctx.db.scalar(
        select(Notification).where(Notification.id == notification_id, *_visible(ctx))
    )
    if notification is None:
        raise not_found("notification_not_found", "Notification not found")
    return notification


@router.get("/{notification_id}/items")
async def list_digest_items(notification_id: uuid.UUID, ctx: CtxDep) -> list[NotificationOut]:
    """The suggestions grouped in a digest, oldest first (dismissed ones left out)."""
    digest = await _get(ctx, notification_id)
    if digest.source_type != DIGEST:
        raise not_found("notification_not_found", "Notification not found")
    rows = await ctx.db.scalars(
        select(Notification)
        .where(
            *_visible(ctx),
            Notification.digest_id == digest.id,
            Notification.status != "dismissed",
        )
        .order_by(Notification.deliver_after, Notification.created_at)
    )
    return await _out(ctx, list(rows))


@router.patch("/{notification_id}")
async def update_notification(
    notification_id: uuid.UUID, body: NotificationUpdate, ctx: CtxDep
) -> NotificationOut:
    notification = await _get(ctx, notification_id)
    now = utcnow()
    notification.status = body.status
    notification.read_at = notification.read_at or now
    if notification.source_type == DIGEST:
        # Reading or dismissing a digest does the same to the suggestions it groups.
        await ctx.db.execute(
            update(Notification)
            .where(
                *_visible(ctx),
                Notification.digest_id == notification.id,
                Notification.status != "dismissed",
            )
            .values(status=body.status, read_at=func.coalesce(Notification.read_at, now))
            .execution_options(synchronize_session=False)
        )
    return (await _out(ctx, [notification]))[0]


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def read_all(ctx: CtxDep) -> None:
    # Listed notifications and the items of digests; never a suggestion still waiting for
    # its digest (it would never be grouped).
    await ctx.db.execute(
        update(Notification)
        .where(
            *_visible(ctx),
            Notification.status == "pending",
            or_(
                Notification.digest_id.is_not(None),
                Notification.category != "suggestion",
                Notification.source_type == DIGEST,
            ),
        )
        .values(status="read", read_at=utcnow())
        .execution_options(synchronize_session=False)
    )
