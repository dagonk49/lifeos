"""Idempotency-Key support for creation endpoints (docs/conventions-api.md)."""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import ForeignKey, Integer, Text, delete
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.core.errors import PROBLEM_MEDIA_TYPE, ProblemError
from lifeos.core.security import utcnow
from lifeos.db.base import Base

TTL = timedelta(hours=24)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    method: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)


def _hash(method: str, path: str, body: BaseModel | None) -> str:
    payload = json.dumps(
        [method, path, jsonable_encoder(body) if body is not None else None], sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def idempotent(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    key: str | None,
    method: str,
    path: str,
    body: BaseModel | None,
    run: Callable[[], Awaitable[BaseModel]],
    status_code: int = 201,
) -> JSONResponse:
    """Runs `run` once per (user, key); replays return the stored response."""
    if not key:
        result = await run()
        return JSONResponse(jsonable_encoder(result), status_code=status_code)
    if len(key) > 128:
        raise ProblemError(400, "idempotency_key_invalid", "Idempotency key too long")
    request_hash = _hash(method, path, body)
    now = utcnow()
    inserted = await session.execute(
        insert(IdempotencyKey)
        .values(
            user_id=user_id,
            key=key,
            method=method,
            path=path,
            request_hash=request_hash,
            created_at=now,
            expires_at=now + TTL,
        )
        .on_conflict_do_nothing()
        .returning(IdempotencyKey.key)
    )
    if inserted.scalar_one_or_none() is None:
        existing = await session.get(IdempotencyKey, (user_id, key))
        assert existing is not None
        if existing.request_hash != request_hash:
            raise ProblemError(
                409, "idempotency_key_reused", "Idempotency key reused with another request"
            )
        if existing.response_status is None:
            raise ProblemError(409, "idempotency_key_in_use", "Request still in progress")
        # A stored problem: the object was erased since (privacy.erasure), never re-created.
        media_type = PROBLEM_MEDIA_TYPE if existing.response_status >= 400 else None
        return JSONResponse(
            existing.response_body, status_code=existing.response_status, media_type=media_type
        )
    result = await run()
    body_json = jsonable_encoder(result)
    record = await session.get(IdempotencyKey, (user_id, key))
    assert record is not None
    record.response_status = status_code
    record.response_body = body_json
    return JSONResponse(body_json, status_code=status_code)


async def purge_expired_keys(session: AsyncSession) -> int:
    result = await session.execute(
        delete(IdempotencyKey).where(IdempotencyKey.expires_at < utcnow())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
