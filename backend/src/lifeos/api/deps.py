from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.api import denials
from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.principal import user_principal
from lifeos.modules.identity import service as identity
from lifeos.modules.identity.models import User, UserSession

SESSION_COOKIE = "lifeos_session"


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request, committed before the response is sent."""
    database: Database = request.app.state.db
    async with database.sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def client_ip(request: Request) -> str | None:
    """Address of the client, as seen by the first proxy (shown in the list of sessions).

    Only Traefik reaches the API, and it always sets X-Real-Ip: to the peer address when it
    faces the Internet (a client-sent value is dropped), or to the value of the reverse proxy
    in front of it (compose.proxy.yaml). The leftmost X-Forwarded-For entry, which uvicorn
    reports, can be written by the client itself in that second case.
    """
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else None


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: UserSession


async def get_optional_auth(
    request: Request, db: DbSession, settings: SettingsDep
) -> AuthContext | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    check = await identity.check_session(db, settings, token, ip=client_ip(request))
    if check is None:
        # Unknown token: nothing to attribute it to.
        return None
    if check.refusal is not None:
        # A revoked, expired or idle session reused (a stolen cookie, an old device).
        await denials.session_rejected(request, check)
        return None
    if not identity.same_device(check.session.user_agent, request.headers.get("user-agent")):
        # Same cookie, another browser or system: a sign of a copied cookie. Not blocked
        # (the owner revokes the session from the settings), but recorded.
        await denials.session_moved(request, check)
    return AuthContext(user=check.user, session=check.session)


async def get_auth(auth: Annotated[AuthContext | None, Depends(get_optional_auth)]) -> AuthContext:
    if auth is None:
        raise ProblemError(401, "unauthenticated", "Authentication required")
    return auth


def _reauthentication_required() -> ProblemError:
    return ProblemError(
        403,
        "reauthentication_required",
        "Recent authentication required",
        "Sign in again with a passkey to perform this operation.",
    )


async def get_recent_auth(
    auth: Annotated[AuthContext, Depends(get_auth)], settings: SettingsDep
) -> AuthContext:
    """For "reinforced validation" operations: login must be recent (not audited; routes
    use `recent_auth(action)`, which records the refusal)."""
    if not is_recent(auth.session, settings):
        raise _reauthentication_required()
    return auth


async def require_recent_auth(
    request: Request, auth: AuthContext, settings: Settings, *, action: str
) -> None:
    """Refuses `action` (and audits the refusal) unless the login is recent."""
    if not is_recent(auth.session, settings):
        await denials.reauthentication_required(
            request, user_id=auth.user.id, session_id=auth.session.id, action=action
        )
        raise _reauthentication_required()


def recent_auth(action: str) -> Callable[..., Awaitable[AuthContext]]:
    """Dependency for a reinforced route; a refusal is audited as `action` (denied)."""

    async def dependency(
        request: Request,
        auth: Annotated[AuthContext, Depends(get_auth)],
        settings: SettingsDep,
    ) -> AuthContext:
        await require_recent_auth(request, auth, settings, action=action)
        return auth

    return dependency


OptionalAuth = Annotated[AuthContext | None, Depends(get_optional_auth)]
Auth = Annotated[AuthContext, Depends(get_auth)]
RecentAuth = Annotated[AuthContext, Depends(recent_auth("auth.reinforced"))]


def is_recent(user_session: UserSession, settings: Settings) -> bool:
    return utcnow() - user_session.authenticated_at <= timedelta(
        minutes=settings.recent_auth_minutes
    )


async def get_ctx(request: Request, db: DbSession, settings: SettingsDep, auth: Auth) -> Ctx:
    principal = await user_principal(
        db, auth.user.id, recent_auth=is_recent(auth.session, settings)
    )
    return Ctx(
        db=db,
        database=request.app.state.db,
        settings=settings,
        principal=principal,
        user=auth.user,
        llm=request.app.state.llm,
        embedder=getattr(request.app.state, "embedder", None),
        connectors=getattr(request.app.state, "connectors", {}),
    )


CtxDep = Annotated[Ctx, Depends(get_ctx)]
