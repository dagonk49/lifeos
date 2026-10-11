import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Computed, DateTime, ForeignKey, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012  (SQLAlchemy declarative convention)
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


def _now() -> datetime:
    return datetime.now(UTC)


class Timestamps:
    # Python-side values: no expired attribute (and no lazy load) after an UPDATE.
    created_at: Mapped[datetime] = mapped_column(default=_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        default=_now, server_default=func.now(), onupdate=_now
    )


class Owned:
    """Every business row belongs to one user and one space (docs/modele-de-donnees.md)."""

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )


def search_vector_column(expression: str) -> Mapped[Any]:
    """Generated French full-text column (`lifeos_fr`: french stemming + unaccent)."""
    return mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('lifeos_fr', {expression})", persisted=True),
        deferred=True,
    )
