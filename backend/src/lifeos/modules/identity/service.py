"""Users, spaces, enrollment tokens and sessions."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import IPv4Address, IPv6Address, ip_address

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError, conflict, not_found
from lifeos.core.security import hash_token, new_token, utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.identity.models import (
    AuthChallenge,
    EnrollmentToken,
    PasskeyCredential,
    Space,
    User,
    UserSession,
)

DEFAULT_SPACES = (("personal", "Personnel"), ("professional", "Professionnel"))
_TOUCH_INTERVAL = timedelta(minutes=1)
_USER_AGENT_MAX = 512


def _parse_ip(value: str | None) -> IPv4Address | IPv6Address | None:
    if not value:
        return None
    try:
        return ip_address(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_in_minutes: int


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email.strip().lower()))


async def list_spaces(session: AsyncSession, user_id: uuid.UUID) -> list[Space]:
    rows = await session.scalars(
        select(Space).where(Space.user_id == user_id).order_by(Space.kind, Space.name)
    )
    return list(rows)


async def create_owner(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    display_name: str,
    timezone: str | None = None,
) -> tuple[User, IssuedToken]:
    """V1 has a single owner account; refuses to create a second one."""
    existing = await session.scalar(select(User.id).where(User.role == "owner").limit(1))
    if existing is not None:
        raise conflict("owner_exists", "An owner account already exists")
    user = User(
        email=email.strip().lower(),
        display_name=display_name.strip(),
        role="owner",
        timezone=timezone or settings.timezone,
    )
    session.add(user)
    await session.flush()
    for kind, name in DEFAULT_SPACES:
        session.add(Space(user_id=user.id, kind=kind, name=name))
    await audit.record(
        session,
        action="user.create_owner",
        actor_type="system",
        actor_id="cli",
        user_id=user.id,
        target_type="user",
        target_id=user.id,
    )
    token = await issue_enrollment_token(session, settings, user=user, purpose="bootstrap")
    return user, token


async def issue_enrollment_token(
    session: AsyncSession, settings: Settings, *, user: User, purpose: str
) -> IssuedToken:
    token = new_token()
    now = utcnow()
    record = EnrollmentToken(
        user_id=user.id,
        token_hash=hash_token(token),
        purpose=purpose,
        expires_at=now + timedelta(minutes=settings.enrollment_token_minutes),
        created_at=now,
    )
    session.add(record)
    await session.flush()
    await audit.record(
        session,
        action="auth.enrollment_token.issue",
        actor_type="system",
        actor_id="cli",
        user_id=user.id,
        target_type="enrollment_token",
        target_id=record.id,
        details={"purpose": purpose},
    )
    return IssuedToken(token=token, expires_in_minutes=settings.enrollment_token_minutes)


async def find_valid_enrollment_token(
    session: AsyncSession, token: str, *, for_update: bool = False
) -> EnrollmentToken | None:
    stmt = select(EnrollmentToken).where(
        EnrollmentToken.token_hash == hash_token(token),
        EnrollmentToken.used_at.is_(None),
        EnrollmentToken.expires_at > utcnow(),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


# --- Sessions -----------------------------------------------------------------------------


async def create_session(
    session: AsyncSession,
    settings: Settings,
    *,
    user: User,
    credential: PasskeyCredential | None,
    user_agent: str | None,
    ip: str | None,
) -> tuple[UserSession, str]:
    token = new_token()
    now = utcnow()
    record = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        credential_id=credential.id if credential else None,
        authenticated_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=settings.session_ttl_days),
        user_agent=user_agent[:_USER_AGENT_MAX] if user_agent else None,
        ip=_parse_ip(ip),
        created_at=now,
    )
    session.add(record)
    await session.flush()
    return record, token


async def resolve_session(
    session: AsyncSession,
    settings: Settings,
    token: str,
    *,
    ip: str | None = None,
) -> tuple[UserSession, User] | None:
    now = utcnow()
    row = (
        await session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == hash_token(token))
        )
    ).one_or_none()
    if row is None:
        return None
    user_session, user = row
    if (
        user_session.revoked_at is not None
        or user_session.expires_at <= now
        or user_session.last_seen_at + timedelta(days=settings.session_idle_days) <= now
        or user.disabled_at is not None
    ):
        return None
    if now - user_session.last_seen_at >= _TOUCH_INTERVAL:
        user_session.last_seen_at = now
        if ip:
            user_session.ip = _parse_ip(ip)
    return user_session, user


async def list_sessions(session: AsyncSession, user_id: uuid.UUID) -> list[UserSession]:
    rows = await session.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
        .order_by(UserSession.last_seen_at.desc())
    )
    return list(rows)


async def revoke_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    reason: str,
    actor_id: uuid.UUID,
) -> None:
    target = await session.scalar(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
    )
    if target is None:
        raise not_found("session_not_found", "Session not found")
    target.revoked_at = utcnow()
    target.revoked_reason = reason
    await audit.record(
        session,
        action="auth.session.revoke",
        actor_type="user",
        actor_id=actor_id,
        user_id=user_id,
        target_type="session",
        target_id=session_id,
        details={"reason": reason},
    )


async def revoke_all_sessions(session: AsyncSession, *, user_id: uuid.UUID, reason: str) -> int:
    result = await session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )
    count = int(result.rowcount or 0)  # type: ignore[attr-defined]
    await audit.record(
        session,
        action="auth.session.revoke_all",
        actor_type="system",
        actor_id="cli",
        user_id=user_id,
        details={"reason": reason, "count": count},
    )
    return count


# --- Passkeys -----------------------------------------------------------------------------


async def list_passkeys(session: AsyncSession, user_id: uuid.UUID) -> list[PasskeyCredential]:
    rows = await session.scalars(
        select(PasskeyCredential)
        .where(PasskeyCredential.user_id == user_id, PasskeyCredential.revoked_at.is_(None))
        .order_by(PasskeyCredential.created_at)
    )
    return list(rows)


async def get_passkey(
    session: AsyncSession, *, user_id: uuid.UUID, passkey_id: uuid.UUID
) -> PasskeyCredential:
    passkey = await session.scalar(
        select(PasskeyCredential).where(
            PasskeyCredential.id == passkey_id,
            PasskeyCredential.user_id == user_id,
            PasskeyCredential.revoked_at.is_(None),
        )
    )
    if passkey is None:
        raise not_found("passkey_not_found", "Passkey not found")
    return passkey


async def revoke_passkey(
    session: AsyncSession, *, user_id: uuid.UUID, passkey_id: uuid.UUID
) -> None:
    passkey = await get_passkey(session, user_id=user_id, passkey_id=passkey_id)
    active = await list_passkeys(session, user_id)
    if len(active) <= 1:
        raise ProblemError(
            409,
            "last_passkey",
            "Cannot revoke the last passkey",
            "Register another passkey first, or use `lifeos-admin enrollment-token`.",
        )
    now = utcnow()
    passkey.revoked_at = now
    # Sessions opened with this passkey are revoked with it.
    await session.execute(
        update(UserSession)
        .where(UserSession.credential_id == passkey.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason="passkey_revoked")
    )
    await audit.record(
        session,
        action="auth.passkey.revoke",
        actor_type="user",
        actor_id=user_id,
        user_id=user_id,
        target_type="passkey",
        target_id=passkey.id,
    )


# --- Maintenance --------------------------------------------------------------------------


async def purge_expired(session: AsyncSession) -> dict[str, int]:
    """Retention for authentication artefacts (docs/politique-de-donnees.md)."""
    now = utcnow()
    day_ago = now - timedelta(days=1)
    month_ago = now - timedelta(days=30)
    challenges = await session.execute(
        delete(AuthChallenge).where(AuthChallenge.expires_at < day_ago)
    )
    tokens = await session.execute(
        delete(EnrollmentToken).where(
            or_(EnrollmentToken.expires_at < day_ago, EnrollmentToken.used_at < day_ago)
        )
    )
    sessions = await session.execute(
        delete(UserSession).where(
            or_(UserSession.expires_at < month_ago, UserSession.revoked_at < month_ago)
        )
    )
    counts = {
        "challenges": int(challenges.rowcount or 0),  # type: ignore[attr-defined]
        "enrollment_tokens": int(tokens.rowcount or 0),  # type: ignore[attr-defined]
        "sessions": int(sessions.rowcount or 0),  # type: ignore[attr-defined]
    }
    await audit.record(
        session,
        action="auth.purge_expired",
        actor_type="system",
        actor_id="purge_auth_artifacts",
        details=dict(counts),
    )
    return counts
