"""Dramatiq broker. Must be imported before any actor is declared (ADR 0004)."""

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware.asyncio import AsyncIO

from lifeos.config import get_settings
from lifeos.workers.metrics import MetricsServer

# Prefix of every Redis key of the broker: `lifeos:<queue>` (waiting messages),
# `lifeos:<queue>.DQ` (retries), `lifeos:<queue>.XQ` (dead letters).
NAMESPACE = "lifeos"


def _build_broker() -> dramatiq.Broker:
    settings = get_settings()
    broker: dramatiq.Broker
    if settings.environment == "test":
        broker = StubBroker()
    else:
        broker = RedisBroker(url=settings.redis_url, namespace=NAMESPACE)  # type: ignore[no-untyped-call]
    broker.add_middleware(AsyncIO())
    # Only acts in a worker process (after_worker_boot): the API never boots a worker.
    if settings.environment != "test" and settings.worker_metrics_port:
        broker.add_middleware(MetricsServer(settings.worker_metrics_port))
    return broker


broker = _build_broker()
dramatiq.set_broker(broker)
