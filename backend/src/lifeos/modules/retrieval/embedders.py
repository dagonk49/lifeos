"""Text embedders. Vectors never leave the server (ADR 0006).

All vectors have EMBEDDING_DIMS dimensions and are L2-normalised, so cosine distance is
meaningful. Models are described by a small spec: query/passage prefixes and whether the
output may be truncated (Matryoshka models).
"""

import asyncio
import hashlib
import math
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from lifeos.config import Settings

EMBEDDING_DIMS = 768
Kind = Literal["query", "passage"]


@dataclass(frozen=True)
class ModelSpec:
    query_prefix: str
    passage_prefix: str
    native_dims: int
    matryoshka: bool = False


MODEL_SPECS: dict[str, ModelSpec] = {
    "google/embeddinggemma-300m": ModelSpec(
        "task: search result | query: ", "title: none | text: ", 768, matryoshka=True
    ),
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": ModelSpec("", "", 768),
    "Qwen/Qwen3-Embedding-0.6B-Q": ModelSpec(
        "Instruct: Retrouver les notes personnelles qui répondent à la question\nQuery: ",
        "",
        1024,
        matryoshka=True,
    ),
}


class EmbeddingError(Exception):
    """The embedder is unavailable: callers fall back to full-text search."""


class Embedder(Protocol):
    name: str
    model: str

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]: ...


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def fit_dims(vector: list[float], spec: ModelSpec) -> list[float]:
    if len(vector) == EMBEDDING_DIMS:
        return normalize(vector)
    if len(vector) > EMBEDDING_DIMS and spec.matryoshka:
        return normalize(vector[:EMBEDDING_DIMS])
    raise EmbeddingError(f"model returns {len(vector)} dimensions, {EMBEDDING_DIMS} expected")


# --- Fake (tests, offline development) -------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    for suffix in ("ements", "ement", "ions", "ion", "es", "s", "e"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


class FakeEmbedder:
    """Deterministic hashed bag of stemmed words: texts sharing words are close."""

    name = "fake"
    model = "fake-bow-768"

    def __init__(self) -> None:
        self.calls = 0

    def _vector(self, text: str) -> list[float]:
        folded = unicodedata.normalize("NFKD", text.lower())
        folded = "".join(c for c in folded if not unicodedata.combining(c))
        vector = [0.0] * EMBEDDING_DIMS
        for word in _WORD.findall(folded):
            if len(word) < 3:
                continue
            digest = hashlib.blake2b(_stem(word).encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMS
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        return normalize(vector)

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        self.calls += 1
        return [self._vector(t) for t in texts]


# --- Local model (fastembed / ONNX Runtime) --------------------------------------------------


LOAD_RETRY_S = 300.0


class LocalEmbedder:
    """Loads the model lazily, once per process; inference runs in a worker thread."""

    name = "local"

    def __init__(self, settings: Settings) -> None:
        self.model = settings.embeddings_model
        self.spec = MODEL_SPECS.get(self.model)
        if self.spec is None:
            raise ValueError(f"unsupported embedding model {self.model!r}")
        self._settings = settings
        self._engine: Any = None
        self._failed_at: float | None = None
        self._lock = asyncio.Lock()

    def _load(self) -> Any:
        from fastembed import TextEmbedding  # optional dependency (extra "ml")

        token = self._settings.hf_token.get_secret_value() if self._settings.hf_token else ""
        if token.strip():
            os.environ.setdefault("HF_TOKEN", token.strip())
        return TextEmbedding(
            model_name=self.model,
            cache_dir=self._settings.embeddings_cache_dir,
            threads=self._settings.embeddings_threads,
        )

    async def load(self) -> None:
        """Loads the model now (downloading it into the cache if needed)."""
        async with self._lock:
            await self._ensure_loaded()

    async def _ensure_loaded(self) -> None:
        if self._engine is not None:
            return
        # A missing model (not downloaded yet) must not make every request wait for a
        # new attempt: fail fast until the retry delay has passed.
        if self._failed_at is not None and time.monotonic() - self._failed_at < LOAD_RETRY_S:
            raise EmbeddingError(f"{self.model} unavailable (last load failed)")
        try:
            self._engine = await asyncio.to_thread(self._load)
        except Exception as exc:  # download or runtime failure
            self._failed_at = time.monotonic()
            raise EmbeddingError(f"cannot load {self.model}: {exc}") from exc
        self._failed_at = None

    def _run(self, texts: list[str], kind: Kind) -> list[list[float]]:
        assert self.spec is not None
        prefix = self.spec.query_prefix if kind == "query" else self.spec.passage_prefix
        vectors = self._engine.embed([prefix + t for t in texts], batch_size=16)
        return [fit_dims([float(x) for x in v], self.spec) for v in vectors]

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        async with self._lock:  # one inference at a time: bounded CPU and RAM
            await self._ensure_loaded()
            return await asyncio.to_thread(self._run, texts, kind)


# --- Remote (the internal `ml` service) ------------------------------------------------------


# Passages per request (the ml service accepts at most 128). Small batches keep a user's
# query from waiting long behind background indexing (one inference at a time).
REMOTE_BATCH = 8
# A search must answer quickly, even without vectors; indexing can wait.
QUERY_TIMEOUT_S = 8.0
PASSAGE_TIMEOUT_S = 120.0


class RemoteEmbedder:
    name = "remote"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.model = settings.embeddings_model
        self._url = settings.ml_url.rstrip("/") + "/embed"
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(PASSAGE_TIMEOUT_S, connect=3.0)
        )

    async def _post(self, texts: list[str], kind: Kind) -> list[list[float]]:
        try:
            response = await self._client.post(
                self._url,
                json={"texts": texts, "kind": kind},
                timeout=QUERY_TIMEOUT_S if kind == "query" else PASSAGE_TIMEOUT_S,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingError(f"ml service unavailable: {type(exc).__name__}") from exc
        if payload.get("model") != self.model:
            raise EmbeddingError("ml service runs another model")
        vectors: list[list[float]] = payload["vectors"]
        if len(vectors) != len(texts) or any(len(v) != EMBEDDING_DIMS for v in vectors):
            raise EmbeddingError("ml service returned malformed vectors")
        return vectors

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), REMOTE_BATCH):
            vectors += await self._post(texts[start : start + REMOTE_BATCH], kind)
        return vectors


def build_embedder(settings: Settings) -> Embedder | None:
    if settings.embeddings_provider == "fake":
        return FakeEmbedder()
    if settings.embeddings_provider == "local":
        return LocalEmbedder(settings)
    if settings.embeddings_provider == "remote":
        return RemoteEmbedder(settings)
    return None
