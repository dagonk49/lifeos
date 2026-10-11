import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, UUIDPrimaryKey
from lifeos.modules.retrieval.embedders import EMBEDDING_DIMS

OBJECT_TYPES = ("fact", "memory", "journal", "task", "goal", "message")


class Embedding(UUIDPrimaryKey, Owned, Base):
    """Vector of one chunk of one object. Deleted with its object (database triggers)."""

    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("object_type", "object_id", "chunk", "model", name="uq_embeddings_chunk"),
        Index("ix_embeddings_object", "object_type", "object_id"),
        Index(
            "ix_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
    )

    object_type: Mapped[str] = mapped_column(Text)
    object_id: Mapped[uuid.UUID]
    chunk: Mapped[int] = mapped_column(Integer, default=0)
    # md5 of the whole source text: a changed object is re-embedded.
    content_hash: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(HALFVEC(EMBEDDING_DIMS))
    created_at: Mapped[datetime]
