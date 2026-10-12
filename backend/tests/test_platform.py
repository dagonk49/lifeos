import asyncio
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.cli import main as cli_main
from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.modules.audit import service as audit
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.identity import service as identity
from lifeos.modules.identity.models import AuthChallenge, User, UserSession

from .conftest import Owner


async def test_health_and_readiness(client: AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "redis": "ok"}


async def test_responses_carry_request_id_and_security_headers(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "abc12345-test"})
    assert response.headers["x-request-id"] == "abc12345-test"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_unknown_route_is_a_problem_document(client: AsyncClient) -> None:
    response = await client.get("/v1/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "not_found"


async def test_validation_errors_do_not_echo_input(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/passkeys/authentication/verify",
        json={"ceremony_id": "secret-value-not-a-uuid", "credential": {}},
    )
    assert response.status_code == 422
    assert "secret-value" not in response.text


async def test_metrics_are_exposed(client: AsyncClient) -> None:
    await client.get("/healthz")
    body = (await client.get("/metrics/")).text
    assert 'lifeos_http_request_duration_seconds_count{method="GET",route="/healthz"' in body


async def test_audit_is_append_only(db: AsyncSession) -> None:
    await audit.record(db, action="test.event", actor_type="system")
    await db.commit()
    with pytest.raises(DBAPIError):
        await db.execute(text("UPDATE audit_events SET action = 'tampered'"))
    await db.rollback()
    with pytest.raises(DBAPIError):
        await db.execute(text("DELETE FROM audit_events"))
    await db.rollback()


async def test_audit_rejects_content(db: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await audit.record(
            db, action="test.event", actor_type="system", details={"note": "x" * 500}
        )


async def test_audit_retention_purge(db: AsyncSession) -> None:
    await audit.record(db, action="old.event", actor_type="system")
    await audit.record(db, action="new.event", actor_type="system")
    await db.commit()
    purged = await audit.purge_older_than(db, days=365, now=utcnow() + timedelta(days=400))
    await db.commit()
    assert purged == 2 + 0  # both events are older than the cutoff
    assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0


async def test_purge_expired_auth_artifacts(
    client: AsyncClient, logged_in: Owner, db: AsyncSession
) -> None:
    await client.post("/v1/auth/passkeys/authentication/options")
    await db.execute(text("UPDATE auth_challenges SET expires_at = now() - interval '2 days'"))
    await db.execute(text("UPDATE user_sessions SET revoked_at = now() - interval '31 days'"))
    await db.commit()
    counts = await identity.purge_expired(db)
    await db.commit()
    assert counts["challenges"] >= 1
    assert counts["sessions"] == 1
    assert await db.scalar(select(func.count()).select_from(AuthChallenge)) == 0
    assert await db.scalar(select(func.count()).select_from(UserSession)) == 0


async def test_cli_bootstrap_and_recovery(
    capsys: pytest.CaptureFixture[str], db: AsyncSession, settings: Settings
) -> None:
    async def cli(*args: str) -> int:
        # The CLI owns its event loop (asyncio.run): run it in a thread.
        return await asyncio.to_thread(cli_main, list(args))

    assert await cli("create-owner", "--email", "me@example.org", "--name", "Me") == 0
    out = capsys.readouterr().out
    assert f"{settings.public_origin}/enroll#token=" in out

    # Single owner in V1.
    assert await cli("create-owner", "--email", "other@example.org", "--name", "X") == 1

    assert await cli("enrollment-token", "--email", "ME@example.org") == 0
    assert "enroll#token=" in capsys.readouterr().out
    assert await cli("enrollment-token", "--email", "nobody@example.org") == 1
    assert await cli("revoke-sessions", "--email", "me@example.org") == 0

    user = await db.scalar(select(User))
    assert user is not None and user.email == "me@example.org"
