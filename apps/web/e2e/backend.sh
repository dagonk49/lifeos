#!/usr/bin/env bash
# Backend for the E2E tests: the API and a worker (document reading, dictation), stopped
# together. Dramatiq has no HTTP endpoint: Playwright waits for the API.
set -euo pipefail
cd "$(dirname "$0")/../../../backend"
uv run dramatiq lifeos.workers.actors --processes 1 --threads 2 &
worker=$!
trap 'kill "$worker" 2>/dev/null || true' EXIT
uv run uvicorn lifeos.main:create_app --factory --host 127.0.0.1 --port 8000
