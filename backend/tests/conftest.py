import os

# Test settings must be in place before anything imports lifeos.config.
os.environ.setdefault("LIFEOS_ENVIRONMENT", "test")
os.environ.setdefault("LIFEOS_DATABASE_NAME", "lifeos_test")
os.environ.setdefault("LIFEOS_DATABASE_USER", "lifeos")
os.environ.setdefault("LIFEOS_DATABASE_PASSWORD", "lifeos")
os.environ.setdefault("LIFEOS_REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("LIFEOS_PUBLIC_ORIGIN", "http://localhost:3000")
os.environ.setdefault("LIFEOS_WEBAUTHN_RP_ID", "localhost")
os.environ.setdefault("LIFEOS_COOKIE_SECURE", "false")
os.environ.setdefault("LIFEOS_LOG_FORMAT", "console")
os.environ.setdefault("LIFEOS_LOG_LEVEL", "WARNING")

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings, get_settings
from lifeos.db.base import Base
from lifeos.db.session import Database
from lifeos.main import create_app
from lifeos.modules.identity import service as identity
from lifeos.modules.identity.models import User
from lifeos.modules.llm.fake import FakeProvider

from .webauthn_soft import SoftAuthenticator

if TYPE_CHECKING:
    from lifeos.modules.guardian.context import Ctx

BACKEND_DIR = Path(__file__).resolve().parents[1]
ORIGIN = os.environ["LIFEOS_PUBLIC_ORIGIN"]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    """Fresh schema for the test session, built by the real migrations."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["configure_logging"] = False
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield


@pytest.fixture(scope="session")
async def database(settings: Settings) -> AsyncIterator[Database]:
    db = Database(settings.database_url, pool_size=2)
    yield db
    await db.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(database: Database) -> AsyncIterator[None]:
    yield
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    async with database.engine.begin() as conn:
        await conn.execute(text("SET LOCAL lifeos.audit_purge = 'on'"))
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def db(database: Database) -> AsyncIterator[AsyncSession]:
    async with database.sessionmaker() as session:
        yield session


@pytest.fixture
def fake_llm() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def app(settings: Settings, fake_llm: FakeProvider) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    application.state.llm = fake_llm
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": ORIGIN, "User-Agent": "pytest"},
    ) as http:
        yield http


@dataclass
class Owner:
    user: User
    enrollment_token: str
    authenticator: SoftAuthenticator


@pytest.fixture
async def owner(database: Database, settings: Settings) -> Owner:
    async with database.sessionmaker() as session, session.begin():
        user, issued = await identity.create_owner(
            session, settings, email="Owner@Example.org", display_name="Owner"
        )
    return Owner(
        user=user,
        enrollment_token=issued.token,
        authenticator=SoftAuthenticator(settings.webauthn_rp_id, ORIGIN),
    )


async def enroll(client: AsyncClient, owner: Owner, label: str = "Pixel") -> dict[str, object]:
    options = await client.post(
        "/v1/auth/passkeys/registration/options",
        json={"enrollment_token": owner.enrollment_token},
    )
    assert options.status_code == 200, options.text
    body = options.json()
    credential = owner.authenticator.create(body["public_key"])
    response = await client.post(
        "/v1/auth/passkeys/registration/verify",
        json={"ceremony_id": body["ceremony_id"], "credential": credential, "label": label},
    )
    assert response.status_code == 201, response.text
    result: dict[str, object] = response.json()
    return result


async def login(client: AsyncClient, authenticator: SoftAuthenticator) -> None:
    options = (await client.post("/v1/auth/passkeys/authentication/options")).json()
    response = await client.post(
        "/v1/auth/passkeys/authentication/verify",
        json={
            "ceremony_id": options["ceremony_id"],
            "credential": authenticator.get(options["public_key"]),
        },
    )
    assert response.status_code == 200, response.text


@pytest.fixture
async def logged_in(client: AsyncClient, owner: Owner) -> Owner:
    """Owner enrolled with one passkey; the client holds a session cookie."""
    await enroll(client, owner)
    return owner


@pytest.fixture
async def spaces(client: AsyncClient, logged_in: Owner) -> dict[str, str]:
    """Space ids by kind (`personal`, `professional`) for the logged-in owner."""
    me = (await client.get("/v1/auth/me")).json()
    return {s["kind"]: s["id"] for s in me["spaces"]}


async def enable_cloud(client: AsyncClient, space_id: str) -> None:
    response = await client.patch(f"/v1/spaces/{space_id}", json={"cloud_ai_enabled": True})
    assert response.status_code == 200, response.text


async def make_ctx(
    database: Database,
    session: AsyncSession,
    settings: Settings,
    user: User,
    llm: FakeProvider,
    *,
    principal: object | None = None,
    recent_auth: bool = True,
) -> "Ctx":
    from lifeos.modules.guardian.context import Ctx
    from lifeos.modules.guardian.principal import Principal, user_principal

    resolved = principal or await user_principal(session, user.id, recent_auth=recent_auth)
    assert isinstance(resolved, Principal)
    return Ctx(
        db=session, database=database, settings=settings, principal=resolved, user=user, llm=llm
    )
