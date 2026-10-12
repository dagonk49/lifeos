#!/usr/bin/env bash
# Disaster recovery: REPLACES the production database, and the encrypted document
# originals, with restic snapshots.
# Usage: restore.sh <db-snapshot|latest> <documents-snapshot|latest|none> --yes
# (`restic snapshots --tag documents` lists the document snapshots: take the one made just
# after the database snapshot; "none" keeps the current files.)
#
# The originals need the same lifeos_documents_key as when they were saved.
# After a restore, replay the erasures recorded in the audit trail after the snapshot date
# (docs/politique-de-donnees.md, section 2).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"
snapshot="${1:?database snapshot id or 'latest' required}"
documents="${2:?documents snapshot id, 'latest' or 'none' required}"
[[ "${3:-}" == "--yes" ]] || { echo "Refusing to run without --yes (this replaces all data)."; exit 1; }

psql_su() { docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 "$@"; }

docker compose stop api worker scheduler
psql_su -d postgres -qc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'lifeos' AND pid <> pg_backend_pid()"
psql_su -d postgres -qc "DROP DATABASE lifeos"
psql_su -d postgres -qc "CREATE DATABASE lifeos OWNER lifeos"
# Restored as superuser: original ownership (role `lifeos`, extensions) is preserved.
restic dump --host lifeos --tag db "$snapshot" lifeos-db.pgdump \
  | docker compose exec -T postgres pg_restore -U postgres -d lifeos --exit-on-error
echo "Database restored from snapshot $snapshot."
if [[ "$documents" != "none" ]]; then
  # Replaces the files of the documents volume (as the service user of the worker image).
  restic dump --host lifeos --tag documents "$documents" lifeos-documents.tar \
    | docker compose run --rm --no-deps -T --entrypoint sh worker \
      -c 'find /data/documents -mindepth 1 -delete && tar -C /data/documents -xf -'
  echo "Document originals restored from snapshot $documents."
fi
docker compose up -d
