import uuid

from fastapi import APIRouter, Request, Response, status

from lifeos.api.deps import (
    SESSION_COOKIE,
    Auth,
    DbSession,
    OptionalAuth,
    RecentAuth,
    SettingsDep,
    client_ip,
    get_recent_auth,
)
from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.modules.audit import service as audit
from lifeos.modules.identity import passkeys
from lifeos.modules.identity import service as identity
from lifeos.modules.identity.models import PasskeyCredential, User
from lifeos.modules.identity.schemas import (
    AuthenticationVerifyRequest,
    CeremonyOptions,
    MeOut,
    PasskeyOut,
    PasskeyUpdate,
    RegistrationOptionsRequest,
    RegistrationVerifyRequest,
    SessionInfo,
    SessionOut,
    SpaceOut,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 24 * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


async def _open_session(
    request: Request,
    response: Response,
    db: DbSession,
    settings: Settings,
    user: User,
    passkey: PasskeyCredential,
) -> None:
    _, token = await identity.create_session(
        db,
        settings,
        user=user,
        credential=passkey,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )
    _set_session_cookie(response, settings, token)


@router.post("/passkeys/registration/options")
async def registration_options(
    body: RegistrationOptionsRequest, db: DbSession, settings: SettingsDep, auth: OptionalAuth
) -> CeremonyOptions:
    enrollment = None
    if body.enrollment_token:
        enrollment = await identity.find_valid_enrollment_token(db, body.enrollment_token)
        if enrollment is None:
            raise ProblemError(400, "enrollment_token_invalid", "Invalid enrollment token")
        user = await db.get(User, enrollment.user_id)
        if user is None:
            raise ProblemError(400, "enrollment_token_invalid", "Invalid enrollment token")
    elif auth is not None:
        # Adding a device to an existing account is a reinforced operation.
        await get_recent_auth(auth, settings)
        user = auth.user
    else:
        raise ProblemError(401, "unauthenticated", "Authentication required")

    existing = await identity.list_passkeys(db, user.id)
    ceremony_id, options = await passkeys.start_registration(
        db, settings, user=user, enrollment_token=enrollment, existing=existing
    )
    return CeremonyOptions(ceremony_id=ceremony_id, public_key=options)


@router.post("/passkeys/registration/verify", status_code=status.HTTP_201_CREATED)
async def registration_verify(
    body: RegistrationVerifyRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    auth: OptionalAuth,
) -> PasskeyOut:
    try:
        user, passkey, enrollment = await passkeys.finish_registration(
            db,
            settings,
            ceremony_id=body.ceremony_id,
            credential=body.credential,
            label=body.label,
            authenticated_user=auth.user if auth else None,
        )
    except ProblemError:
        # Keep the consumed challenge and the failure audit event.
        await db.commit()
        raise
    if enrollment is not None:
        # Enrollment just verified the user with their new passkey: sign them in.
        await _open_session(request, response, db, settings, user, passkey)
    return PasskeyOut.model_validate(passkey)


@router.post("/passkeys/authentication/options")
async def authentication_options(db: DbSession, settings: SettingsDep) -> CeremonyOptions:
    ceremony_id, options = await passkeys.start_authentication(db, settings)
    return CeremonyOptions(ceremony_id=ceremony_id, public_key=options)


@router.post("/passkeys/authentication/verify")
async def authentication_verify(
    body: AuthenticationVerifyRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    auth: OptionalAuth,
) -> UserOut:
    try:
        user, passkey = await passkeys.finish_authentication(
            db, settings, ceremony_id=body.ceremony_id, credential=body.credential
        )
    except ProblemError:
        await db.commit()
        raise
    if auth is not None and auth.user.id == user.id:
        # Re-authentication: the fresh session replaces the current one.
        await identity.revoke_session(
            db,
            user_id=user.id,
            session_id=auth.session.id,
            reason="reauthenticated",
            actor_id=user.id,
        )
    await _open_session(request, response, db, settings, user, passkey)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, db: DbSession, auth: Auth, settings: SettingsDep) -> None:
    await identity.revoke_session(
        db,
        user_id=auth.user.id,
        session_id=auth.session.id,
        reason="logout",
        actor_id=auth.user.id,
    )
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict"
    )


@router.get("/me")
async def me(db: DbSession, auth: Auth) -> MeOut:
    spaces = await identity.list_spaces(db, auth.user.id)
    return MeOut(
        user=UserOut.model_validate(auth.user),
        spaces=[SpaceOut.model_validate(s) for s in spaces],
        session=SessionInfo.model_validate(auth.session),
    )


@router.get("/sessions")
async def list_sessions(db: DbSession, auth: Auth) -> list[SessionOut]:
    sessions = await identity.list_sessions(db, auth.user.id)
    return [
        SessionOut.model_validate(s).model_copy(update={"current": s.id == auth.session.id})
        for s in sessions
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(session_id: uuid.UUID, db: DbSession, auth: Auth) -> None:
    await identity.revoke_session(
        db, user_id=auth.user.id, session_id=session_id, reason="user", actor_id=auth.user.id
    )


@router.get("/passkeys")
async def list_passkeys(db: DbSession, auth: Auth) -> list[PasskeyOut]:
    return [PasskeyOut.model_validate(p) for p in await identity.list_passkeys(db, auth.user.id)]


@router.patch("/passkeys/{passkey_id}")
async def rename_passkey(
    passkey_id: uuid.UUID, body: PasskeyUpdate, db: DbSession, auth: Auth
) -> PasskeyOut:
    passkey = await identity.get_passkey(db, user_id=auth.user.id, passkey_id=passkey_id)
    passkey.label = body.label.strip()
    await audit.record(
        db,
        action="auth.passkey.rename",
        actor_type="user",
        actor_id=auth.user.id,
        user_id=auth.user.id,
        target_type="passkey",
        target_id=passkey.id,
    )
    return PasskeyOut.model_validate(passkey)


@router.delete("/passkeys/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_passkey(passkey_id: uuid.UUID, db: DbSession, auth: RecentAuth) -> None:
    await identity.revoke_passkey(db, user_id=auth.user.id, passkey_id=passkey_id)
