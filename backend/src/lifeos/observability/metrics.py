"""Prometheus metrics (docs/exploitation.md §5).

Labels never carry user data: only route templates, providers, purposes, task names, queue
names, dependency names, document purposes and the deployed version. A test scrapes the API
and checks every label name against an allowlist.
"""

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Histogram(
    "lifeos_http_request_duration_seconds",
    "HTTP request duration by route template",
    ["method", "route", "status"],
)

LLM_CALLS = Counter(
    "lifeos_llm_calls_total",
    "Cloud LLM calls by purpose and outcome",
    ["provider", "purpose", "outcome"],
)

LLM_TOKENS = Counter(
    "lifeos_llm_tokens_total",
    "Cloud LLM tokens by direction",
    ["provider", "direction"],
)

# Incremented in the worker process, scraped from its own endpoint (workers/metrics.py).
# `task`, not `job`: Prometheus reserves `job` for the scrape job of the target.
# Worker process: Web Push sends by outcome (sent, retried, failed, expired), ADR 0019.
PUSH_SENDS = Counter(
    "lifeos_push_sends_total",
    "Web Push sends by outcome",
    ["outcome"],
)

JOB_RUNS = Counter(
    "lifeos_job_runs_total",
    "Background task runs by task and outcome",
    ["task", "outcome"],
)

# Worker process: set at boot, then by the `heartbeat` task, sent every minute by the
# scheduler through the given queue. A stale value means the scheduler, the queue or the
# worker is stuck (alert TachesPlanifieesBloquees).
WORKER_HEARTBEAT = Gauge(
    "lifeos_worker_heartbeat_timestamp_seconds",
    "Unix time of the last heartbeat task run by the worker",
    ["queue"],
)

# API process: refreshed every LIFEOS_METRICS_REFRESH_SECONDS (api/gauges.py).
QUEUE_MESSAGES = Gauge(
    "lifeos_queue_messages",
    "Messages waiting in a task queue",
    ["queue"],
)
QUEUE_DELAYED = Gauge(
    "lifeos_queue_delayed_messages",
    "Messages of a task queue waiting for a retry",
    ["queue"],
)
QUEUE_DEAD_LETTERS = Gauge(
    "lifeos_queue_dead_letters",
    "Messages abandoned after their retries (kept 7 days by the broker)",
    ["queue"],
)
DOCUMENTS_BACKLOG = Gauge(
    "lifeos_documents_backlog",
    "Documents waiting to be read, or dictations waiting to be transcribed",
    ["purpose"],
)
DOCUMENTS_BACKLOG_OLDEST = Gauge(
    "lifeos_documents_backlog_oldest_seconds",
    "Age of the oldest document waiting to be read (or dictation to be transcribed)",
    ["purpose"],
)
DEPENDENCY_UP = Gauge(
    "lifeos_dependency_up",
    "Whether an internal dependency of the API answers (1) or not (0)",
    ["dependency"],
)
BUILD_INFO = Gauge(
    "lifeos_build_info",
    "Running LifeOS version and deployed image tag (value: always 1)",
    ["version", "revision"],
)

SECURITY_EVENTS = Counter(
    "lifeos_security_events_total",
    "Refusals and alerts seen at the HTTP layer by kind, each one counted (audited or not)",
    ["kind"],
)
