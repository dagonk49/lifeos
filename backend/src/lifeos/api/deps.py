from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

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
    resolved = await identity.resolve_session(db, settings, token, ip=client_ip(request))
    if resolved is None:
        return None
    user_session, user = resolved
    return AuthContext(user=user, session=user_session)


async def get_auth(auth: Annotated[AuthContext | None, Depends(get_optional_auth)]) -> AuthContext:
    if auth is None:
        raise ProblemError(401, "unauthenticated", "Authentication required")
    return auth


async def get_recent_auth(
    auth: Annotated[AuthContext, Depends(get_auth)], settings: SettingsDep
) -> AuthContext:
    """For "reinforced validation" operations: login must be recent."""
    if not is_recent(auth.session, settings):
        raise ProblemError(
            403,
            "reauthentication_required",
            "Recent authentication required",
            "Sign in again with a passkey to perform this operation.",
        )
    return auth


OptionalAuth = Annotated[AuthContext | None, Depends(get_optional_auth)]
Auth = Annotated[AuthContext, Depends(get_auth)]
RecentAuth = Annotated[AuthContext, Depends(get_recent_auth)]


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
    )


CtxDep = Annotated[Ctx, Depends(get_ctx)]
