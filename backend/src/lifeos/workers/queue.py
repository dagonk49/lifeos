"""Enqueues Dramatiq messages by actor name, without importing the actors.

The API must not import `lifeos.workers.actors` (worker-only dependencies, logging setup,
job runtime): a message names its actor, and the worker resolves it. Enqueueing is best
effort: callers whose work is also picked up by a periodic job tolerate a failure.
"""

from typing import Any

import dramatiq

from lifeos.logging import get_logger
from lifeos.workers.broker import broker

log = get_logger(__name__)


def enqueue(actor_name: str, *args: Any, queue: str = "documents") -> bool:
    """Sends a message to `actor_name` on `queue`; False (logged) if the broker failed."""
    message: dramatiq.Message[Any] = dramatiq.Message(
        queue_name=queue, actor_name=actor_name, args=args, kwargs={}, options={}
    )
    try:
        broker.declare_queue(queue)
        broker.enqueue(message)
    except Exception as exc:  # broker unreachable: the periodic job catches up
        log.warning("enqueue_failed", actor=actor_name, queue=queue, error=type(exc).__name__)
        return False
    return True
