#!/usr/bin/env bash
# Disaster recovery: REPLACES the production database with a restic snapshot.
# Usage: restore.sh <snapshot-id|latest> --yes
#
# After a restore, replay the erasures recorded in the audit trail after the snapshot date
# (docs/politique-de-donnees.md, section 2).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"
snapshot="${1:?snapshot id or 'latest' required}"
[[ "${2:-}" == "--yes" ]] || { echo "Refusing to run without --yes (this replaces all data)."; exit 1; }

psql_su() { docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 "$@"; }

docker compose stop api worker scheduler
psql_su -d postgres -qc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'lifeos' AND pid <> pg_backend_pid()"
psql_su -d postgres -qc "DROP DATABASE lifeos"
psql_su -d postgres -qc "CREATE DATABASE lifeos OWNER lifeos"
# Restored as superuser: original ownership (role `lifeos`, extensions) is preserved.
restic dump --host lifeos --tag db "$snapshot" lifeos-db.pgdump \
  | docker compose exec -T postgres pg_restore -U postgres -d lifeos --exit-on-error
docker compose up -d
echo "Database restored from snapshot $snapshot."
