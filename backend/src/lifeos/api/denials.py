"""Audit of the refusals seen at the HTTP layer (docs/recette-securite.md).

Each refusal is enforced on every request, logged and counted (`lifeos_security_events_total`).
Its audit event is limited with Redis (SET NX EX): a robot, or a stolen cookie replayed in
a loop, must not flood the append-only audit trail. Without Redis the event is skipped,
never the refusal.

The audit lives here, not in lifeos.core, which does not import the business modules.
"""

import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from lifeos.core.errors import problem_response
from lifeos.core.security import hash_token
from lifeos.db.session import Database
from lifeos.logging import get_logger
from lifeos.modules.audit import service as audit
from lifeos.modules.guardian.principal import SpaceAccessDeniedError
from lifeos.modules.identity.models import UserSession
from lifeos.modules.identity.service import SessionCheck
from lifeos.observability.metrics import SECURITY_EVENTS

log = get_logger("lifeos.security")

_KEY_PREFIX = "lifeos:audit:"
# One audited cross-site write per minute; one foreign space per user per minute; one
# rejected or moved session per session per hour.
ORIGIN_WINDOW = 60
SPACE_WINDOW = 60
SESSION_WINDOW = 3600
# One audited refusal of a reinforced operation per session and action per minute.
REAUTH_WINDOW = 60
_AREA_RE = re.compile(r"[^a-z0-9_-]")


async def first_in_window(redis: Redis, key: str, seconds: int) -> bool:
    """True for the first call per window and key: that one is audited."""
    try:
        return bool(await redis.set(_KEY_PREFIX + key, 1, nx=True, ex=seconds))
    except RedisError as exc:
        log.warning("audit_limiter_unavailable", error_type=type(exc).__name__)
        return False


async def _record_limited(
    database: Database, redis: Redis, *, key: str, seconds: int, **event: Any
) -> None:
    try:
        if await first_in_window(redis, key, seconds):
            await audit.record_detached(database, **event)
    except Exception as exc:
        # The refusal stands whatever happens to its trace.
        log.warning("denial_audit_failed", action=event.get("action"), error=type(exc).__name__)


def _route(request: Request) -> str:
    """The route template (`/v1/tasks/{task_id}`), rebuilt from the matched parameters:
    identifiers are not kept, and the path of an included router is relative."""
    path = request.url.path
    for key, value in request.path_params.items():
        path = path.replace(f"/{value}", f"/{{{key}}}", 1)
    return path[:200]


# --- CSRF (OriginCheckMiddleware) ---------------------------------------------------------


def origin_kind(origin: str | None) -> str:
    """What a refused Origin looked like, without storing what the client wrote."""
    if origin is None:
        return "absent"
    return "null" if origin.strip() == "null" else "foreign"


def path_area(path: str) -> str:
    """First segment after /v1 (`tasks`, `auth`...), the only part of the path kept."""
    parts = path.split("/")
    return _AREA_RE.sub("", parts[2].lower())[:40] if len(parts) > 2 else ""


async def _cookie_owner(database: Database, token: str | None) -> uuid.UUID | None:
    """The account a session cookie belongs to, whatever the state of the session."""
    if not token:
        return None
    async with database.sessionmaker() as session:
        user_id: uuid.UUID | None = await session.scalar(
            select(UserSession.user_id).where(UserSession.token_hash == hash_token(token))
        )
        return user_id


async def origin_mismatch(
    app: Any, *, method: str, path: str, origin: str | None, session_token: str | None
) -> None:
    """A state-changing request whose Origin is not the web client's.

    With SameSite=Strict a cross-site page cannot send the session cookie: a refused write
    that carries one is a cookie used outside the browser (or a misconfigured
    LIFEOS_PUBLIC_ORIGIN). The event then goes to that account's audit log.
    """
    kind, area = origin_kind(origin), path_area(path)
    SECURITY_EVENTS.labels("origin_mismatch").inc()
    log.warning("origin_mismatch", method=method, origin=kind, area=area)
    state = getattr(app, "state", None)
    database: Database | None = getattr(state, "db", None)
    redis: Redis | None = getattr(state, "redis", None)
    if database is None or redis is None:
        return
    try:
        if not await first_in_window(redis, "origin_mismatch", ORIGIN_WINDOW):
            return
        await audit.record_detached(
            database,
            action="http.origin_mismatch",
            outcome="denied",
            actor_type="anonymous",
            user_id=await _cookie_owner(database, session_token),
            details={
                "method": method,
                "origin": kind,
                "area": area,
                "session_cookie": session_token is not None,
            },
        )
    except Exception as exc:
        log.warning("denial_audit_failed", action="http.origin_mismatch", error=type(exc).__name__)


# --- Sessions (api/deps.py) ---------------------------------------------------------------


async def session_rejected(request: Request, check: SessionCheck) -> None:
    """A known session cookie that no longer opens a session (revoked, expired...)."""
    assert check.refusal is not None
    SECURITY_EVENTS.labels("session_rejected").inc()
    log.warning("session_rejected", reason=check.refusal, route=_route(request))
    details: dict[str, audit.DetailValue] = {"reason": check.refusal}
    if check.refusal == "revoked" and check.session.revoked_reason:
        details["revoked_reason"] = check.session.revoked_reason[:40]
    await _record_limited(
        request.app.state.db,
        request.app.state.redis,
        key=f"session_rejected:{check.session.id}",
        seconds=SESSION_WINDOW,
        action="auth.session.rejected",
        outcome="denied",
        actor_type="anonymous",
        user_id=check.session.user_id,
        target_type="session",
        target_id=check.session.id,
        details=details,
    )


async def session_moved(request: Request, check: SessionCheck) -> None:
    """A valid session used from another browser or system: audited, never blocked.

    Every request of such a session would match: the log line, like the audit event, is
    written once per session and per window (the metric counts every request)."""
    SECURITY_EVENTS.labels("session_ua_changed").inc()
    try:
        first = await first_in_window(
            request.app.state.redis, f"session_ua_logged:{check.session.id}", SESSION_WINDOW
        )
    except Exception:
        first = False
    if first:
        log.warning("session_ua_changed", route=_route(request))
    await _record_limited(
        request.app.state.db,
        request.app.state.redis,
        key=f"session_ua_changed:{check.session.id}",
        seconds=SESSION_WINDOW,
        action="auth.session.ua_changed",
        outcome="success",
        actor_type="user",
        actor_id=check.user.id,
        user_id=check.user.id,
        target_type="session",
        target_id=check.session.id,
        details={"user_agent_present": bool(request.headers.get("user-agent"))},
    )


async def reauthentication_required(
    request: Request, *, user_id: uuid.UUID, session_id: uuid.UUID, action: str
) -> None:
    """A reinforced operation attempted with a session older than the allowed delay.

    Limited per session and action: a script replaying it with an old (stolen) session
    must not flood the audit trail."""
    SECURITY_EVENTS.labels("reauthentication_required").inc()
    try:
        if not await first_in_window(
            request.app.state.redis, f"reauth:{session_id}:{action}", REAUTH_WINDOW
        ):
            return
        await audit.record_detached(
            request.app.state.db,
            action=action,
            outcome="denied",
            actor_type="user",
            actor_id=user_id,
            user_id=user_id,
            target_type="session",
            target_id=session_id,
            details={"reason": "reauthentication_required", "route": _route(request)},
        )
    except Exception as exc:
        log.warning("denial_audit_failed", action=action, error=type(exc).__name__)


# --- Spaces (Principal.require_space) -----------------------------------------------------


async def _space_access(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SpaceAccessDeniedError)
    SECURITY_EVENTS.labels("space_access").inc()
    log.warning("space_access_denied", actor_type=exc.actor_type, route=_route(request))
    await _record_limited(
        request.app.state.db,
        request.app.state.redis,
        key=f"space_access:{exc.user_id}",
        seconds=SPACE_WINDOW,
        action="space.access",
        outcome="denied",
        actor_type=exc.actor_type,
        actor_id=exc.actor_id,
        user_id=exc.user_id,
        target_type="space",
        target_id=exc.space_id,
        details={"reason": exc.code, "method": request.method, "route": _route(request)},
    )
    return problem_response(exc.status, exc.code, exc.title, exc.detail, extra=exc.extra)


def install_denial_handlers(app: FastAPI) -> None:
    # More specific than the ProblemError handler (looked up along the exception's MRO).
    app.add_exception_handler(SpaceAccessDeniedError, _space_access)
