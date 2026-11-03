#!/usr/bin/env bash
# Updates LifeOS to another version: a backup first, the new images built while the current
# version still runs, the migrations, a health check, and the way back (docs/exploitation.md §6).
#
#   infra/scripts/update.sh [--to REF] [--yes]    update (default: the latest vX.Y.Z tag)
#   infra/scripts/update.sh --rollback [--restore-db] [--yes]
#                                                 back to the version before the last update
#   infra/scripts/update.sh --status              deployed versions and kept images
#
# Options:
#   --to REF        tag (vX.Y.Z), branch (origin/main) or commit to deploy
#   --no-fetch      do not fetch the remote's tags and branches first
#   --no-backup     no pre-update backup (a rollback can then only downgrade the schema)
#   --no-rollback   if the health check fails, stop there and print how to roll back
#   --restore-db    with --rollback: restore the pre-update backup (database and originals)
#                   instead of downgrading the schema; what was written since is lost
#   --keep N        versions whose images stay on the machine (default 3)
#   --yes           no question
#
# Run it as the owner of the checkout, not as root: the backup and the restore ask for sudo
# when /etc/lifeos/backup.env is root's. Each update is recorded in infra/state/deployments.tsv
# and the images of each version keep their own tag (the commit), so a rollback rebuilds
# nothing. The schema goes back by the migrations' downgrade, run with the newer images (the
# only ones that know the newer revisions); for a migration marked `Downgrade: lossy`, or when
# the downgrade fails, by restoring the pre-update backup (restore.sh).
# Exit status: 0 updated (or rolled back) and healthy; 1 failed (after an automatic rollback
# when it was possible: the message says which version runs).
set -euo pipefail
# The script runs from a copy: the checkout below replaces this very file.
if [[ -z "${LIFEOS_UPDATE_COPY:-}" ]]; then
  infra_dir="$(cd "$(dirname "$0")/.." && pwd)"
  copy="$(mktemp "${TMPDIR:-/tmp}/lifeos-update.XXXXXX")"
  cp "$0" "$copy"
  LIFEOS_UPDATE_COPY="$copy" LIFEOS_UPDATE_INFRA="$infra_dir" exec bash "$copy" "$@"
fi
cd "$LIFEOS_UPDATE_INFRA"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
. scripts/lib/common.sh

mode=update ref="" fetch=1 backup=1 auto_rollback=1 restore_db=0 keep=3 assume_yes=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --to) ref="${2:?--to needs a value}"; shift 2 ;;
    --rollback) mode=rollback; shift ;;
    --status) mode=status; shift ;;
    --no-fetch) fetch=0; shift ;;
    --no-backup) backup=0; shift ;;
    --no-rollback) auto_rollback=0; shift ;;
    --restore-db) restore_db=1; shift ;;
    --keep) keep="${2:?--keep needs a value}"; shift 2 ;;
    --yes) assume_yes=1; shift ;;
    -h | --help) sed -n '2,28p' "$LIFEOS_UPDATE_COPY"; rm -f "$LIFEOS_UPDATE_COPY"; exit 0 ;;
    *) rm -f "$LIFEOS_UPDATE_COPY"; die "unknown option $1 (see --help)" ;;
  esac
done
[[ "$keep" =~ ^[1-9][0-9]?$ ]] || die "--keep: a number of versions (1 to 99)."
[[ "$restore_db" -eq 0 || "$mode" == rollback ]] || die "--restore-db goes with --rollback."

state=state/deployments.tsv
wait_timeout="${LIFEOS_UPDATE_WAIT_SECONDS:-300}"
cleanup() { rm -f "$LIFEOS_UPDATE_COPY"; }
trap cleanup EXIT

git_() { git -C .. "$@"; }
short() { git_ rev-parse --short=12 "$1"; }
describe() { git_ describe --tags --always "$1" 2>/dev/null || short "$1"; }
# The schema revision of the database (empty when PostgreSQL does not answer).
schema() {
  psql_su -d lifeos -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '[:space:]'
}
# Services built from this checkout, with the repository of their image (tab separated).
built_services() {
  docker compose config --format json | jq -r '.services | to_entries[]
    | select(.value.build != null) | [.key, (.value.image | sub(":[^:/]*$"; ""))] | @tsv'
}
# Services meant to keep running (one-shot services such as migrate excluded).
lasting_services() {
  docker compose config --format json \
    | jq -r '.services | to_entries[] | select((.value.restart // "no") != "no") | .key'
}

# record ACTION FROM_COMMIT FROM_TAG FROM_SCHEMA TO_COMMIT TO_TAG TO_SCHEMA BACKUP_RUN RESULT
record() {
  mkdir -p "$(dirname "$state")"
  if [[ ! -s "$state" ]]; then
    printf '# date\taction\tfrom_commit\tfrom_tag\tfrom_schema\tto_commit\tto_tag\tto_schema\tbackup_run\tresult\n' \
      > "$state"
  fi
  local field line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for field in "$@"; do line+=$'\t'"${field:--}"; done
  printf '%s\n' "$line" >> "$state"
}

# backup_script SCRIPT ARGS...: backup.sh or restore.sh, through sudo when their settings are
# root's (/etc/lifeos/backup.env, mode 0600). LIFEOS_BACKUP_RUN and _TAG are passed along.
backup_script() {
  local script="scripts/$1"
  shift
  local settings=(env "LIFEOS_BACKUP_RUN=${LIFEOS_BACKUP_RUN:-}" "LIFEOS_BACKUP_TAG=${LIFEOS_BACKUP_TAG:-}")
  if [[ -n "${RESTIC_REPOSITORY:-}" || "$EUID" -eq 0 \
    || -r "${LIFEOS_BACKUP_ENV:-/etc/lifeos/backup.env}" ]]; then
    "${settings[@]}" "$script" "$@"
  else
    echo "(${script##*/} reads the backup settings of root: sudo)"
    sudo "${settings[@]}" "$script" "$@"
  fi
}
backup_configured() { [[ -n "${RESTIC_REPOSITORY:-}" || -e "${LIFEOS_BACKUP_ENV:-/etc/lifeos/backup.env}" ]]; }

confirm() {
  [[ "$assume_yes" -eq 1 ]] && return 0
  [[ -t 0 ]] || die "no terminal to confirm: add --yes."
  local answer
  read -r -p "$1 [y/N] " answer
  [[ "$answer" == [yYoO]* ]] || die "cancelled: nothing changed."
}

# keep_images TAG CURRENT: the images the LifeOS containers run now also get TAG, so that a
# later build of CURRENT (e.g. `local`, install.sh) cannot take the rollback version away.
keep_images() {
  local tag="$1" current="$2" service repo id image
  while IFS=$'\t' read -r service repo; do
    id="$(docker compose ps -aq "$service" | head -1)"
    image="$repo:$current"
    [[ -z "$id" ]] || image="$(docker inspect --format '{{.Image}}' "$id")"
    if docker image inspect "$image" >/dev/null 2>&1; then
      docker tag "$image" "$repo:$tag"
    else
      echo "warning: no image for $service ($repo:$current)" >&2
    fi
  done < <(built_services)
}

# images_present TAG: every LifeOS image of this configuration exists with TAG.
images_present() {
  local service repo
  while IFS=$'\t' read -r service repo; do
    docker image inspect "$repo:$1" >/dev/null 2>&1 || return 1
  done < <(built_services)
}

# Migrations added between two commits whose downgrade loses data (marker in the file).
lossy_migrations() {
  local file
  git_ diff --name-only --diff-filter=A "$1" "$2" -- backend/migrations/versions/ \
    | while read -r file; do
      if git_ show "$2:$file" | grep -q 'Downgrade: lossy'; then echo "${file##*/}"; fi
    done
}

start_services() {
  local waited
  docker compose up -d || return 1
  mapfile -t waited < <(wait_services)
  [[ "${#waited[@]}" -eq 0 ]] \
    || docker compose up -d --wait --wait-timeout "$wait_timeout" "${waited[@]}"
}

# health_check TAG: migrations applied up to this code's head, every service up with the
# images of TAG, API ready, routing through Traefik, local OCR. Prints each problem.
health_check() {
  local tag="$1" problems=0 service repo id image exit_code domain
  problem() { echo "health check: $*" >&2; problems=$((problems + 1)); }
  step "Health check"
  id="$(docker compose ps -aq migrate | head -1)"
  exit_code="$( [[ -n "$id" ]] && docker inspect --format '{{.State.ExitCode}}' "$id" || echo none)"
  [[ "$exit_code" == 0 ]] || problem "migrate ended with status $exit_code (docker compose logs migrate)"
  docker compose run --rm --no-deps -T migrate alembic current 2>/dev/null | grep -q '(head)' \
    || problem "the database schema is not at the head of this version's migrations"
  while read -r service; do
    [[ -n "$(docker compose ps -q --status running "$service")" ]] \
      || problem "$service is not running (docker compose logs $service)"
  done < <(lasting_services)
  while IFS=$'\t' read -r service repo; do
    id="$(docker compose ps -q "$service" | head -1)"
    [[ -n "$id" ]] || continue
    image="$(docker inspect --format '{{.Config.Image}}' "$id")"
    [[ "$image" == "$repo:$tag" ]] || problem "$service runs $image instead of $repo:$tag"
  done < <(built_services)
  local ready=0
  for _ in $(seq 1 10); do
    if docker compose exec -T api python -c "import urllib.request
urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 3
  done
  [[ "$ready" -eq 1 ]] || problem "the API is not ready (/readyz: database or Redis)"
  # Traefik's routing, as the reverse proxy or the browser reach it (both modes).
  if docker compose config --services | grep -qx traefik; then
    domain="$(env_value LIFEOS_DOMAIN)"
    if grep -qs '^COMPOSE_FILE=.*compose\.proxy\.yaml' .env; then
      url=http://traefik:80/healthz
    else
      url=https://traefik:443/healthz
    fi
    docker compose exec -T api python -c "import ssl, sys, urllib.request
request = urllib.request.Request(sys.argv[1], headers={'Host': sys.argv[2]})
urllib.request.urlopen(request, timeout=10, context=ssl._create_unverified_context())" \
      "$url" "$domain" >/dev/null 2>&1 || problem "Traefik does not route https://$domain/healthz"
  fi
  if [[ -n "$(docker compose ps -q --status running ml 2>/dev/null)" ]]; then
    docker compose exec -T ml lifeos-ml selftest >/dev/null 2>&1 \
      || problem "the local OCR check failed (docker compose exec ml lifeos-ml selftest)"
  fi
  [[ "$problems" -eq 0 ]] && say "healthy: $(docker compose ps --status running --services | wc -l) services up, schema $(schema)"
  return "$problems"
}

# rollback_to COMMIT TAG SCHEMA NEWER_COMMIT BACKUP_RUN RESTORE: back to the version COMMIT
# (images TAG, schema SCHEMA) from NEWER_COMMIT, whose images and .env tag are in place.
rollback_to() {
  local commit="$1" tag="$2" target="$3" newer="$4" run="$5" restore="$6" method=none current
  local lossy
  current="$(schema)"
  if [[ "$restore" -eq 1 ]]; then
    method=restore
  elif [[ -n "$current" && "$current" != "$target" ]]; then
    method=downgrade
    lossy="$(lossy_migrations "$commit" "$newer" | paste -sd' ')"
    if [[ -n "$lossy" ]]; then
      echo "Migration(s) whose downgrade loses data: $lossy"
      method=restore
    fi
  fi
  if [[ "$method" == restore && ( -z "$run" || "$run" == - ) ]]; then
    echo "No pre-update backup to restore (--no-backup)." >&2
    [[ "$restore" -eq 0 ]] || return 1
    method=downgrade
  fi
  if [[ "$method" == downgrade ]]; then
    step "Schema back from $current to $target (downgrade, with the newer images)"
    docker compose stop api worker scheduler
    if ! docker compose run --rm --no-deps -T migrate alembic downgrade "$target"; then
      if [[ -z "$run" || "$run" == - ]]; then
        echo "The downgrade failed and there is no pre-update backup: restore one with" \
          "infra/scripts/restore.sh (docs/restauration.md)." >&2
        return 1
      fi
      echo "The downgrade failed: the pre-update backup is restored instead." >&2
      method=restore
    fi
  fi
  step "Code and images of $(describe "$commit") ($tag)"
  git_ checkout --quiet --detach "$commit"
  set_env LIFEOS_IMAGE_TAG "$tag"
  ./scripts/init-secrets.sh >/dev/null
  if ! images_present "$tag"; then
    echo "The images $tag are no longer on this machine: rebuilt."
    docker compose build
  fi
  if [[ "$method" == restore ]]; then
    step "Pre-update backup restored (run $run)"
    local status=0
    backup_script restore.sh "$run" --yes || status=$?
    # 2: restored, with warnings (missing originals, no passkey): reported above.
    [[ "$status" -eq 0 || "$status" -eq 2 ]] || return 1
  fi
  start_services || true
  health_check "$tag"
}

# Removes the images of the versions older than the last $keep ones (never `local`, never
# the deployed one, never a tag this script did not record).
prune_images() {
  local tags=() kept=() tag service repo current
  current="$(env_value LIFEOS_IMAGE_TAG)"
  mapfile -t tags < <(awk -F'\t' '!/^#/ { print $4; print $7 }' "$state" | sed '/^-$/d' \
    | tac | awk '!seen[$0]++')
  kept=("${tags[@]:0:$keep}")
  for tag in "${tags[@]:$keep}"; do
    [[ "$tag" == local || "$tag" == "$current" ]] && continue
    while IFS=$'\t' read -r service repo; do
      docker image rm "$repo:$tag" >/dev/null 2>&1 && echo "removed: $repo:$tag"
    done < <(built_services | awk -F'\t' '!seen[$2]++')
  done
  echo "images kept: ${kept[*]}"
}

preflight() {
  require_tools git docker jq flock
  [[ -f .env ]] || die "infra/.env is missing: install LifeOS first (install.sh)."
  if [[ "$EUID" -eq 0 && "$(stat -c %u ..)" -ne 0 ]]; then
    die "run it as $(stat -c %U ..), the owner of the checkout, not as root (it asks for sudo" \
      "for the backup)."
  fi
  git_ rev-parse --git-dir >/dev/null 2>&1 || die "$(cd .. && pwd) is not a git checkout."
  [[ -z "$(git_ status --porcelain --untracked-files=no)" ]] \
    || die "local changes to tracked files (git status): commit or set them aside first;" \
      "settings belong in infra/.env."
  local lock_dir=/run/lock
  [[ -d "$lock_dir" && -w "$lock_dir" ]] || lock_dir="${TMPDIR:-/tmp}"
  # Opened read-only (flock needs no more), whoever created it.
  [[ -e "$lock_dir/lifeos-update.lock" ]] || (umask 022 && : >"$lock_dir/lifeos-update.lock")
  exec 8<"$lock_dir/lifeos-update.lock"
  flock -n 8 || die "another update or rollback is running."
  [[ -n "$(schema)" ]] || die "the database does not answer: start LifeOS first (docker compose up -d)."
}

disk_check() {
  local need_gb="${LIFEOS_UPDATE_MIN_FREE_GB:-5}" root free_kb
  root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
  free_kb="$(df -Pk "$root" 2>/dev/null | awk 'NR == 2 { print $4 }')"
  if [[ -n "$free_kb" && "$free_kb" -lt $((need_gb * 1048576)) ]]; then
    die "less than $need_gb GB free for Docker ($root): docker image prune, or" \
      "update.sh --keep 2 after a successful update (docs/exploitation.md §6)."
  fi
}

table() { if command -v column >/dev/null; then column -t -s $'\t'; else cat; fi; }

do_status() {
  local tag repos
  tag="$(env_value LIFEOS_IMAGE_TAG)"
  echo "Deployed: $(describe HEAD) (images ${tag:-local}, schema $(schema))"
  if [[ -s "$state" ]]; then
    echo
    table < "$state"
  fi
  echo
  echo "LifeOS images on this machine:"
  repos="$(built_services | cut -f2 | sort -u | paste -sd'|')"
  docker image ls --format '{{.Repository}}:{{.Tag}}\t{{.CreatedSince}}\t{{.Size}}' \
    | { grep -E "^($repos):" || true; } | table
}

do_update() {
  preflight
  disk_check
  if [[ "$fetch" -eq 1 && -n "$(git_ remote)" ]]; then
    git_ fetch --quiet --tags || die "git fetch failed (network?): --no-fetch to use what is here."
  fi
  if [[ -z "$ref" ]]; then
    ref="$(git_ tag -l 'v[0-9]*' --sort=-v:refname | head -1)"
    [[ -n "$ref" ]] || die "no release tag (vX.Y.Z) here: --to <branch or commit>."
  fi
  local from_commit from_tag from_schema to_commit to_tag keep_tag run=- removed new
  to_commit="$(git_ rev-parse --verify --quiet "$ref^{commit}")" || die "unknown version: $ref"
  from_commit="$(git_ rev-parse HEAD)"
  from_tag="$(env_value LIFEOS_IMAGE_TAG)"
  from_tag="${from_tag:-local}"
  from_schema="$(schema)"
  to_tag="$(short "$to_commit")"
  if [[ "$to_commit" == "$from_commit" && "$to_tag" == "$from_tag" ]]; then
    echo "LifeOS already runs $(describe "$to_commit")."
    return 0
  fi
  removed="$(git_ diff --name-only --diff-filter=D "$from_commit" "$to_commit" \
    -- backend/migrations/versions/ | sed 's|.*/||' | paste -sd' ')"
  [[ -z "$removed" ]] || die "$ref is older than what runs (it lacks the migrations $removed):" \
    "use update.sh --rollback, or restore.sh with a backup of that version."
  new="$(git_ diff --name-only --diff-filter=A "$from_commit" "$to_commit" \
    -- backend/migrations/versions/ | sed 's|.*/||' | paste -sd' ')"
  if [[ "$backup" -eq 1 ]] && ! backup_configured; then
    die "backups are not set up (install-backups.sh, docs/exploitation.md §4): set them up," \
      "or --no-backup (a rollback could then only downgrade the schema)."
  fi
  cat <<EOF
Update of LifeOS
  from  $(describe "$from_commit") (images $from_tag, schema $from_schema)
  to    $(describe "$to_commit") (images $to_tag)
  migrations  ${new:-none}
  backup      $([[ "$backup" -eq 1 ]] && echo "before the update (restic, tag pre-update)" || echo "NONE (--no-backup)")
EOF
  confirm "Update now? LifeOS is unavailable for a few minutes."

  step "Images of the current version kept"
  keep_tag="$from_tag"
  [[ "$from_tag" != local ]] || keep_tag="$(short "$from_commit")"
  keep_images "$keep_tag" "$from_tag"
  echo "tag: $keep_tag"

  if [[ "$backup" -eq 1 ]]; then
    step "Backup before the update"
    run="$(date -u +%Y%m%dT%H%M%SZ)-pre-update"
    LIFEOS_BACKUP_RUN="$run" LIFEOS_BACKUP_TAG=pre-update backup_script backup.sh \
      || die "the backup failed: nothing changed, LifeOS still runs $(describe "$from_commit")."
  fi

  step "Code of $(describe "$to_commit"), images $to_tag"
  git_ checkout --quiet --detach "$to_commit"
  ./scripts/init-secrets.sh >/dev/null
  if ! LIFEOS_IMAGE_TAG="$to_tag" docker compose build; then
    git_ checkout --quiet --detach "$from_commit"
    record update "$from_commit" "$keep_tag" "$from_schema" "$to_commit" "$to_tag" - "$run" \
      build-failed
    die "the build failed: nothing changed, LifeOS still runs $(describe "$from_commit")."
  fi

  step "Start (migrations first)"
  set_env LIFEOS_IMAGE_TAG "$to_tag"
  local healthy=0
  if start_services && health_check "$to_tag"; then healthy=1; fi
  if [[ "$healthy" -eq 1 ]]; then
    record update "$from_commit" "$keep_tag" "$from_schema" "$to_commit" "$to_tag" "$(schema)" \
      "$run" ok
    step "Images of older versions"
    prune_images
    step "Done"
    echo "LifeOS runs $(describe "$to_commit") (schema $(schema))."
    echo "Way back, if something is wrong: infra/scripts/update.sh --rollback"
    return 0
  fi
  record update "$from_commit" "$keep_tag" "$from_schema" "$to_commit" "$to_tag" "$(schema)" \
    "$run" failed
  echo "Last logs (JSON, without personal data):" >&2
  docker compose logs --no-color --tail 20 migrate api >&2 2>/dev/null || true
  if [[ "$auto_rollback" -eq 0 ]]; then
    cat >&2 <<EOF
The update failed its health check (above); LifeOS runs $(describe "$to_commit"), maybe badly.
Logs: docker compose logs --tail 100 migrate api worker
Way back: infra/scripts/update.sh --rollback   (or --rollback --restore-db: pre-update backup)
EOF
    return 1
  fi
  step "Automatic rollback to $(describe "$from_commit")"
  if rollback_to "$from_commit" "$keep_tag" "$from_schema" "$to_commit" "$run" 0; then
    record rollback "$to_commit" "$to_tag" - "$from_commit" "$keep_tag" "$(schema)" "$run" ok
    echo "The update to $(describe "$to_commit") FAILED (health check above): LifeOS runs" \
      "$(describe "$from_commit") again." >&2
  else
    record rollback "$to_commit" "$to_tag" - "$from_commit" "$keep_tag" "$(schema)" "$run" failed
    echo "The update FAILED, and so did the rollback: see above, then restore the pre-update" \
      "backup (infra/scripts/restore.sh ${run/#-/latest} --yes, docs/restauration.md)." >&2
  fi
  return 1
}

do_rollback() {
  preflight
  local last from_commit from_tag from_schema to_commit to_tag run current
  last="$(awk -F'\t' '$2 == "update" && ($10 == "ok" || $10 == "failed")' "$state" 2>/dev/null \
    | tail -1)"
  [[ -n "$last" ]] || die "no update recorded in infra/$state: nothing to roll back."
  IFS=$'\t' read -r _ _ from_commit from_tag from_schema to_commit to_tag _ run _ <<<"$last"
  current="$(env_value LIFEOS_IMAGE_TAG)"
  if [[ "$current" == "$from_tag" && "$(git_ rev-parse HEAD)" == "$from_commit" ]]; then
    echo "LifeOS already runs $(describe "$from_commit"), the version before the last update."
    return 0
  fi
  [[ "$current" == "$to_tag" ]] || echo "warning: the images in use ($current) are not those" \
    "of the last update ($to_tag)." >&2
  cat <<EOF
Rollback of LifeOS
  from  $(describe HEAD) (images ${current:-local}, schema $(schema))
  to    $(describe "$from_commit") (images $from_tag, schema $from_schema)
  data  $([[ "$restore_db" -eq 1 ]] && echo "pre-update backup $run restored: what was written since is LOST" \
    || echo "kept (schema downgrade; a lossy migration restores the pre-update backup)")
EOF
  confirm "Roll back now? LifeOS is unavailable for a few minutes."
  if rollback_to "$from_commit" "$from_tag" "$from_schema" "$to_commit" "$run" "$restore_db"; then
    record rollback "$to_commit" "$to_tag" - "$from_commit" "$from_tag" "$(schema)" "$run" ok
    step "Done"
    echo "LifeOS runs $(describe "$from_commit") again (schema $(schema))."
    return 0
  fi
  record rollback "$to_commit" "$to_tag" - "$from_commit" "$from_tag" "$(schema)" "$run" failed
  echo "The rollback FAILED: see above (docs/exploitation.md §6, docs/restauration.md)." >&2
  return 1
}

case "$mode" in
  status) do_status ;;
  rollback) do_rollback ;;
  update) do_update ;;
esac
