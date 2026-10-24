from prometheus_client import Counter, Histogram

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

JOB_RUNS = Counter(
    "lifeos_job_runs_total",
    "Background job runs by job and outcome",
    ["job", "outcome"],
)

SECURITY_EVENTS = Counter(
    "lifeos_security_events_total",
    "Refusals and alerts seen at the HTTP layer by kind, each one counted (audited or not)",
    ["kind"],
)
