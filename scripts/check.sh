#!/usr/bin/env bash
# Runs the same checks as the CI, locally. Usage: scripts/check.sh [--e2e]
# Requires PostgreSQL (database lifeos_test, and lifeos_e2e for --e2e) and Redis.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
# Same database settings as the CI (overridable).
export LIFEOS_DATABASE_NAME="${LIFEOS_DATABASE_NAME:-lifeos_test}"
export LIFEOS_DATABASE_USER="${LIFEOS_DATABASE_USER:-lifeos}"
export LIFEOS_DATABASE_PASSWORD="${LIFEOS_DATABASE_PASSWORD:-lifeos}"

echo "== backend"
cd "$root/backend"
uv run ruff check .
uv run ruff format --check .
uv run ruff check --config pyproject.toml ../infra/scripts/lib/recette.py
uv run ruff format --config pyproject.toml --check ../infra/scripts/lib/recette.py
uv run mypy
uv run mypy ../infra/scripts/lib/recette.py
uv run pytest -q
uv run alembic upgrade head
uv run alembic check

echo "== web"
cd "$root/apps/web"
pnpm -s typecheck
pnpm -s test
NEXT_TELEMETRY_DISABLED=1 pnpm -s build >/dev/null

echo "== infra"
shellcheck "$root"/infra/scripts/*.sh "$root"/infra/scripts/lib/*.sh \
  "$root"/infra/postgres/init/*.sh "$root"/infra/prometheus/*.sh "$root"/scripts/*.sh \
  "$root"/apps/web/e2e/*.sh
if command -v systemd-analyze >/dev/null; then
  units="$(mktemp -d)"
  for unit in "$root"/infra/systemd/*; do sed "s|/opt/lifeos|$root|g" "$unit" > "$units/${unit##*/}"; done
  systemd-analyze verify "$units"/*.service "$units"/*.timer
  rm -rf "$units"
fi
# On a throw-away copy: services read infra/.env, which a fresh clone does not have.
infra_copy="$(mktemp -d)"
cp -r "$root/infra/." "$infra_copy"
cp "$infra_copy/.env.example" "$infra_copy/.env"
(cd "$infra_copy" && LIFEOS_DOMAIN=example.lan docker compose config --quiet \
  && LIFEOS_DOMAIN=example.lan docker compose -f compose.yaml -f compose.gpu.yaml config --quiet \
  && LIFEOS_DOMAIN=example.lan docker compose --profile monitoring config --quiet)
rm -rf "$infra_copy"
# Alert rules and their unit tests (the CI also runs scripts/test-monitoring.sh).
prometheus_image="$(sed -n 's/^ *image: \(prom\/prometheus:.*\)$/\1/p' "$root/infra/compose.yaml")"
docker run --rm --network none -v "$root/infra/prometheus:/etc/prometheus:ro" -w /etc/prometheus \
  --entrypoint promtool "$prometheus_image" test rules alerts.test.yml

if [[ "${1:-}" == "--e2e" ]]; then
  echo "== e2e"
  cd "$root/apps/web"
  pnpm -s e2e
fi
echo "All checks passed."
