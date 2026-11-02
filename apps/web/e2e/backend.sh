#!/usr/bin/env bash
# Backend for the E2E tests: the API, a worker (document reading, dictation, reminders) and
# the scheduler (periodic jobs, as in production), stopped together. Dramatiq has no HTTP
# endpoint: Playwright waits for the API. Journeys that cannot wait for a schedule run the
# job themselves (`lifeos-admin run-job`, e2e/helpers.ts).
set -euo pipefail
cd "$(dirname "$0")/../../../backend"
uv run dramatiq lifeos.workers.actors --processes 1 --threads 2 &
worker=$!
uv run lifeos-scheduler &
scheduler=$!
trap 'kill "$worker" "$scheduler" 2>/dev/null || true' EXIT
uv run uvicorn lifeos.main:create_app --factory --host 127.0.0.1 --port 8000
