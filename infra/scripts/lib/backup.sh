# shellcheck shell=bash
# Shared by backup.sh, restore.sh, restore-check.sh, restore-drill.sh, backup-verify.sh and
# export-keys.sh (sourced from infra/, never run). Requires restic >= 0.17 and jq.
#
# A backup run makes up to three restic snapshots, all with the host `lifeos`, the tag
# `lifeos` and the same `run:<UTC time>` tag:
#   documents  lifeos-documents.tar   the encrypted originals (taken first)
#   db         lifeos-db.pgdump       pg_dump --format=custom of the database
#   config     lifeos-config.tar      infra/.env (no secret by design), traefik/dynamic/tls.yml
# The db snapshot also carries `version:<git describe>`, `rev:<alembic revision>` and
# `dockey:<fingerprint of lifeos_documents_key>`: a restore can check that it has the right
# code and the right key without the key itself ever entering the repository.

# Reads /etc/lifeos/backup.env (install-backups.sh) unless the restic settings are already in
# the environment (systemd passes them to the units; by hand, they come from here), or
# whatever the environment holds with `load_backup_env always`.
load_backup_env() {
  local file="${LIFEOS_BACKUP_ENV:-/etc/lifeos/backup.env}"
  if [[ ( -z "${RESTIC_REPOSITORY:-}" || "${1:-}" == always ) && -r "$file" ]]; then
    set -a
    # shellcheck source=/dev/null
    . "$file"
    set +a
  fi
}

die() { echo "Error: $*" >&2; exit 1; }
say() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }

require_tools() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null || die "$tool is required (docs/exploitation.md §4)."
  done
  if [[ " $* " == *" restic "* ]]; then
    local version
    version="$(restic version | sed -n 's/^restic \([0-9]*\.[0-9]*\).*/\1/p')"
    if [[ -z "$version" ]] || ! printf '%s\n%s\n' 0.17 "$version" | sort -V -C; then
      die "restic >= 0.17 is required (found ${version:-none}): install the official binary."
    fi
  fi
}

require_restic_env() {
  : "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required (or /etc/lifeos/backup.env)}"
  : "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required (or /etc/lifeos/backup.env)}"
}

# One backup, restore or restore check at a time on this host (they share the repository
# and the database server).
take_lock() {
  local dir=/run/lock
  [[ -d "$dir" && -w "$dir" ]] || dir="${TMPDIR:-/tmp}"
  exec 9>"$dir/lifeos-backup.lock"
  flock -w "${LIFEOS_LOCK_WAIT_SECONDS:-7200}" 9 \
    || die "another LifeOS backup or restore is still running."
}

# The off-site repository replaces the main one (restore tests, restores from the copy).
use_copy_repository() {
  : "${RESTIC_COPY_REPOSITORY:?RESTIC_COPY_REPOSITORY is not set: no off-site copy configured}"
  export RESTIC_REPOSITORY="$RESTIC_COPY_REPOSITORY"
  export RESTIC_PASSWORD_FILE="${RESTIC_COPY_PASSWORD_FILE:-$RESTIC_PASSWORD_FILE}"
}

# Whether the main repository already is off this machine (the off-site freshness metric is
# then the backup itself). LIFEOS_BACKUP_PRIMARY_OFFSITE=yes|no overrides the guess: a local
# path is taken as on this machine, anything else (sftp:, s3:, b2:, rest:...) as off-site.
primary_is_offsite() {
  case "${LIFEOS_BACKUP_PRIMARY_OFFSITE:-auto}" in
    yes | true | 1) return 0 ;;
    no | false | 0) return 1 ;;
  esac
  [[ "$RESTIC_REPOSITORY" != /* && "$RESTIC_REPOSITORY" != local:* ]]
}

# First 12 hex digits of the SHA-256 of the documents key (surrounding whitespace ignored, as
# the application does): identifies the key without revealing it.
documents_key_fingerprint() {
  [[ -s "$1" ]] || return 1
  tr -d '[:space:]' < "$1" | sha256sum | cut -c1-12
}

psql_su() { docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 "$@"; }

compose_json() { docker compose config --format json; }

# All LifeOS snapshots of the current repository (JSON array).
lifeos_snapshots() { restic snapshots --host lifeos --json; }

# jq helpers: the value of a `prefix:` tag, the snapshots of one kind, and the documents
# snapshot that goes with a database snapshot (same run tag; for snapshots made before run
# tags existed, the first documents snapshot taken after it and before the next database one).
_snapshot_jq_defs() {
  cat <<'EOF'
def tag($p): [(.tags // [])[] | select(startswith($p))] | first // null
  | if . == null then null else .[($p | length):] end;
def kind($k): map(select((.tags // []) | index($k)));
def partner($db; $dbs; $others):
  ($db | tag("run:")) as $run
  | if $run != null then [$others[] | select(tag("run:") == $run)] | first
    else ([$dbs[] | select(.time > $db.time)] | first | .time) as $next
      | [$others[] | select(tag("run:") == null and .time > $db.time
          and ($next == null or .time < $next))] | first
    end;
EOF
}

# pick_pair SELECTOR: the database snapshot (latest, a run id, or a snapshot id) and the
# snapshots of the same run, as one JSON object; fails when nothing matches.
pick_pair() {
  local selector="${1#run:}"
  lifeos_snapshots | jq -ce --arg sel "$selector" "$(_snapshot_jq_defs)"'
    sort_by(.time) as $all
    | ($all | kind("db")) as $dbs
    | (if $sel == "latest" then $dbs[-1]
       else [$dbs[] | select(tag("run:") == $sel or .short_id == $sel
                             or (.id | startswith($sel)))] | last end) as $db
    | if $db == null then null else
        partner($db; $dbs; $all | kind("documents")) as $documents
        | partner($db; $dbs; $all | kind("config")) as $config
        | {db: $db.id, db_short: $db.short_id, time: $db.time,
           run: ($db | tag("run:")), legacy: (($db | tag("run:")) == null),
           version: ($db | tag("version:")), rev: ($db | tag("rev:")),
           dockey: ($db | tag("dockey:")),
           documents: ($documents.id // null), documents_short: ($documents.short_id // null),
           documents_time: ($documents.time // null),
           config: (if ($db | tag("run:")) == null then null else ($config.id // null) end)}
      end'
}

# Retention of the current repository (docs/politique-de-donnees.md): 14 daily and 4 weekly
# database snapshots; a documents or config snapshot is kept exactly as long as its database
# snapshot, so pairs never break and nothing outlives the policy (erasure guarantee).
apply_retention() {
  local ids
  restic forget --retry-lock 30m --host lifeos --tag db --keep-daily 14 --keep-weekly 4 -q
  mapfile -t ids < <(lifeos_snapshots | jq -r "$(_snapshot_jq_defs)"'
    sort_by(.time) as $all
    | ($all | kind("db")) as $dbs
    | [$dbs[] | partner(.; $dbs; $all | kind("documents")), partner(.; $dbs; $all | kind("config"))
       | select(. != null) | .id] as $kept
    | $all[] | select(((.tags // []) | index("documents") or index("config")))
    | select(.id as $id | $kept | index($id) | not) | .id')
  if [[ "${#ids[@]}" -gt 0 ]]; then
    say "retention: ${#ids[@]} snapshot(s) whose database snapshot is gone"
    restic forget --retry-lock 30m -q "${ids[@]}"
  fi
  restic prune --retry-lock 30m -q
}

# metric_set FILE NAME VALUE HELP: one gauge of a Prometheus textfile (node exporter textfile
# collector), the other series kept; written atomically. Never fails the caller.
metric_set() {
  local file="$1" name="$2" value="$3" help="$4" tmp
  {
    mkdir -p "$(dirname "$file")" && tmp="$(mktemp "$file.XXXXXX")" || return 0
    if [[ -f "$file" ]]; then
      awk -v n="$name" '$1 != n && !($1 == "#" && $3 == n)' "$file" > "$tmp"
    fi
    printf '# HELP %s %s\n# TYPE %s gauge\n%s %s\n' "$name" "$help" "$name" "$name" "$value" >> "$tmp"
    chmod 644 "$tmp" && mv -f "$tmp" "$file"
  } 2>/dev/null || echo "warning: could not write the metric $name to $file" >&2
}

metrics_dir() { echo "${LIFEOS_METRICS_DIR:-/var/lib/lifeos/metrics}"; }

# Dead man's switch (healthchecks.io style): `hc_ping URL /start`, `hc_ping URL` (success),
# `hc_ping URL /fail`. Only the event is sent, never a log. Never fails the caller.
hc_ping() {
  local url="${1:-}" suffix="${2:-}"
  [[ -n "$url" ]] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null "${url%/}$suffix" \
    || echo "warning: healthcheck ping failed" >&2
}
