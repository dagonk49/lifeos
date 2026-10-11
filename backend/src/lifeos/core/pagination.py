"""Keyset pagination on (created_at DESC, id DESC) — see docs/conventions-api.md."""

import base64
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, tuple_

from lifeos.core.errors import ProblemError

MAX_LIMIT = 200


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        created, row_id = json.loads(raw)
        return datetime.fromisoformat(created), uuid.UUID(row_id)
    except (ValueError, TypeError) as exc:
        raise ProblemError(400, "invalid_cursor", "Invalid pagination cursor") from exc


def keyset[S: Select[Any]](stmt: S, model: Any, cursor: str | None, limit: int) -> S:
    limit = max(1, min(limit, MAX_LIMIT))
    if cursor:
        created, row_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(model.created_at, model.id) < tuple_(created, row_id))
    return stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)


def page[R, T](rows: Sequence[R], limit: int, convert: Callable[[R], T]) -> Page[T]:
    limit = max(1, min(limit, MAX_LIMIT))
    items = list(rows[:limit])
    next_cursor = None
    if len(rows) > limit:
        last: Any = items[-1]
        next_cursor = encode_cursor(last.created_at, last.id)
    return Page[T](items=[convert(r) for r in items], next_cursor=next_cursor)
