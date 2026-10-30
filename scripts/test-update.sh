#!/usr/bin/env bash
# Update and rollback test with the real update.sh, backup.sh and restore.sh (CI infra job;
# locally: Docker, git, restic >= 0.17, jq). Everything runs in a throw-away git repository
# holding a copy of infra/ and four versions, with its own Compose project, image names and
# restic repository: a local LifeOS and its images are never touched. The images of each
# version are the given backend image plus that version's migrations (built offline); the
# services are those of a backup (no ml, stt, web nor Traefik).
#   1. v1.0.0 installed by hand (images `local`), with an owner;
#   2. update.sh (latest tag: v1.1.0, one more migration): pre-update backup, images of v1.0.0
#      kept under their commit, schema at head, health check, deployments.tsv, version label;
#   3. --rollback: schema downgraded with the newer images, then the images of v1.0.0 (not
#      rebuilt), data kept;
#   4. update again, data changed, then --rollback --restore-db: the pre-update backup is back;
#   5. a migration marked `Downgrade: lossy` (v1.3.0): --rollback restores the backup by itself;
#   6. v1.2.0, whose migration fails: automatic rollback, v1.0.0 healthy again (exit status 1);
#   7. images of older versions pruned (never `local`), --status.
# LIFEOS_TEST_BACKEND_IMAGE: backend image built from this code (default lifeos-backend:local);
# LIFEOS_TEST_VERBOSE=1 prints every log at the end.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
base_image="${LIFEOS_TEST_BACKEND_IMAGE:-lifeos-backend:local}"
prefix="lifeos-update-test-$$"
repo_name="$prefix-backend"
work="$(mktemp -d)"
repo="$work/repo"
infra="$repo/infra"

failures=0
check() {
  local description="$1"
  shift
  if "$@"; then
    echo "ok   - $description"
  else
    echo "FAIL - $description"
    failures=$((failures + 1))
  fi
}
has() { grep -q -- "$1" "$2"; }
equals() { [[ "$1" == "$2" ]]; }
fails() { ! "$@"; }
logged() {
  local name="$1"
  shift
  "$@" > "$work/$name.log" 2>&1
}

compose() { (cd "$infra" && docker compose "$@"); }
sql() { compose exec -T postgres psql -U postgres -d lifeos -tAc "$1" | tr -d '[:space:]'; }
schema() { sql 'SELECT version_num FROM alembic_version'; }
display_name() { sql "SELECT display_name FROM users WHERE email = 'owner@example.org'"; }
probe_table() { sql "SELECT to_regclass('public.update_probe') IS NOT NULL"; }
deployed_tag() { sed -n 's/^LIFEOS_IMAGE_TAG=//p' "$infra/.env"; }
image_id() {
  local id
  id="$(docker image inspect --format '{{.Id}}' "$repo_name:$1" 2>/dev/null)" || id=none
  echo "${id:-none}"
}
head_commit() { git -C "$repo" rev-parse HEAD; }
last_line() { tail -1 "$infra/state/deployments.tsv" | cut -f "$1"; }
api_healthy() {
  [[ "$(docker inspect --format '{{.State.Health.Status}}' "$(compose ps -q api)")" == healthy ]]
}
build_info() {
  compose exec -T api python -c "import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:8000/metrics/', timeout=5).read().decode())" \
    | grep '^lifeos_build_info'
}
update() { "$infra/scripts/update.sh" --no-fetch --yes "$@"; }

cleanup() {
  local status=$?
  exec 1>&3 2>&4
  if [[ "$status" -ne 0 && "$failures" -eq 0 && -d "$infra" ]]; then
    echo "Aborted (status $status). Containers and their last logs:" >&2
    compose ps -a >&2 || true
    compose logs --no-color --tail 40 >&2 || true
    for log in "$work"/*.log; do
      [[ -f "$log" ]] && { echo "== ${log##*/}"; cat "$log" || true; } >&2
    done
  fi
  [[ -d "$infra" ]] && compose down -v --remove-orphans >/dev/null 2>&1
  docker image ls --format '{{.Repository}}:{{.Tag}}' | grep "^$repo_name:" \
    | xargs -r docker image rm -f >/dev/null 2>&1
  rm -rf "$work"
}
exec 3>&1 4>&2
trap cleanup EXIT
trap 'echo "Unexpected failure, line $LINENO: $BASH_COMMAND" >&2' ERR

# --- A throw-away repository with four versions -------------------------------------------
mkdir -p "$repo/backend"
cp -r "$root/infra" "$repo/"
cp -r "$root/backend/migrations" "$root/backend/pyproject.toml" "$repo/backend/"
cp "$root/.gitignore" "$repo/"
find "$repo" -name __pycache__ -prune -exec rm -rf {} +
find "$infra/secrets" "$infra/traefik/certs" -type f ! -name README.md ! -name .gitkeep -delete \
  2>/dev/null || true
rm -rf "$infra/.env" "$infra/state" "$infra/traefik/dynamic/tls.yml"
cat > "$repo/backend/Dockerfile.test" <<'EOF'
# Test image: the backend image of this code, with the migrations of this version.
ARG BASE
FROM ${BASE} AS app
USER root
RUN rm -rf /app/migrations
COPY migrations /app/migrations
USER lifeos
EOF
cat > "$infra/compose.test.yaml" <<EOF
# Test overlay: test images built from the given backend image, only what a backup needs.
x-test-backend: &test-backend
  build: { context: ../backend, dockerfile: Dockerfile.test, args: { BASE: "$base_image" } }
  image: $repo_name:\${LIFEOS_IMAGE_TAG:-local}
services:
  migrate: *test-backend
  api: *test-backend
  worker: *test-backend
  scheduler: *test-backend
  ml: { profiles: [disabled] }
  stt: { profiles: [disabled] }
  web: { profiles: [disabled] }
  traefik: { profiles: [disabled] }
EOF
migration() {  # REVISION DOWN_REVISION SLUG BODY_UP BODY_DOWN [DOCSTRING]
  cat > "$repo/backend/migrations/versions/20990101_$1_$3.py" <<EOF
"""Test migration $1 (scripts/test-update.sh).${6:+

$6}"""

from alembic import op

revision = "$1"
down_revision = "$2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    $4


def downgrade() -> None:
    $5
EOF
}
git -C "$repo" init -q -b main
git -C "$repo" -c user.name=test -c user.email=test@example.org add -A
commit() { git -C "$repo" -c user.name=test -c user.email=test@example.org commit -q "$@"; }
commit -m "v1.0.0"
git -C "$repo" tag v1.0.0
v1="$(head_commit)"
# The head is the revision no other migration revises (ids are not in chain order).
head="$(comm -23 \
  <(sed -n 's/^revision[^=]*= *"\([^"]*\)".*/\1/p' "$repo"/backend/migrations/versions/*.py | sort) \
  <(sed -n 's/^down_revision[^=]*= *"\([^"]*\)".*/\1/p' "$repo"/backend/migrations/versions/*.py | sort))"
migration 9001 "$head" update_probe \
  'op.execute("CREATE TABLE update_probe (id integer PRIMARY KEY, note text)")' \
  'op.execute("DROP TABLE update_probe")'
git -C "$repo" add -A && commit -m "v1.1.0"
git -C "$repo" tag v1.1.0
v11="$(head_commit)"
migration 9002 9001 failing 'op.execute("SELECT 1 / 0")' 'pass'
git -C "$repo" add -A && commit -m "v1.2.0"
v12="$(head_commit)"
git -C "$repo" checkout -q --detach "$v11"
migration 9002 9001 lossy_column \
  'op.execute("ALTER TABLE users ADD COLUMN update_probe text")' \
  'op.execute("ALTER TABLE users DROP COLUMN update_probe")' \
  'Downgrade: lossy (the column update_probe and its values are dropped).'
git -C "$repo" add -A && commit -m "v1.3.0"
v13="$(head_commit)"
git -C "$repo" checkout -q --detach "$v1"
short() { git -C "$repo" rev-parse --short=12 "$1"; }

export COMPOSE_PROJECT_NAME="$prefix"
export RESTIC_REPOSITORY="$work/restic" RESTIC_PASSWORD_FILE="$work/restic-password"
export LIFEOS_BACKUP_ENV=/nonexistent LIFEOS_METRICS_DIR="$work/metrics"
export LIFEOS_SAFETY_DIR="$work/safety" LIFEOS_RESTORE_TMPDIR="$work" TMPDIR="$work"
export LIFEOS_UPDATE_MIN_FREE_GB=1
head -c 32 /dev/urandom | base64 > "$RESTIC_PASSWORD_FILE"
restic init -q

echo "# 1. v1.0.0 installed (images local)"
"$infra/scripts/init-secrets.sh" >/dev/null
{
  sed -e 's/lifeos\.example\.lan/lifeos.test/' "$infra/.env.example"
  echo "COMPOSE_FILE=compose.yaml:compose.test.yaml"
  echo "LIFEOS_EMBEDDINGS_PROVIDER=disabled"
  echo "LIFEOS_EXTRACTION_PROVIDER=disabled"
  echo "LIFEOS_STT_PROVIDER=disabled"
} > "$infra/.env"
logged build compose build
logged up compose up -d --wait --wait-timeout 180 api worker
compose exec -T api lifeos-admin create-owner --email owner@example.org --name Avant >/dev/null
v1_schema="$(schema)"
check "v1.0.0 runs, schema $v1_schema" equals "$(display_name)" Avant
local_id="$(image_id local)"

echo "# 2. Update to the latest tag (v1.1.0, one more migration)"
check "update.sh succeeds" logged update-1 update
check "... after a backup tagged pre-update" equals "$(restic snapshots --tag pre-update --json \
  | jq -r '[.[] | .tags[] | select(. == "db" or . == "documents" or . == "config")] | sort | join(",")')" \
  "config,db,documents"
check "... the code of v1.1.0 checked out" equals "$(head_commit)" "$v11"
check "... the images of v1.1.0 in use (tag = commit)" equals "$(deployed_tag)" "$(short "$v11")"
check "... the images of v1.0.0 kept under their commit" equals "$(image_id "$(short "$v1")")" "$local_id"
check "... schema at the new head" equals "$(schema)" 9001
check "... the API healthy, data there" equals "$(display_name)" Avant
check "... and it reports the deployed version" has "revision=\"$(short "$v11")\"" <(build_info)
check "... recorded in deployments.tsv" equals "$(last_line 2)/$(last_line 10)" update/ok
check "... with the pre-update backup run" has "pre-update" <(last_line 9)
check "an update to an older version is refused (use --rollback)" fails logged older \
  update --to v1.0.0 --no-backup
check "... before anything changes" equals "$(head_commit)/$(schema)" "$v11/9001"

echo "# 3. Rollback: downgrade with the newer images, then the previous ones"
check "update.sh --rollback succeeds" logged rollback-1 update --rollback
check "... schema downgraded" equals "$(schema)" "$v1_schema"
check "... the new table dropped" equals "$(probe_table)" f
check "... the code and images of v1.0.0, not rebuilt" equals \
  "$(head_commit) $(deployed_tag) $(image_id "$(short "$v1")")" "$v1 $(short "$v1") $local_id"
check "... data kept, the API healthy" equals "$(display_name)" Avant
check "... recorded" equals "$(last_line 2)/$(last_line 10)" rollback/ok
check "a second --rollback has nothing to do" logged rollback-noop update --rollback
check "... and says so" has "already runs" "$work/rollback-noop.log"

echo "# 4. Update again, data changed, then rollback with the pre-update backup"
check "update.sh --to v1.1.0 succeeds" logged update-2 update --to v1.1.0
sql "UPDATE users SET display_name = 'Apres' WHERE email = 'owner@example.org'" >/dev/null
sql "INSERT INTO update_probe VALUES (1, 'apres')" >/dev/null
check "update.sh --rollback --restore-db succeeds" logged rollback-2 update --rollback --restore-db
check "... the database of before the update is back" equals "$(display_name)/$(schema)" \
  "Avant/$v1_schema"
check "... the current database saved first (restore.sh)" \
  test -n "$(find "$work/safety" -name 'pre-restore-*.pgdump' 2>/dev/null | head -1)"
check "... the API healthy" api_healthy

echo "# 5. A migration marked 'Downgrade: lossy': the rollback restores the backup by itself"
git -C "$repo" tag v1.3.0 "$v13"
check "update.sh --to v1.3.0 succeeds" logged update-3 update --to v1.3.0
check "... schema 9002" equals "$(schema)" 9002
sql "UPDATE users SET display_name = 'Perdu', update_probe = 'x' WHERE email = 'owner@example.org'" \
  >/dev/null
check "update.sh --rollback succeeds" logged rollback-3 update --rollback
check "... naming the lossy migration" has "20990101_9002_lossy_column.py" "$work/rollback-3.log"
check "... the pre-update backup restored" equals "$(display_name)/$(schema)" "Avant/$v1_schema"

echo "# 6. A failing migration: automatic rollback"
git -C "$repo" tag v1.2.0 "$v12"
check "update.sh --to v1.2.0 fails" fails logged update-4 update --to v1.2.0
check "... saying LifeOS runs the previous version again" has "runs v1.0.0 again" "$work/update-4.log"
check "... the code and images of v1.0.0" equals "$(head_commit) $(deployed_tag)" "$v1 $(short "$v1")"
check "... schema untouched (the migrations run in one transaction)" equals "$(schema)" "$v1_schema"
check "... data kept, the API healthy" equals "$(display_name)" Avant
check "... recorded: the failed update, then the rollback" equals \
  "$(tail -2 "$infra/state/deployments.tsv" | cut -f2,10 | paste -sd' ')" \
  $'update\tfailed rollback\tok'

echo "# 7. Image retention and status"
check "update.sh --to v1.3.0 --keep 2 succeeds" logged update-5 update --to v1.3.0 --keep 2
check "... the images of the last two versions kept" equals \
  "$(image_id "$(short "$v1")") $([[ "$(image_id "$(short "$v13")")" != none ]] && echo present)" \
  "$local_id present"
check "... those of older ones removed (v1.1.0, v1.2.0)" equals \
  "$(image_id "$(short "$v11")") $(image_id "$(short "$v12")")" "none none"
check "... never the images tagged local" equals "$(image_id local)" "$local_id"
check "update.sh --status" logged status update --status
check "... lists the deployments and the kept images" has "$repo_name:$(short "$v13")" \
  "$work/status.log"
check "local changes to tracked files are refused" fails logged dirty \
  bash -c "echo '# local' >> '$infra/compose.yaml' && '$infra/scripts/update.sh' --no-fetch --yes"
git -C "$repo" checkout -q -- infra/compose.yaml

if [[ "$failures" -gt 0 || -n "${LIFEOS_TEST_VERBOSE:-}" ]]; then
  for log in "$work"/*.log; do echo "== ${log##*/}"; tail -40 "$log"; done
fi
if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) failed."
  exit 1
fi
echo "All update and rollback checks passed."
