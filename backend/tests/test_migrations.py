"""Migrations go down as well as up (task 12): what a rollback runs.

`infra/scripts/update.sh --rollback` runs the downgrades of the newer migrations; the test
session builds a fresh schema, so they would otherwise never run in CI. These tests use a
database of their own (`<test database>_migrations`): the session's schema is left alone.
"""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import URL, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from lifeos.config import Settings

from .conftest import BACKEND_DIR


async def _execute(url: URL, *statements: str, autocommit: bool = False) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT" if autocommit else None)
    try:
        async with engine.begin() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def _database_exists(admin: URL, name: str) -> bool:
    engine = create_async_engine(admin)
    try:
        async with engine.connect() as conn:
            found = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
    finally:
        await engine.dispose()
    return found is not None


async def _rows(url: URL, query: str, **params: Any) -> list[tuple[Any, ...]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return [tuple(row) for row in (await conn.execute(text(query), params)).all()]
    finally:
        await engine.dispose()


async def _seed(url: URL, ids: dict[str, uuid.UUID]) -> None:
    """An owner, a document kept local by hand, a pending LLM reservation (schema at head)."""
    statements = (
        "INSERT INTO users (id, email, display_name, role) "
        "VALUES (:user, 'rollback@example.org', 'Retour', 'owner')",
        "INSERT INTO spaces (id, user_id, kind, name) "
        "VALUES (:space, :user, 'personal', 'Personnel')",
        "INSERT INTO documents (id, user_id, space_id, title, kind, mime, size_bytes, sha256, "
        "status, local_only) VALUES (:document, :user, :space, 'Relevé', 'pdf', "
        "'application/pdf', 10, repeat('0', 64), 'ready', true)",
        "INSERT INTO llm_calls (id, user_id, purpose, provider, input_tokens, output_tokens, "
        "redactions, outcome, duration_ms, created_at, reserved_tokens) VALUES (:call, :user, "
        "'chat.reply', 'gemini', 0, 0, 0, 'reserved', 0, now(), 2048)",
    )
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            for statement in statements:
                await conn.execute(text(statement), ids)
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def migrations_url(settings: Settings) -> Iterator[URL]:
    url = settings.database_url.set(database=f"{settings.database_name}_migrations")
    admin = settings.database_url.set(database="postgres")
    name = str(url.database)
    created = False
    if not asyncio.run(_database_exists(admin, name)):
        asyncio.run(_execute(admin, f'CREATE DATABASE "{name}"', autocommit=True))
        created = True
    try:
        # pgvector needs a superuser (the CI's database user is one).
        asyncio.run(_execute(url, "CREATE EXTENSION IF NOT EXISTS vector"))
    except DBAPIError:
        pytest.skip(f"a superuser must create {name} with the vector extension")
    yield url
    if created:
        asyncio.run(_execute(admin, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True))


@pytest.fixture(scope="module")
def alembic_config(migrations_url: URL) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["configure_logging"] = False
    config.attributes["database_url"] = migrations_url
    command.downgrade(config, "base")
    return config


def test_every_migration_goes_down_and_up_again(alembic_config: Config) -> None:
    script = ScriptDirectory.from_config(alembic_config)
    revisions = list(reversed(list(script.walk_revisions())))
    assert revisions[0].down_revision is None
    assert len(script.get_heads()) == 1, "migrations must stay sequential (one head)"
    for revision in revisions:
        command.upgrade(alembic_config, revision.revision)
        command.downgrade(alembic_config, "-1")
        command.upgrade(alembic_config, revision.revision)
    # Built step by step, the schema is still exactly the models' one (`alembic check`).
    command.check(alembic_config)


def test_a_rollback_keeps_the_data_and_its_protections(
    alembic_config: Config, migrations_url: URL
) -> None:
    """Back to 0006 (before the LLM reservations and the manual refusal), then up again."""
    command.upgrade(alembic_config, "head")
    ids = {name: uuid.uuid4() for name in ("user", "space", "document", "call")}
    asyncio.run(_seed(migrations_url, ids))
    user, document, call = ids["user"], ids["document"], ids["call"]

    command.downgrade(alembic_config, "0006")
    # The manual refusal becomes a detected category: the document still never leaves.
    assert asyncio.run(
        _rows(
            migrations_url, "SELECT title, sensitivity FROM documents WHERE id = :id", id=document
        )
    ) == [("Relevé", ["identity"])]
    # A pending reservation becomes a failed call: it still counts against the caps.
    assert asyncio.run(
        _rows(migrations_url, "SELECT outcome, error_code FROM llm_calls WHERE id = :id", id=call)
    ) == [("error", "llm_reservation_abandoned")]

    command.upgrade(alembic_config, "head")
    assert asyncio.run(
        _rows(
            migrations_url,
            "SELECT u.display_name, d.sensitivity FROM users u JOIN documents d "
            "ON d.user_id = u.id WHERE u.id = :id",
            id=user,
        )
    ) == [("Retour", ["identity"])]
    command.downgrade(alembic_config, "base")
