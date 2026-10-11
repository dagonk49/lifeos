"""WebAuthn ceremonies. Challenges are stored server-side and consumed exactly once."""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    bytes_to_base64url,
    options_to_json_dict,
    parse_authentication_credential_json,
    parse_registration_credential_json,
)
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidJSONStructure,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.identity.models import (
    AuthChallenge,
    EnrollmentToken,
    PasskeyCredential,
    User,
)

_LABEL_MAX = 80


def _ceremony_failed(code: str = "passkey_verification_failed") -> ProblemError:
    # Deliberately vague: details go to the audit trail, not to the client.
    return ProblemError(400, code, "Passkey verification failed")


async def _store_challenge(
    session: AsyncSession,
    settings: Settings,
    *,
    purpose: str,
    challenge: bytes,
    user_id: uuid.UUID | None = None,
    enrollment_token_id: uuid.UUID | None = None,
) -> AuthChallenge:
    now = utcnow()
    record = AuthChallenge(
        purpose=purpose,
        user_id=user_id,
        enrollment_token_id=enrollment_token_id,
        challenge=challenge,
        expires_at=now + timedelta(minutes=settings.webauthn_challenge_minutes),
        created_at=now,
    )
    session.add(record)
    await session.flush()
    return record


async def _consume_challenge(
    session: AsyncSession, *, ceremony_id: uuid.UUID, purpose: str
) -> AuthChallenge:
    challenge = await session.scalar(
        select(AuthChallenge)
        .where(AuthChallenge.id == ceremony_id, AuthChallenge.purpose == purpose)
        .with_for_update()
    )
    if challenge is None or challenge.consumed_at is not None or challenge.expires_at <= utcnow():
        raise ProblemError(400, "ceremony_invalid", "Unknown, expired or already used ceremony")
    challenge.consumed_at = utcnow()
    return challenge


# --- Registration -------------------------------------------------------------------------


async def start_registration(
    session: AsyncSession,
    settings: Settings,
    *,
    user: User,
    enrollment_token: EnrollmentToken | None,
    existing: list[PasskeyCredential],
) -> tuple[uuid.UUID, dict[str, Any]]:
    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user.id.bytes,
        user_name=user.email,
        user_display_name=user.display_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c.credential_id) for c in existing],
    )
    challenge = await _store_challenge(
        session,
        settings,
        purpose="registration",
        challenge=options.challenge,
        user_id=user.id,
        enrollment_token_id=enrollment_token.id if enrollment_token else None,
    )
    return challenge.id, options_to_json_dict(options)


async def finish_registration(
    session: AsyncSession,
    settings: Settings,
    *,
    ceremony_id: uuid.UUID,
    credential: dict[str, Any],
    label: str,
    authenticated_user: User | None,
) -> tuple[User, PasskeyCredential, EnrollmentToken | None]:
    """Registers a passkey, either with an enrollment token or from an authenticated session.

    The challenge is consumed even when verification fails; failures are committed to the
    audit trail by the caller.
    """
    challenge = await _consume_challenge(session, ceremony_id=ceremony_id, purpose="registration")
    if challenge.user_id is None:
        raise _ceremony_failed()
    user = await session.get(User, challenge.user_id)
    if user is None or user.disabled_at is not None:
        raise _ceremony_failed()

    enrollment: EnrollmentToken | None = None
    if challenge.enrollment_token_id is not None:
        enrollment = await session.scalar(
            select(EnrollmentToken)
            .where(EnrollmentToken.id == challenge.enrollment_token_id)
            .with_for_update()
        )
        if (
            enrollment is None
            or enrollment.used_at is not None
            or enrollment.expires_at <= utcnow()
        ):
            raise ProblemError(400, "enrollment_token_invalid", "Invalid enrollment token")
    elif authenticated_user is None or authenticated_user.id != user.id:
        # A ceremony started from a session must be finished by the same user.
        raise _ceremony_failed()

    try:
        parsed = parse_registration_credential_json(credential)
        verified = verify_registration_response(
            credential=parsed,
            expected_challenge=challenge.challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.public_origin,
            require_user_verification=True,
        )
    except (InvalidRegistrationResponse, InvalidJSONStructure, ValueError, KeyError) as exc:
        await audit.record(
            session,
            action="auth.passkey.register",
            outcome="failure",
            actor_type="user" if authenticated_user else "anonymous",
            actor_id=authenticated_user.id if authenticated_user else None,
            user_id=user.id,
            details={"reason": type(exc).__name__},
        )
        raise _ceremony_failed() from exc

    now = utcnow()
    transports = [
        t.value if isinstance(t, AuthenticatorTransport) else str(t)
        for t in (parsed.response.transports or [])
    ]
    passkey = PasskeyCredential(
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=transports,
        aaguid=verified.aaguid,
        backup_eligible=verified.credential_device_type.value == "multi_device",
        backup_state=verified.credential_backed_up,
        label=(label.strip() or "Passkey")[:_LABEL_MAX],
        created_at=now,
    )
    session.add(passkey)
    if enrollment is not None:
        enrollment.used_at = now
    await session.flush()
    await audit.record(
        session,
        action="auth.passkey.register",
        actor_type="user" if authenticated_user else "anonymous",
        actor_id=authenticated_user.id if authenticated_user else None,
        user_id=user.id,
        target_type="passkey",
        target_id=passkey.id,
        details={"via": "enrollment_token" if enrollment else "session"},
    )
    return user, passkey, enrollment


# --- Authentication -----------------------------------------------------------------------


async def start_authentication(
    session: AsyncSession, settings: Settings
) -> tuple[uuid.UUID, dict[str, Any]]:
    # Discoverable credentials: no allow-list, the authenticator picks the account.
    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge = await _store_challenge(
        session, settings, purpose="authentication", challenge=options.challenge
    )
    return challenge.id, options_to_json_dict(options)


async def finish_authentication(
    session: AsyncSession,
    settings: Settings,
    *,
    ceremony_id: uuid.UUID,
    credential: dict[str, Any],
) -> tuple[User, PasskeyCredential]:
    challenge = await _consume_challenge(session, ceremony_id=ceremony_id, purpose="authentication")

    async def fail(reason: str, user_id: uuid.UUID | None = None) -> ProblemError:
        await audit.record(
            session,
            action="auth.login",
            outcome="failure",
            actor_type="anonymous",
            user_id=user_id,
            details={"reason": reason},
        )
        return _ceremony_failed()

    try:
        parsed = parse_authentication_credential_json(credential)
    except (InvalidJSONStructure, ValueError, KeyError) as exc:
        raise await fail("malformed_credential") from exc

    passkey = await session.scalar(
        select(PasskeyCredential).where(
            PasskeyCredential.credential_id == parsed.raw_id,
            PasskeyCredential.revoked_at.is_(None),
        )
    )
    if passkey is None:
        raise await fail("unknown_credential")
    user = await session.get(User, passkey.user_id)
    if user is None or user.disabled_at is not None:
        raise await fail("user_disabled", passkey.user_id)
    user_handle = parsed.response.user_handle
    if user_handle is not None and user_handle != user.id.bytes:
        raise await fail("user_handle_mismatch", user.id)

    try:
        verified = verify_authentication_response(
            credential=parsed,
            expected_challenge=challenge.challenge,
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.public_origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as exc:
        raise await fail("verification_failed", user.id) from exc

    passkey.sign_count = verified.new_sign_count
    passkey.backup_state = verified.credential_backed_up
    passkey.last_used_at = utcnow()
    await audit.record(
        session,
        action="auth.login",
        actor_type="user",
        actor_id=user.id,
        user_id=user.id,
        target_type="passkey",
        target_id=passkey.id,
    )
    return user, passkey


def credential_id_b64(passkey: PasskeyCredential) -> str:
    return bytes_to_base64url(passkey.credential_id)
