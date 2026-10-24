#!/usr/bin/env bash
# Restore test, production untouched: restores one backup run (default: the latest) into a
# scratch database and a scratch volume, then checks it with `lifeos-admin verify-data`:
# schema, counts, search, and every original present, decrypted with THIS server's key and
# identical to what the database recorded.
#
#   infra/scripts/restore-check.sh [latest|RUN-ID|SNAPSHOT-ID] [--offsite]
#
#   --offsite   test the off-site copy (RESTIC_COPY_REPOSITORY) instead of the main repository
#
# Weekly (lifeos-restore-check.timer) and monthly from the off-site copy
# (lifeos-restore-check-offsite.timer). Any problem, missing originals included, makes it fail
# (an alert follows under systemd). On success it writes
# lifeos_restore_check_last_success_timestamp_seconds to LIFEOS_METRICS_DIR.
# Optional dead man's switch: LIFEOS_RESTORE_CHECK_HEALTHCHECK_URL.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh

selector=latest offsite=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --offsite) offsite=1; shift ;;
    -h | --help) sed -n '2,17p' "$0"; exit 0 ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) selector="$1"; shift ;;
  esac
done
load_backup_env
require_restic_env
require_tools restic jq docker flock sha256sum
repository="main repository"
if [[ "$offsite" -eq 1 ]]; then
  use_copy_repository
  repository="off-site copy"
fi
take_lock
healthcheck="${LIFEOS_RESTORE_CHECK_HEALTHCHECK_URL:-}"
hc_ping "$healthcheck" /start

scratch=lifeos_restore_check
project="$(compose_json | jq -r .name)"
image="$(compose_json | jq -r .services.worker.image)"
volume="${project}_restore_check"
cleanup() {
  local status=$?
  psql_su -d postgres -qc "DROP DATABASE IF EXISTS $scratch" >/dev/null 2>&1 || true
  docker volume rm -f "$volume" >/dev/null 2>&1 || true
  [[ "$status" -eq 0 ]] || hc_ping "$healthcheck" /fail
}
trap cleanup EXIT

pair="$(pick_pair "$selector")" || die "no backup run matches '$selector' in the $repository."
field() { jq -r --arg f "$1" '.[$f] // empty' <<<"$pair"; }
run="$(field run)"
documents="$(field documents)"
say "checking run ${run:-(legacy)} from the $repository: database $(field db_short)" \
  "of $(field time), documents ${documents:0:8}"
[[ "$(field legacy)" == "true" ]] && echo "warning: snapshot made before run tags: paired by time" >&2
[[ -n "$documents" ]] || die "this run has no documents snapshot (incomplete pair)."
dockey="$(field dockey)"
local_key="$(documents_key_fingerprint secrets/lifeos_documents_key)" \
  || die "infra/secrets/lifeos_documents_key is missing: the originals cannot be checked."
if [[ -n "$dockey" && "$dockey" != "$local_key" ]]; then
  die "the documents key of this server ($local_key) is not the one of the backup ($dockey):" \
    "overwritten or replaced? Restore the saved key (export-keys.sh, docs/restauration.md)."
fi

psql_su -d postgres -qc "DROP DATABASE IF EXISTS $scratch"
psql_su -d postgres -qc "CREATE DATABASE $scratch"
restic dump --retry-lock 30m "$(field db)" lifeos-db.pgdump \
  | docker compose exec -T postgres pg_restore -U postgres -d "$scratch" --exit-on-error

# The archive is extracted by the service user, on a new volume mounted where the image owns
# the directory (so it is writable), without network.
docker volume rm -f "$volume" >/dev/null 2>&1 || true
docker volume create "$volume" >/dev/null
restic dump --retry-lock 30m "$documents" lifeos-documents.tar \
  | docker run --rm -i --network none --read-only -v "$volume:/data/documents" \
    --entrypoint tar "$image" -C /data/documents -xf -

# The api image has the documents key (Docker secret) and reaches the database server.
status=0
docker compose run --rm --no-deps -T -e LIFEOS_DATABASE_NAME="$scratch" \
  -v "$volume:/restore:ro" api lifeos-admin verify-data --documents-dir /restore \
  --files-as-of "$(field documents_time)" || status=$?
if [[ "$status" -ne 0 ]]; then
  echo "Restore check FAILED (verify-data status $status): see the report above." >&2
  exit 1
fi
metrics="$(metrics_dir)/lifeos_restore_check.prom"
metric_set "$metrics" lifeos_restore_check_last_success_timestamp_seconds "$(date +%s)" \
  "Unix time of the last successful LifeOS restore test."
if [[ "$offsite" -eq 1 ]]; then
  metric_set "$metrics" lifeos_restore_check_offsite_last_success_timestamp_seconds \
    "$(date +%s)" "Unix time of the last successful LifeOS restore test from the off-site copy."
fi
hc_ping "$healthcheck"
say "restore check OK (run ${run:-legacy}, $repository)"
