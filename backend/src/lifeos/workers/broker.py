"""Dramatiq broker. Must be imported before any actor is declared (ADR 0004)."""

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware.asyncio import AsyncIO

from lifeos.config import get_settings


def _build_broker() -> dramatiq.Broker:
    settings = get_settings()
    broker: dramatiq.Broker
    if settings.environment == "test":
        broker = StubBroker()
    else:
        broker = RedisBroker(url=settings.redis_url, namespace="lifeos")  # type: ignore[no-untyped-call]
    broker.add_middleware(AsyncIO())
    return broker


broker = _build_broker()
dramatiq.set_broker(broker)
