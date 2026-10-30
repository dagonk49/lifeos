"""Owner settings and spaces (cloud consent is a reinforced operation)."""

import uuid
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from lifeos.api.deps import CtxDep
from lifeos.core.errors import ProblemError, not_found
from lifeos.modules.audit import service as audit
from lifeos.modules.guardian.actions import authorize
from lifeos.modules.identity.models import Space
from lifeos.modules.notifications.service import retime_pending

# Settings that change when a notification may be delivered.
_TIMING = ("timezone", "quiet_hours_start", "quiet_hours_end", "critical_bypass_quiet_hours")

router = APIRouter(tags=["settings"])


class SpaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    name: str
    cloud_ai_enabled: bool


class SpaceUpdate(BaseModel):
    cloud_ai_enabled: bool


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    display_name: str
    email: str
    locale: str
    timezone: str
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    llm_monthly_token_budget: int
    llm_paused: bool
    critical_bypass_quiet_hours: bool


class SettingsUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    llm_monthly_token_budget: int | None = Field(default=None, ge=0, le=100_000_000)
    # Emergency stop of every cloud call (F08: "arrêt d'urgence").
    llm_paused: bool | None = None
    # F09: critical alerts may go through quiet hours. Granting it is reinforced (recent
    # passkey), withdrawing it is immediate; both are audited.
    critical_bypass_quiet_hours: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("unknown time zone") from exc
        return value


@router.get("/spaces")
async def list_spaces(ctx: CtxDep) -> list[SpaceOut]:
    rows = await ctx.db.scalars(
        select(Space).where(Space.id.in_(ctx.principal.space_ids)).order_by(Space.kind)
    )
    return [SpaceOut.model_validate(s) for s in rows]


@router.patch("/spaces/{space_id}")
async def update_space(space_id: uuid.UUID, body: SpaceUpdate, ctx: CtxDep) -> SpaceOut:
    ctx.principal.require_space(space_id)
    space = await ctx.db.get(Space, space_id)
    if space is None:
        raise not_found("space_not_found", "Space not found")
    if space.cloud_ai_enabled != body.cloud_ai_enabled:
        # Changing what may leave the server is a reinforced operation.
        await authorize(ctx, "space.cloud_consent", space_id)
        space.cloud_ai_enabled = body.cloud_ai_enabled
        await audit.record(
            ctx.db,
            action="space.cloud_consent",
            actor_type="user",
            actor_id=ctx.principal.actor_id,
            user_id=ctx.principal.user_id,
            space_id=space_id,
            target_type="space",
            target_id=space_id,
            details={"enabled": body.cloud_ai_enabled},
        )
    return SpaceOut.model_validate(space)


@router.get("/settings")
async def get_settings(ctx: CtxDep) -> SettingsOut:
    return SettingsOut.model_validate(ctx.user)


@router.patch("/settings")
async def update_settings(body: SettingsUpdate, ctx: CtxDep) -> SettingsOut:
    changes = body.model_dump(exclude_unset=True)
    quiet = {
        k: changes.get(k, getattr(ctx.user, k)) for k in ("quiet_hours_start", "quiet_hours_end")
    }
    if (quiet["quiet_hours_start"] is None) != (quiet["quiet_hours_end"] is None):
        raise ProblemError(
            422, "quiet_hours_incomplete", "Quiet hours need both a start and an end"
        )
    before = {key: getattr(ctx.user, key) for key in _TIMING}
    bypass = changes.pop("critical_bypass_quiet_hours", None)
    if bypass is not None and bypass != ctx.user.critical_bypass_quiet_hours:
        if bypass:
            # Letting alerts through the owner's silence is a consent: reinforced (the
            # refusal is audited by the guardian).
            await authorize(ctx, "notification.critical_bypass", None)
        ctx.user.critical_bypass_quiet_hours = bypass
        await audit.record(
            ctx.db,
            action="notification.critical_bypass",
            actor_type="user",
            actor_id=ctx.principal.actor_id,
            user_id=ctx.principal.user_id,
            details={"enabled": bypass},
        )
    if bypass is not None:
        changes["critical_bypass_quiet_hours"] = bypass
    for key, value in changes.items():
        if key == "critical_bypass_quiet_hours":
            continue
        if key in ("display_name", "timezone", "llm_monthly_token_budget", "llm_paused") and (
            value is None
        ):
            continue
        setattr(ctx.user, key, value)
    if any(getattr(ctx.user, key) != before[key] for key in _TIMING):
        # Notifications not delivered yet follow the new quiet hours, zone or permission.
        await retime_pending(ctx.db, ctx.user)
    await audit.record(
        ctx.db,
        action="settings.update",
        actor_type="user",
        actor_id=ctx.principal.actor_id,
        user_id=ctx.principal.user_id,
        details={"fields": ",".join(sorted(changes))},
    )
    return SettingsOut.model_validate(ctx.user)
