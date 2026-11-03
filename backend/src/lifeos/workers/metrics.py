"""Metrics endpoint of the worker process (docs/exploitation.md §5).

Task runs (lifeos_job_runs_total) and the heartbeat are counted in the worker process, which
serves them itself: Prometheus scrapes `worker:<LIFEOS_WORKER_METRICS_PORT>` on the internal
network. One port per process: the worker runs with `--processes 1` (infra/compose.yaml).
"""

import time
from typing import Any

import dramatiq
from prometheus_client import start_http_server

from lifeos.logging import get_logger
from lifeos.observability.metrics import WORKER_HEARTBEAT

log = get_logger("lifeos.worker_metrics")
# Queue of the `heartbeat` task (workers/actors.py, sent every minute by the scheduler).
HEARTBEAT_QUEUE = "realtime"


def beat() -> None:
    """Marks the worker alive and its heartbeat queue flowing, now."""
    WORKER_HEARTBEAT.labels(HEARTBEAT_QUEUE).set(time.time())


class MetricsServer(dramatiq.Middleware):
    """Serves the default Prometheus registry once the worker process has booted."""

    def __init__(self, port: int) -> None:
        self.port = port

    def after_worker_boot(self, broker: dramatiq.Broker, worker: Any) -> None:
        try:
            start_http_server(self.port)  # daemon thread, every interface of the container
        except OSError as exc:  # a second process in the same container: no metrics there
            log.warning("worker_metrics_unavailable", port=self.port, error=type(exc).__name__)
            return
        # The series exists from boot: a heartbeat that never comes is then seen as stale.
        beat()
        log.info("worker_metrics_listening", port=self.port)
