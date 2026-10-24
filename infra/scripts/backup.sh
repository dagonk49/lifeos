#!/usr/bin/env bash
# Backup run with restic (>= 0.17): the encrypted document originals, then the database, then
# the configuration, three snapshots tagged with the same run id; then the check of the
# originals against the archive, retention, the off-site copy and the metrics.
#
#   infra/scripts/backup.sh          (nightly: lifeos-backup.timer, see install-backups.sh)
#
# Settings: environment, or /etc/lifeos/backup.env (infra/backup.env.example):
#   RESTIC_REPOSITORY, RESTIC_PASSWORD_FILE    main repository (required)
#   RESTIC_COPY_REPOSITORY, RESTIC_COPY_PASSWORD_FILE
#                              off-site copy, same retention (recommended)
#   LIFEOS_HEALTHCHECK_URL     dead man's switch: pinged at start, on success, on failure
#   LIFEOS_METRICS_DIR         Prometheus textfile metrics (default /var/lib/lifeos/metrics)
#   LIFEOS_BACKUP_RUN          run id (default: the UTC time), LIFEOS_BACKUP_TAG one more tag:
#                              update.sh names its pre-update run, to restore it on a rollback
#
# Files first, then the database: a document deleted or purged meanwhile leaves at worst an
# extra file in the archive (swept after a restore), never a document without its original.
# A document uploaded meanwhile is reported, and is in the next backup.
# lifeos_documents_key is never backed up (only its fingerprint, in a tag): keep it apart
# (export-keys.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { sed -n '2,22p' "$0"; exit 0; }
load_backup_env
require_restic_env
require_tools restic jq docker curl flock sha256sum
take_lock

metrics="$(metrics_dir)/lifeos_backup.prom"
started="$(date +%s)"
run="${LIFEOS_BACKUP_RUN:-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$run" =~ ^[0-9A-Za-z-]{1,40}$ ]] || die "LIFEOS_BACKUP_RUN: letters, digits and dashes only."
[[ "${LIFEOS_BACKUP_TAG:-}" =~ ^[a-z0-9-]{0,32}$ ]] || die "LIFEOS_BACKUP_TAG: a-z, 0-9 and dashes."
work="$(mktemp -d)"
index="/tmp/lifeos-backup-$run.list"
created=()  # snapshots of this run: removed if it does not complete

finish() {
  local status=$?
  rm -rf "$work"
  docker compose exec -T worker rm -f "$index" >/dev/null 2>&1 || true
  local now
  now="$(date +%s)"
  metric_set "$metrics" lifeos_backup_duration_seconds "$((now - started))" \
    "Duration of the last LifeOS backup run."
  if [[ "$status" -ne 0 ]]; then
    if [[ "${#created[@]}" -gt 0 ]]; then
      # An incomplete run would leave a database without its originals (or the reverse).
      restic forget --retry-lock 5m -q "${created[@]}" >/dev/null 2>&1 \
        || echo "warning: could not remove the snapshots of the incomplete run $run" >&2
    fi
    metric_set "$metrics" lifeos_backup_last_failure_timestamp_seconds "$now" \
      "Unix time of the last failed LifeOS backup run."
    hc_ping "${LIFEOS_HEALTHCHECK_URL:-}" /fail
    echo "Backup run $run FAILED (status $status)." >&2
  fi
}
trap finish EXIT
hc_ping "${LIFEOS_HEALTHCHECK_URL:-}" /start

# snapshot KIND FILE COMMAND...: one snapshot of the command's output; prints its id. With
# --stdin-from-command, a failing command leaves no snapshot behind.
snapshot() {
  local kind="$1" file="$2"
  shift 2
  restic backup --retry-lock 30m --json -q "${tags[@]}" --tag "$kind" --stdin-filename "$file" \
    --stdin-from-command -- "$@" | jq -r 'select(.message_type == "summary") | .snapshot_id'
}

restic unlock -q  # stale locks only (a killed run), never those of a running process
version="$(git -C .. -c safe.directory='*' describe --tags --always --dirty 2>/dev/null || true)"
[[ -n "$version" ]] || version="$(sed -n 's/^version = "\(.*\)"$/\1/p' ../backend/pyproject.toml)"
revision="$(psql_su -d lifeos -tAc 'SELECT version_num FROM alembic_version')"
dockey="$(documents_key_fingerprint secrets/lifeos_documents_key)" \
  || die "infra/secrets/lifeos_documents_key is missing or empty."
tags=(--host lifeos --tag lifeos --tag "run:$run" --tag "version:$version" --tag "rev:$revision"
  --tag "dockey:$dockey")
[[ -z "${LIFEOS_BACKUP_TAG:-}" ]] || tags+=(--tag "$LIFEOS_BACKUP_TAG")
say "run $run: LifeOS $version, schema $revision, documents key $dockey"

# 1. Encrypted originals (already AES-256-GCM: unreadable without lifeos_documents_key). A file
# created or removed while tar reads makes it exit with status 1: harmless here (originals are
# written atomically); 2 or more is a failure. The list of archived files stays in the worker.
files_as_of="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# shellcheck disable=SC2016  # $1 and $? belong to the shell of the worker container
documents="$(snapshot documents lifeos-documents.tar docker compose exec -T worker sh -c \
  'tar -C /data/documents --exclude="*.tmp" --warning=no-file-changed --warning=no-file-removed \
     --index-file="$1" -cvf - .; [ "$?" -le 1 ]' sh "$index")"
[[ -n "$documents" ]] || die "no documents snapshot id"
created+=("$documents")
say "documents: snapshot ${documents:0:8}"

# 2. Database: one consistent pg_dump (if it fails, restic keeps no partial snapshot).
database="$(snapshot db lifeos-db.pgdump \
  docker compose exec -T postgres pg_dump -U postgres -d lifeos --format=custom)"
[[ -n "$database" ]] || die "no database snapshot id"
created+=("$database")
say "database: snapshot ${database:0:8}"

# 3. Configuration needed to rebuild the same instance (domain, WebAuthn RP ID, overlays):
# infra/.env holds no secret by design; the TLS file only names the certificate files.
config_files=(.env)
[[ -f traefik/dynamic/tls.yml ]] && config_files+=(traefik/dynamic/tls.yml)
config="$(snapshot config lifeos-config.tar tar -cf - "${config_files[@]}")"
[[ -n "$config" ]] || die "no config snapshot id"
say "config: snapshot ${config:0:8}"
created=()  # complete run: kept whatever happens next

# 4. Originals referenced by the database but absent from the archive (reported, never hidden:
# metric lifeos_backup_missing_originals, restore-check.sh fails on them).
docker compose exec -T worker cat "$index" > "$work/index"
missing=""
report="$(docker compose exec -T api lifeos-admin verify-data --json --files-as-of \
  "$files_as_of" --files-from - < "$work/index")" || true
if jq -e '.originals' <<<"$report" >/dev/null 2>&1; then
  missing="$(jq -r '.originals.missing' <<<"$report")"
  say "originals: $(jq -r '.originals | "\(.referenced) referenced, \(.missing) missing, " +
    "\(.recent) uploaded during the backup (next run)"' <<<"$report")"
  jq -r '.originals.missing_ids[] | "missing original: document \(.)"' <<<"$report" >&2
  jq -r '.problems[] | "warning: \(.)"' <<<"$report" >&2
fi
if [[ -n "$missing" ]]; then
  metric_set "$metrics" lifeos_backup_missing_originals "$missing" \
    "Originals referenced by the database and absent from the last backup."
else
  echo "warning: originals not checked (is the api service running?)" >&2
fi
now="$(date +%s)"
metric_set "$metrics" lifeos_backup_last_success_timestamp_seconds "$now" \
  "Unix time of the last successful LifeOS backup (database, originals, configuration)."

# 5. Retention (docs/politique-de-donnees.md), then a light check of the repository structure
# (the data itself is read by backup-verify.sh, monthly).
apply_retention
restic check --retry-lock 30m -q
primary_is_offsite && metric_set "$metrics" lifeos_backup_offsite_last_success_timestamp_seconds \
  "$now" "Unix time of the last LifeOS backup present off this machine."

# 6. Off-site copy: same snapshots, same retention (deleted data leaves it as fast).
if [[ -n "${RESTIC_COPY_REPOSITORY:-}" ]]; then
  primary="$RESTIC_REPOSITORY"
  primary_password="$RESTIC_PASSWORD_FILE"
  (
    use_copy_repository
    restic unlock -q
    restic copy --retry-lock 30m -q --host lifeos --from-repo "$primary" \
      --from-password-file "$primary_password"
    apply_retention
    restic check --retry-lock 30m -q
  )
  metric_set "$metrics" lifeos_backup_offsite_last_success_timestamp_seconds "$(date +%s)" \
    "Unix time of the last LifeOS backup present off this machine."
  say "off-site copy: up to date"
fi
hc_ping "${LIFEOS_HEALTHCHECK_URL:-}"
say "backup run $run complete"
