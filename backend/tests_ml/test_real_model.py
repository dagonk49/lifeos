"""Checks the real embedding model (CI job `ml-model`; needs `lifeos-ml download` first).

Runs offline on a read-only model cache, like the `ml` service in production. Prints the
distances used to calibrate the memory-quality thresholds, and the throughput.
"""

import os
import time

import pytest

from lifeos.config import Settings
from lifeos.modules.retrieval.embedders import EMBEDDING_DIMS, LocalEmbedder

pytestmark = pytest.mark.skipif(
    os.environ.get("LIFEOS_TEST_REAL_MODEL") != "1",
    reason="needs the downloaded model (LIFEOS_TEST_REAL_MODEL=1)",
)

PASSAGES = [
    "Rendez-vous chez le dentiste mardi à 14 h pour un détartrage.",
    "J'ai couru 10 kilomètres au bord du lac ce matin, en 55 minutes.",
    "La facture d'électricité de septembre s'élève à 82 euros.",
    "Réunion d'équipe : décision de livrer la version 2 en novembre.",
    "Ma sœur Claire arrive samedi avec ses deux enfants.",
]
QUERIES = {
    "Quand est mon prochain soin dentaire ?": 0,
    "Quelle distance ai-je parcourue en courant ?": 1,
    "Combien ai-je payé pour l'énergie ?": 2,
    "Qu'a-t-on décidé pour la prochaine version ?": 3,
    "Qui vient me rendre visite ce week-end ?": 4,
}


def _distance(u: list[float], v: list[float]) -> float:
    return 1.0 - sum(x * y for x, y in zip(u, v, strict=True))


@pytest.fixture(scope="module")
def embedder() -> LocalEmbedder:
    return LocalEmbedder(Settings())


async def test_french_queries_find_their_passage(embedder: LocalEmbedder) -> None:
    passages = await embedder.embed(PASSAGES, "passage")
    assert all(len(v) == EMBEDDING_DIMS for v in passages)
    assert all(abs(sum(x * x for x in v) - 1.0) < 1e-3 for v in passages)
    for query, expected in QUERIES.items():
        [vector] = await embedder.embed([query], "query")
        distances = [_distance(vector, p) for p in passages]
        print(f"{query!r}: {[round(d, 3) for d in distances]}")
        assert distances.index(min(distances)) == expected, query


async def test_memory_quality_distances(embedder: LocalEmbedder) -> None:
    pairs = {
        "duplicate": ("Je préfère courir le matin", "Le matin, je préfère aller courir"),
        "duplicate2": ("J'habite à Lyon", "Je vis à Lyon"),
        "update": ("J'habite à Lyon", "J'habite à Bordeaux"),
        "negation": ("J'aime le café", "Je n'aime pas le café"),
        "unrelated": ("J'aime le café", "Ma voiture est une Clio"),
    }
    distances = {}
    for name, (a, b) in pairs.items():
        va, vb = await embedder.embed([a, b], "passage")
        distances[name] = round(_distance(va, vb), 3)
    print(f"memory-quality distances: {distances}")
    assert distances["duplicate"] < distances["unrelated"]
    assert distances["negation"] < distances["unrelated"]
    assert distances["update"] < distances["unrelated"]


async def test_throughput(embedder: LocalEmbedder) -> None:
    texts = [f"{PASSAGES[i % len(PASSAGES)]} (note {i})" for i in range(64)]
    await embedder.embed(texts[:2], "passage")  # warm-up
    start = time.perf_counter()
    await embedder.embed(texts, "passage")
    elapsed = time.perf_counter() - start
    start = time.perf_counter()
    await embedder.embed(["Qu'ai-je fait la semaine dernière ?"], "query")
    query_ms = (time.perf_counter() - start) * 1000
    print(f"throughput: {len(texts) / elapsed:.1f} passages/s, one query: {query_ms:.0f} ms")
