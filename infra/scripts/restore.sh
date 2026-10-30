#!/usr/bin/env bash
# Disaster recovery: REPLACES the database and the encrypted document originals of this server
# with one backup run (database and documents snapshots of the same run), applies the
# migrations of this code, then checks everything (lifeos-admin verify-data).
# On a new machine, follow docs/restauration.md (or restore-drill.sh) first.
#
#   infra/scripts/restore.sh latest|RUN-ID|DB-SNAPSHOT-ID --yes [options]
#   infra/scripts/restore.sh DB-SNAPSHOT DOCUMENTS-SNAPSHOT|latest|none --yes   (former form)
#
#   --offsite          restore from the off-site copy (RESTIC_COPY_REPOSITORY)
#   --no-documents     database only: keep the current originals
#   --no-safety-dump   do not save the current database first (blank machine)
#   --no-replay        do not erase again what was erased since the backup (to recover an
#                      object deleted by mistake: docs/restauration.md §3, étape 10)
#
# Nothing is replaced before both archives are fetched and read, the documents key is
# compared with the backup's (fingerprint tag), and the backup's schema is known to this code.
# The current database is first saved to LIFEOS_SAFETY_DIR (default /var/backups/lifeos), with
# the list of what its audit trail shows was erased since the backup (identifiers only): these
# erasures are replayed once the backup is restored, so that nothing erased comes back
# (docs/politique-de-donnees.md §2).
# Exit status: 0 restored and checked; 2 restored, with warnings (missing originals, listed,
# docs/restauration.md; no passkey); 1 failed, or the erasures made since the backup were not
# replayed.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh

positional=() assume_yes=0 offsite=0 with_documents=1 safety_dump=1 replay=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) assume_yes=1; shift ;;
    --offsite) offsite=1; shift ;;
    --no-documents) with_documents=0; shift ;;
    --no-safety-dump) safety_dump=0; shift ;;
    --no-replay) replay=0; shift ;;
    -h | --help) sed -n '2,24p' "$0"; exit 0 ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) positional+=("$1"); shift ;;
  esac
done
[[ "${#positional[@]}" -ge 1 ]] || die "which backup? 'latest', a run id or a snapshot id (see --help)."
[[ "$assume_yes" -eq 1 ]] || die "refusing to run without --yes (this replaces all data)."
documents_choice="${positional[1]:-}"
[[ "$documents_choice" == "none" ]] && with_documents=0

load_backup_env
require_restic_env
require_tools restic jq docker flock sha256sum
repository="main repository"
if [[ "$offsite" -eq 1 ]]; then
  use_copy_repository
  repository="off-site copy"
fi
take_lock

# Behind an existing reverse proxy, compose.proxy.yaml needs its external network.
if grep -qs '^COMPOSE_FILE=.*compose\.proxy\.yaml' .env; then
  network="$(sed -n 's/^LIFEOS_PROXY_NETWORK=//p' .env)"
  network="${network:-lifeos-proxy}"
  docker network inspect "$network" >/dev/null 2>&1 \
    || die "behind a reverse proxy: create the Docker network first (docker network create" \
      "$network, or install.sh --behind-proxy), then connect the proxy to it."
fi

pair="$(pick_pair "${positional[0]}")" \
  || die "no backup run matches '${positional[0]}' in the $repository."
field() { jq -r --arg f "$1" '.[$f] // empty' <<<"$pair"; }
database="$(field db)"
documents="$(field documents)"
documents_time="$(field documents_time)"
if [[ -n "$documents_choice" && "$documents_choice" != "none" ]]; then
  # Former form: the documents snapshot is named explicitly.
  if [[ "$documents_choice" == "latest" ]]; then
    documents="$(restic snapshots --host lifeos --tag documents --json \
      | jq -r 'sort_by(.time) | last | .id // empty')"
  else
    documents="$documents_choice"
  fi
  documents_time=""
  [[ "$documents" == "$(field documents)" ]] \
    || echo "warning: documents snapshot chosen by hand, not the one of the same run" >&2
fi
run="$(field run)"
cat <<EOF
Backup run ${run:-(legacy)} from the $repository:
  database   $(field db_short) of $(field time) (LifeOS $(field version), schema $(field rev))
  documents  ${documents:-none}$([[ "$with_documents" -eq 1 ]] || echo " (not restored)")
EOF
[[ "$(field legacy)" == "true" ]] && echo "warning: snapshot made before run tags: paired by time" >&2
if [[ "$with_documents" -eq 1 ]]; then
  [[ -n "$documents" ]] \
    || die "this run has no documents snapshot: --no-documents to restore the database only."
  local_key="$(documents_key_fingerprint secrets/lifeos_documents_key)" \
    || die "infra/secrets/lifeos_documents_key is missing: install the saved key first" \
      "(docs/restauration.md), or --no-documents."
  dockey="$(field dockey)"
  if [[ -n "$dockey" && "$dockey" != "$local_key" ]]; then
    die "infra/secrets/lifeos_documents_key ($local_key) is not the key of this backup" \
      "($dockey): the originals would be unreadable. Install the saved key."
  fi
fi

work="" stopped=0 running=()
# Once the services are stopped, they are restarted whatever happens (the error is still
# reported): those that were running, or all of them on a blank machine.
finish() {
  local status=$?
  [[ -z "$work" ]] || rm -rf "$work"
  if [[ "$stopped" -eq 1 && "${#running[@]}" -gt 0 ]]; then
    docker compose up -d "${running[@]}" || true
  elif [[ "$stopped" -eq 1 ]]; then
    docker compose up -d || true
  fi
  exit "$status"
}
trap finish EXIT

# The database server only (a blank machine has nothing running yet).
docker compose up -d --wait postgres redis

# Both archives are fetched and read BEFORE anything is replaced: a wrong choice must not
# leave an empty database or documents volume behind. Large files: not in /tmp (tmpfs).
work="$(mktemp -d -p "${LIFEOS_RESTORE_TMPDIR:-/var/tmp}" lifeos-restore.XXXXXX)"
restic dump --retry-lock 30m "$database" lifeos-db.pgdump > "$work/db.pgdump"
docker compose exec -T postgres pg_restore --list < "$work/db.pgdump" > /dev/null
revision="$(docker compose exec -T postgres pg_restore --data-only --table=alembic_version -f - \
  < "$work/db.pgdump" | awk '/^COPY .*alembic_version/ { getline; print; exit }')"
known="$(sed -n 's/^revision[^=]*= *"\([^"]*\)".*/\1/p' ../backend/migrations/versions/*.py)"
grep -qx -- "$revision" <<<"$known" \
  || die "the backup's schema (${revision:-none}) is unknown to this code: it was made by a newer" \
    "LifeOS. Check out its version first: git checkout $(field version)"
if [[ "$with_documents" -eq 1 ]]; then
  restic dump --retry-lock 30m "$documents" lifeos-documents.tar > "$work/documents.tar"
  originals="$(tar -tf "$work/documents.tar" | grep -c '\.bin$' || true)"
  say "archives read: database (schema $revision), $originals encrypted original(s)"
fi

safety_dir="${LIFEOS_SAFETY_DIR:-/var/backups/lifeos}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
current=0  # whether this server already holds a LifeOS database (not a blank machine)
[[ "$(psql_su -d lifeos -tAc "SELECT to_regclass('public.audit_events') IS NOT NULL" \
  2>/dev/null)" == "t" ]] && current=1
if [[ "$safety_dump" -eq 1 && "$current" -eq 1 ]]; then
  safety="$safety_dir/pre-restore-$stamp.pgdump"
  (umask 077 && mkdir -p "$safety_dir" \
    && docker compose exec -T postgres pg_dump -U postgres -d lifeos --format=custom > "$safety")
  say "current database saved: $safety (delete it once the restore is validated)"
fi

# The erasures made since the backup, from the audit trail of the database about to be
# replaced: identifiers only (no content), replayed once the backup is restored. From one hour
# before the backup run: a deletion audited just before the dump started may have committed
# after it; replaying an erasure the backup already holds finds nothing to erase.
erasures=""
if [[ "$replay" -eq 1 && "$current" -eq 1 ]]; then
  erasures="$safety_dir/erasures-since-${run:-backup}-$stamp.csv"
  (umask 077 && mkdir -p "$safety_dir" && psql_su -d lifeos -q -v db_time="$(field time)" \
    -v documents_time="$documents_time" > "$erasures") <<'SQL' \
    || die "could not list the erasures made since the backup (audit trail unreadable):" \
      "nothing replaced. To restore anyway, without replaying them: --no-replay."
COPY (
  SELECT occurred_at, user_id, action, target_type, target_id FROM audit_events
  WHERE outcome = 'success' AND target_id IS NOT NULL
    AND (action LIKE '%.delete' OR action = 'fact.retract')
    AND occurred_at >= LEAST(:'db_time'::timestamptz,
      COALESCE(NULLIF(:'documents_time', '')::timestamptz, :'db_time'::timestamptz))
      - interval '1 hour'
  ORDER BY id
) TO STDOUT WITH (FORMAT csv, HEADER)
SQL
  say "erasures made since the backup: $(($(wc -l < "$erasures") - 1)) listed in $erasures"
fi

mapfile -t running < <(docker compose ps --services --status running | grep -vx 'postgres\|redis' || true)
stopped=1
docker compose stop api worker scheduler
psql_su -d postgres -qc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'lifeos' AND pid <> pg_backend_pid()"
psql_su -d postgres -qc "DROP DATABASE lifeos"
psql_su -d postgres -qc "CREATE DATABASE lifeos OWNER lifeos"
# Restored as superuser: original ownership (role `lifeos`, extensions) is preserved.
docker compose exec -T postgres pg_restore -U postgres -d lifeos --exit-on-error < "$work/db.pgdump"
say "database restored (run ${run:-legacy})"
if [[ "$with_documents" -eq 1 ]]; then
  # Replaces the files of the documents volume (as the service user of the worker image).
  docker compose run --rm --no-deps -T --entrypoint sh worker \
    -c 'find /data/documents -mindepth 1 -delete && tar -C /data/documents -xf -' \
    < "$work/documents.tar"
  say "document originals restored"
fi
# A backup made by an older version: this code's migrations bring it up to date.
docker compose run --rm --no-deps migrate

# What was erased since the backup is erased again (rows, cascades and vectors, the copies that
# quote them, originals), then the retention purges whose deadline has passed meanwhile.
replayed=1
if [[ -n "$erasures" ]]; then
  docker compose run --rm --no-deps -T api lifeos-admin replay-erasures - --yes < "$erasures" \
    || replayed=0
fi

# Check: schema at head, counts, search, originals present and decryptable with this key.
check=(lifeos-admin verify-data --require-head)
[[ -n "$documents_time" && "$with_documents" -eq 1 ]] && check+=(--files-as-of "$documents_time")
status=0
docker compose run --rm --no-deps -T api "${check[@]}" || status=$?
cat <<EOF

Next steps (docs/restauration.md):
- Sign in: passkeys only work on the same domain ($(sed -n 's/^LIFEOS_WEBAUTHN_RP_ID=//p' .env));
  otherwise: docker compose exec api lifeos-admin enrollment-token --email <owner e-mail>
$(if [[ "$replay" -eq 0 ]]; then
  echo "- Erasures made after $(field time) NOT replayed (--no-replay): delete again what must"
  echo "  stay erased (docs/restauration.md §3, étape 10)."
elif [[ -z "$erasures" ]]; then
  echo "- No database was replaced, so no erasure list: if the old server's database still"
  echo "  exists, replay its erasures from it (docs/restauration.md §3, étape 10)."
else
  echo "- Delete $erasures with the safety dump once the restore is validated."
fi)
- New machine: download the models (docker compose --profile setup run --rm ml-download,
  then stt-download).
EOF
if [[ "$replayed" -eq 0 ]]; then
  echo "Restore done, but the erasures made since the backup were NOT replayed: data erased" \
    "since may be back. Run again: docker compose run --rm --no-deps -T api lifeos-admin" \
    "replay-erasures - --yes < $erasures" >&2
  status=1
fi
case "$status" in
  0) say "restore complete and checked" ;;
  2) echo "Restore complete, with warnings (above): missing originals may be in an older" \
       "backup (docs/restauration.md)." >&2 ;;
  *) echo "Restore done, but the check FAILED (status $status): see the report above." >&2
     status=1 ;;
esac
[[ "$status" -eq 0 ]] || exit "$status"
