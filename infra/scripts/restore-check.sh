#!/usr/bin/env bash
# Restore test: restores the latest database snapshot into a scratch database, checks it,
# then drops it. Production data is not touched. Run it regularly (e.g. weekly).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"

scratch=lifeos_restore_check
psql_su() { docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 "$@"; }

psql_su -d postgres -qc "DROP DATABASE IF EXISTS $scratch"
psql_su -d postgres -qc "CREATE DATABASE $scratch"
trap 'psql_su -d postgres -qc "DROP DATABASE IF EXISTS $scratch" >/dev/null' EXIT

restic dump --host lifeos --tag db latest lifeos-db.pgdump \
  | docker compose exec -T postgres pg_restore -U postgres -d "$scratch" --exit-on-error

version="$(psql_su -d "$scratch" -tAc 'SELECT version_num FROM alembic_version')"
users="$(psql_su -d "$scratch" -tAc 'SELECT count(*) FROM users')"
audit="$(psql_su -d "$scratch" -tAc 'SELECT count(*) FROM audit_events')"
echo "restore OK: schema revision $version, $users user(s), $audit audit event(s)"
