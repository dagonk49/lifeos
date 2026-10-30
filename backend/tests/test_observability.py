"""Supervision (task 11): gauges of the API, worker metrics, heartbeat, label hygiene."""

import re
import time
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from prometheus_client import REGISTRY
from prometheus_client.parser import text_string_to_metric_families
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos import __version__
from lifeos.api import gauges
from lifeos.config import Settings
from lifeos.core.security import utcnow
from lifeos.modules.documents.models import Document

from .conftest import Owner

# Every label name a lifeos_* series may carry: never one that could hold user data.
ALLOWED_LABELS = {
    "method",
    "route",
    "status",
    "provider",
    "purpose",
    "outcome",
    "direction",
    "task",
    "queue",
    "dependency",
    # Security refusals (lifeos_security_events_total): a closed list of refusal kinds.
    "kind",
    "version",
    "revision",
    "le",  # histogram buckets
}
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def sample(name: str, **labels: str) -> float | None:
    return REGISTRY.get_sample_value(name, labels)


def health_transport(statuses: dict[str, int]) -> httpx.MockTransport:
    """Internal services by host name: their /healthz answers the given status."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/healthz"
        return httpx.Response(statuses[request.url.host])

    return httpx.MockTransport(handler)


async def add_document(
    db: AsyncSession, owner: Owner, space: str, *, purpose: str, status: str, age: timedelta
) -> None:
    document = Document(
        user_id=owner.user.id,
        space_id=uuid.UUID(space),
        title="Relevé de compte confidentiel",
        kind="image",
        mime="image/jpeg",
        size_bytes=1024,
        sha256=uuid.uuid4().hex * 2,
        purpose=purpose,
        status=status,
    )
    db.add(document)
    await db.flush()
    await db.execute(
        update(Document).where(Document.id == document.id).values(created_at=utcnow() - age)
    )
    await db.commit()


async def test_gauges_count_queues_backlog_and_dependencies(
    app: FastAPI,
    client: AsyncClient,
    db: AsyncSession,
    logged_in: Owner,
    spaces: dict[str, str],
    settings: Settings,
) -> None:
    personal = spaces["personal"]
    await add_document(
        db, logged_in, personal, purpose="receipt", status="pending", age=timedelta(hours=2)
    )
    await add_document(
        db, logged_in, personal, purpose="receipt", status="processing", age=timedelta(minutes=5)
    )
    await add_document(
        db, logged_in, personal, purpose="dictation", status="pending", age=timedelta(minutes=1)
    )
    await add_document(
        db, logged_in, personal, purpose="document", status="ready", age=timedelta(days=3)
    )
    redis: Redis = app.state.redis
    keys = ["lifeos:documents", "lifeos:documents.DQ", "lifeos:stt.XQ"]
    await redis.delete(*keys)
    try:
        await redis.rpush("lifeos:documents", "m1", "m2", "m3")
        await redis.rpush("lifeos:documents.DQ", "m4")
        await redis.zadd("lifeos:stt.XQ", {"m5": 1.0, "m6": 2.0})
        remote = settings.model_copy(
            update={
                "embeddings_provider": "remote",
                "stt_provider": "remote",
                "ml_url": "http://ml:8100",
                "stt_url": "http://stt:8200",
            }
        )
        async with httpx.AsyncClient(transport=health_transport({"ml": 200, "stt": 503})) as http:
            await gauges.refresh(app.state.db, redis, remote, http)
    finally:
        await redis.delete(*keys)

    assert sample("lifeos_queue_messages", queue="documents") == 3
    assert sample("lifeos_queue_delayed_messages", queue="documents") == 1
    assert sample("lifeos_queue_dead_letters", queue="stt") == 2
    assert sample("lifeos_queue_messages", queue="maintenance") == 0
    assert sample("lifeos_documents_backlog", purpose="receipt") == 2
    assert sample("lifeos_documents_backlog", purpose="dictation") == 1
    assert sample("lifeos_documents_backlog", purpose="document") == 0
    oldest = sample("lifeos_documents_backlog_oldest_seconds", purpose="receipt")
    assert oldest is not None and 7100 < oldest < 7300
    assert sample("lifeos_documents_backlog_oldest_seconds", purpose="document") == 0
    for dependency, up in {"database": 1, "redis": 1, "ml": 1, "stt": 0}.items():
        assert sample("lifeos_dependency_up", dependency=dependency) == up

    # What Prometheus scrapes: names and labels only, never a title, an e-mail or an id.
    body = (await client.get("/metrics/")).text
    assert "Relevé" not in body and "@" not in body
    for family in text_string_to_metric_families(body):
        for series in family.samples:
            if not series.name.startswith("lifeos_"):
                continue
            assert set(series.labels) <= ALLOWED_LABELS, series
            for value in series.labels.values():
                assert len(value) <= 64 and not UUID_PATTERN.search(value), series


async def test_unreachable_redis_is_reported_not_raised(app: FastAPI, settings: Settings) -> None:
    unreachable = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    try:
        async with httpx.AsyncClient(transport=health_transport({})) as http:
            await gauges.refresh(app.state.db, unreachable, settings, http)
    finally:
        await unreachable.aclose()
    assert sample("lifeos_dependency_up", dependency="redis") == 0
    assert sample("lifeos_dependency_up", dependency="database") == 1


def test_only_the_internal_services_in_use_are_checked(settings: Settings) -> None:
    disabled = settings.model_copy(
        update={
            "embeddings_provider": "disabled",
            "extraction_provider": "disabled",
            "stt_provider": "disabled",
        }
    )
    assert gauges.internal_services(disabled) == {}
    ocr_only = disabled.model_copy(update={"extraction_provider": "remote"})
    assert gauges.internal_services(ocr_only) == {"ml": ocr_only.ml_url}


async def test_build_info_names_the_version_and_the_image_tag(client: AsyncClient) -> None:
    body = (await client.get("/metrics/")).text
    assert f'lifeos_build_info{{revision="local",version="{__version__}"}} 1.0' in body


def test_worker_serves_its_metrics_once_booted(monkeypatch: pytest.MonkeyPatch) -> None:
    from lifeos.workers import metrics

    ports: list[int] = []
    monkeypatch.setattr(metrics, "start_http_server", lambda port: ports.append(port))
    before = time.time()
    metrics.MetricsServer(9101).after_worker_boot(None, None)
    assert ports == [9101]
    beat = sample("lifeos_worker_heartbeat_timestamp_seconds", queue="realtime")
    assert beat is not None and beat >= before

    def taken(port: int) -> None:
        raise OSError("address already in use")

    monkeypatch.setattr(metrics, "start_http_server", taken)
    metrics.MetricsServer(9101).after_worker_boot(None, None)  # logged, never fatal


def test_heartbeat_task_runs_every_minute_on_its_queue() -> None:
    from apscheduler.triggers.interval import IntervalTrigger

    from lifeos.workers import actors
    from lifeos.workers.broker import broker
    from lifeos.workers.queue import QUEUES
    from lifeos.workers.scheduler import build_scheduler

    jobs = {job.id: job for job in build_scheduler().get_jobs()}
    trigger = jobs["heartbeat"].trigger
    assert isinstance(trigger, IntervalTrigger)
    assert trigger.interval == timedelta(minutes=1)
    before = time.time()
    actors.heartbeat.fn()
    beat = sample("lifeos_worker_heartbeat_timestamp_seconds", queue=actors.heartbeat.queue_name)
    assert beat is not None and beat >= before
    # Every queue of every actor is measured by the gauges.
    used = {broker.get_actor(name).queue_name for name in broker.get_declared_actors()}
    assert used == set(QUEUES)


async def test_task_runs_are_counted_by_task_and_outcome() -> None:
    from lifeos.workers.jobs import run_task

    def runs(outcome: str) -> float:
        return sample("lifeos_job_runs_total", task="observability_test", outcome=outcome) or 0.0

    failed, succeeded = runs("failure"), runs("success")

    async def ok() -> int:
        return 1

    async def boom() -> Any:
        raise RuntimeError("boom")

    assert await run_task("observability_test", ok) == 1
    with pytest.raises(RuntimeError):
        await run_task("observability_test", boom)
    assert runs("success") == succeeded + 1
    assert runs("failure") == failed + 1
