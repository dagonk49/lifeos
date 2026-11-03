from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from lifeos.api.deps import client_ip
from lifeos.core.security import utcnow
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.identity.models import EnrollmentToken, UserSession

from .conftest import ORIGIN, Owner, enroll, login
from .webauthn_soft import SoftAuthenticator


async def test_enrollment_registers_passkey_and_opens_session(
    client: AsyncClient, owner: Owner
) -> None:
    passkey = await enroll(client, owner, label="Pixel 9")
    assert passkey["label"] == "Pixel 9"
    assert passkey["backup_eligible"] is True

    me = await client.get("/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "owner@example.org"
    assert {s["kind"] for s in body["spaces"]} == {"personal", "professional"}


async def test_enrollment_token_is_single_use(client: AsyncClient, owner: Owner) -> None:
    await enroll(client, owner)
    response = await client.post(
        "/v1/auth/passkeys/registration/options",
        json={"enrollment_token": owner.enrollment_token},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "enrollment_token_invalid"


async def test_expired_enrollment_token_is_refused(
    client: AsyncClient, owner: Owner, db: AsyncSession
) -> None:
    await db.execute(update(EnrollmentToken).values(expires_at=utcnow() - timedelta(minutes=1)))
    await db.commit()
    response = await client.post(
        "/v1/auth/passkeys/registration/options",
        json={"enrollment_token": owner.enrollment_token},
    )
    assert response.status_code == 400


async def test_registration_requires_token_or_session(client: AsyncClient) -> None:
    response = await client.post("/v1/auth/passkeys/registration/options", json={})
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"


async def test_login_with_passkey(client: AsyncClient, logged_in: Owner) -> None:
    client.cookies.clear()
    assert (await client.get("/v1/auth/me")).status_code == 401
    await login(client, logged_in.authenticator)
    assert (await client.get("/v1/auth/me")).status_code == 200


async def test_challenge_cannot_be_replayed(client: AsyncClient, logged_in: Owner) -> None:
    options = (await client.post("/v1/auth/passkeys/authentication/options")).json()
    credential = logged_in.authenticator.get(options["public_key"])
    payload = {"ceremony_id": options["ceremony_id"], "credential": credential}
    assert (await client.post("/v1/auth/passkeys/authentication/verify", json=payload)).is_success
    replay = await client.post("/v1/auth/passkeys/authentication/verify", json=payload)
    assert replay.status_code == 400
    assert replay.json()["code"] == "ceremony_invalid"


async def test_unknown_passkey_is_refused_and_audited(
    client: AsyncClient, logged_in: Owner, settings: object, db: AsyncSession
) -> None:
    stranger = SoftAuthenticator("localhost", ORIGIN)
    stranger.user_handle = logged_in.authenticator.user_handle
    options = (await client.post("/v1/auth/passkeys/authentication/options")).json()
    response = await client.post(
        "/v1/auth/passkeys/authentication/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": stranger.get(options["public_key"]),
        },
    )
    assert response.status_code == 400
    event = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "auth.login", AuditEvent.outcome == "failure")
    )
    assert event is not None
    assert event.details == {"reason": "unknown_credential"}


async def test_wrong_origin_in_client_data_is_refused(
    client: AsyncClient, logged_in: Owner
) -> None:
    options = (await client.post("/v1/auth/passkeys/authentication/options")).json()
    credential = logged_in.authenticator.get(options["public_key"], origin="https://evil.example")
    response = await client.post(
        "/v1/auth/passkeys/authentication/verify",
        json={"ceremony_id": options["ceremony_id"], "credential": credential},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "passkey_verification_failed"


async def test_user_verification_is_required(client: AsyncClient, owner: Owner) -> None:
    owner.authenticator.user_verification = False
    options = (
        await client.post(
            "/v1/auth/passkeys/registration/options",
            json={"enrollment_token": owner.enrollment_token},
        )
    ).json()
    response = await client.post(
        "/v1/auth/passkeys/registration/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": owner.authenticator.create(options["public_key"]),
        },
    )
    assert response.status_code == 400


async def test_cloned_authenticator_is_detected(client: AsyncClient, logged_in: Owner) -> None:
    await login(client, logged_in.authenticator)  # sign_count = 2 server side
    logged_in.authenticator.sign_count = 0  # a clone replays an older counter
    options = (await client.post("/v1/auth/passkeys/authentication/options")).json()
    response = await client.post(
        "/v1/auth/passkeys/authentication/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": logged_in.authenticator.get(options["public_key"]),
        },
    )
    assert response.status_code == 400


async def test_logout_revokes_session(client: AsyncClient, logged_in: Owner) -> None:
    assert (await client.post("/v1/auth/logout")).status_code == 204
    assert (await client.get("/v1/auth/me")).status_code == 401


async def test_list_and_revoke_sessions(client: AsyncClient, logged_in: Owner) -> None:
    sessions = (await client.get("/v1/auth/sessions")).json()
    assert len(sessions) == 1
    assert sessions[0]["current"] is True
    assert sessions[0]["user_agent"] == "pytest"
    response = await client.delete(f"/v1/auth/sessions/{sessions[0]['id']}")
    assert response.status_code == 204
    assert (await client.get("/v1/auth/me")).status_code == 401


async def test_expired_and_idle_sessions_are_rejected(
    client: AsyncClient, logged_in: Owner, db: AsyncSession
) -> None:
    await db.execute(update(UserSession).values(expires_at=utcnow() - timedelta(seconds=1)))
    await db.commit()
    assert (await client.get("/v1/auth/me")).status_code == 401

    await login(client, logged_in.authenticator)
    await db.execute(update(UserSession).values(last_seen_at=utcnow() - timedelta(days=8)))
    await db.commit()
    assert (await client.get("/v1/auth/me")).status_code == 401


async def test_add_second_passkey_then_revoke_first(client: AsyncClient, logged_in: Owner) -> None:
    laptop = SoftAuthenticator("localhost", ORIGIN)
    options = (await client.post("/v1/auth/passkeys/registration/options", json={})).json()
    created = await client.post(
        "/v1/auth/passkeys/registration/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": laptop.create(options["public_key"]),
            "label": "Laptop",
        },
    )
    assert created.status_code == 201
    passkeys = (await client.get("/v1/auth/passkeys")).json()
    assert [p["label"] for p in passkeys] == ["Pixel", "Laptop"]

    # Revoking the passkey of the current session also revokes that session.
    first = passkeys[0]["id"]
    assert (await client.delete(f"/v1/auth/passkeys/{first}")).status_code == 204
    assert (await client.get("/v1/auth/me")).status_code == 401

    await login(client, laptop)
    assert len((await client.get("/v1/auth/passkeys")).json()) == 1


async def test_last_passkey_cannot_be_revoked(client: AsyncClient, logged_in: Owner) -> None:
    passkeys = (await client.get("/v1/auth/passkeys")).json()
    response = await client.delete(f"/v1/auth/passkeys/{passkeys[0]['id']}")
    assert response.status_code == 409
    assert response.json()["code"] == "last_passkey"


async def test_reinforced_operations_require_recent_login(
    client: AsyncClient, logged_in: Owner, db: AsyncSession
) -> None:
    await db.execute(update(UserSession).values(authenticated_at=utcnow() - timedelta(minutes=11)))
    await db.commit()
    response = await client.post("/v1/auth/passkeys/registration/options", json={})
    assert response.status_code == 403
    assert response.json()["code"] == "reauthentication_required"


async def test_rename_passkey(client: AsyncClient, logged_in: Owner) -> None:
    passkey_id = (await client.get("/v1/auth/passkeys")).json()[0]["id"]
    response = await client.patch(f"/v1/auth/passkeys/{passkey_id}", json={"label": "Téléphone"})
    assert response.json()["label"] == "Téléphone"


async def test_unsafe_request_without_origin_is_refused(
    client: AsyncClient, logged_in: Owner
) -> None:
    response = await client.post("/v1/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["code"] == "origin_mismatch"
    del client.headers["Origin"]
    assert (await client.post("/v1/auth/logout")).status_code == 403


async def test_session_token_is_stored_hashed(
    client: AsyncClient, logged_in: Owner, db: AsyncSession
) -> None:
    token = client.cookies.get("lifeos_session")
    assert token
    stored = await db.scalar(select(UserSession.token_hash))
    assert stored is not None and token.encode() not in stored


def _request(
    headers: dict[str, str], peer: tuple[str, int] | None = ("172.19.0.3", 40000)
) -> Request:
    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "headers": raw, "client": peer})


def test_client_address_is_the_one_traefik_vouches_for() -> None:
    # Behind a reverse proxy, X-Forwarded-For starts with whatever the client wrote.
    spoofed = _request(
        {"X-Real-Ip": "203.0.113.7", "X-Forwarded-For": "6.6.6.6, 203.0.113.7, 172.19.0.4"},
        peer=("6.6.6.6", 0),
    )
    assert client_ip(spoofed) == "203.0.113.7"
    assert client_ip(_request({})) == "172.19.0.3"
    assert client_ip(_request({}, peer=None)) is None
