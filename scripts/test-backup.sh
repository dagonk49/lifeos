#!/usr/bin/env bash
# Backup and restore round trip with the real scripts, the real backend image and restic (CI
# infra job; locally: Docker, restic >= 0.17, jq, python3). Everything runs on a throw-away
# copy of infra/ (own Compose project, secrets, two local restic repositories: main and
# "off-site"), so a local LifeOS is never touched:
#   1. a running LifeOS: owner, passkey, task, transaction, encrypted document;
#   2. backup.sh: one run = documents + database + config snapshots, in both repositories;
#      metrics; healthcheck pings;
#   3. restore-check.sh, from both repositories: every original decrypted;
#   4. coherence: a missing original is reported (never hidden), an incomplete pair refused;
#   5. blank machine (volumes deleted): restore.sh, then sign-in with the same passkey,
#      search, finances, and the original downloaded and decrypted;
#   6. failures: a wrong documents key is refused before anything is replaced; a failed
#      backup writes its metric and pings /fail; notify.sh posts an alert without personal
#      data;
#   7. restore-drill.sh on a second blank copy (install.sh replaced by a stand-in, the images
#      being built): key checked first, configuration of the backup, same passkey.
# LIFEOS_TEST_BACKEND_IMAGE: backend image to use (default lifeos-backend:local);
# LIFEOS_TEST_VERBOSE=1 prints every log at the end.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
image="${LIFEOS_TEST_BACKEND_IMAGE:-lifeos-backend:local}"
prefix="lifeos-backup-test-$$"
work="$(mktemp -d)"
infra="$work/infra"

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
lacks() { ! grep -qi -- "$1" "$2"; }
equals() { [[ "$1" == "$2" ]]; }
fails() { ! "$@"; }
# logged NAME COMMAND...: the command's output goes to $work/NAME.log (shown on failure).
logged() {
  local name="$1"
  shift
  "$@" > "$work/$name.log" 2>&1
}

compose() { (cd "$infra" && docker compose "$@"); }
client() { compose exec -T api python - "$@" < "$root/scripts/test-backup-client.py"; }
# The second, blank machine of the scripted drill (section 7).
vm_compose() { (cd "$work/vm2/infra" && COMPOSE_PROJECT_NAME="$prefix-vm2" docker compose "$@"); }
tasks_in_database() {
  compose exec -T postgres psql -U postgres -d lifeos -tAc 'SELECT count(*) FROM tasks'
}
snapshots() { restic snapshots --host lifeos --json; }
requests() { cat "$work/requests.log" 2>/dev/null || true; }

cleanup() {
  local status=$?
  # set -e may stop the script inside a redirected command (logged): back to the job's output.
  exec 1>&3 2>&4
  if [[ "$status" -ne 0 && "$failures" -eq 0 && -d "$infra" ]]; then
    # Stopped outside a check (set -e): show why, then clean up.
    echo "Aborted (status $status). Containers and their last logs:" >&2
    compose ps -a >&2 || true
    compose logs --no-color --tail 40 >&2 || true
    for log in "$work"/*.log; do
      [[ -f "$log" ]] && { echo "== ${log##*/}"; cat "$log" || true; } >&2
    done
  fi
  [[ -d "$infra" ]] && compose down -v --remove-orphans >/dev/null 2>&1
  [[ -d "$work/vm2/infra" ]] && vm_compose down -v --remove-orphans >/dev/null 2>&1
  docker volume rm -f "${prefix}_restore_check" >/dev/null 2>&1
  [[ -n "${stub:-}" ]] && kill "$stub" 2>/dev/null
  rm -rf "$work"
}
exec 3>&1 4>&2
trap cleanup EXIT
trap 'echo "Unexpected failure, line $LINENO: $BASH_COMMAND" >&2' ERR

# --- A throw-away instance ----------------------------------------------------------------
cp -r "$root/infra" "$work/"
mkdir -p "$work/backend"
cp -r "$root/backend/migrations" "$root/backend/pyproject.toml" "$work/backend/"
# Never the developer's own secrets, configuration or certificates.
find "$infra/secrets" "$infra/traefik/certs" -type f ! -name README.md ! -name .gitkeep -delete \
  2>/dev/null || true
rm -f "$infra/.env" "$infra/traefik/dynamic/tls.yml"
"$infra/scripts/init-secrets.sh" >/dev/null
cat > "$infra/compose.test.yaml" <<EOF
# Test overlay: the given backend image, and only what a backup needs.
services:
  migrate: { image: $image, pull_policy: never }
  api: { image: $image, pull_policy: never }
  worker: { image: $image, pull_policy: never }
  scheduler: { image: $image, pull_policy: never }
  ml: { profiles: [disabled] }
  stt: { profiles: [disabled] }
  web: { profiles: [disabled] }
  traefik: { profiles: [disabled] }
EOF
{
  sed -e 's/lifeos\.example\.lan/lifeos.test/' "$infra/.env.example"
  echo "COMPOSE_FILE=compose.yaml:compose.test.yaml"
  echo "LIFEOS_EMBEDDINGS_PROVIDER=disabled"
  echo "LIFEOS_EXTRACTION_PROVIDER=disabled"
  echo "LIFEOS_STT_PROVIDER=disabled"
} > "$infra/.env"

export COMPOSE_PROJECT_NAME="$prefix"
export RESTIC_REPOSITORY="$work/repository" RESTIC_PASSWORD_FILE="$work/restic-password"
export RESTIC_COPY_REPOSITORY="$work/offsite" RESTIC_COPY_PASSWORD_FILE="$work/offsite-password"
export LIFEOS_BACKUP_ENV=/nonexistent LIFEOS_METRICS_DIR="$work/metrics"
export LIFEOS_SAFETY_DIR="$work/safety" LIFEOS_RESTORE_TMPDIR="$work" TMPDIR="$work"
head -c 32 /dev/urandom | base64 > "$RESTIC_PASSWORD_FILE"
head -c 32 /dev/urandom | base64 > "$RESTIC_COPY_PASSWORD_FILE"
restic init -q
restic init -q --repo "$RESTIC_COPY_REPOSITORY" --password-file "$RESTIC_COPY_PASSWORD_FILE" \
  --from-repo "$RESTIC_REPOSITORY" --from-password-file "$RESTIC_PASSWORD_FILE" \
  --copy-chunker-params

# Stub of the healthcheck and alert endpoints: records every request.
python3 - "$work/requests.log" "$work/port" <<'EOF' &
import http.server, json, sys
class Handler(http.server.BaseHTTPRequestHandler):
    def record(self):
        size = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(size).decode("utf-8", "replace")
        entry = {"method": self.command, "path": self.path, "title": self.headers.get("Title"),
                 "authorization": self.headers.get("Authorization"), "body": body}
        with open(sys.argv[1], "a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.send_response(200)
        self.end_headers()
    do_GET = do_POST = do_HEAD = record
    def log_message(self, *args): pass
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
open(sys.argv[2], "w").write(str(server.server_address[1]))
server.serve_forever()
EOF
stub=$!
for _ in $(seq 1 50); do [[ -s "$work/port" ]] && break; sleep 0.1; done
stub_url="http://127.0.0.1:$(cat "$work/port")"
export LIFEOS_HEALTHCHECK_URL="$stub_url/ping/backup"
export LIFEOS_ALERT_URL="$stub_url/alert" LIFEOS_ALERT_TOKEN=test-token

echo "# 1. A running LifeOS with an owner and data"
logged up compose up -d --wait --wait-timeout 180 api
logged up-worker compose up -d worker
token="$(compose exec -T api lifeos-admin create-owner --email owner@example.org --name Owner \
  | sed -n 's/.*#token=//p')"
state="$(client setup "$token")"
document="$(jq -r .document <<<"$state")"
check "owner, passkey, task, transaction and document created" equals "$(tasks_in_database)" 1

echo "# 2. Backup run"
check "backup.sh succeeds" logged backup-1 "$infra/scripts/backup.sh"
run="$(snapshots | jq -r '[.[] | .tags[] | select(startswith("run:"))] | unique | .[0]')"
check "one run, three snapshots: documents, db, config" equals \
  "$(snapshots | jq -r --arg r "$run" '[.[] | select(.tags | index($r)) | .tags[]
    | select(. == "db" or . == "documents" or . == "config")] | sort | join(",")')" \
  "config,db,documents"
check "the db snapshot names the version, the schema and the key fingerprint" equals \
  "$(snapshots | jq -r '.[] | select(.tags | index("db")) | [.tags[] | split(":")[0]] | sort | join(",")')" \
  "db,dockey,lifeos,rev,run,version"
check "... and the fingerprint is the one of this server's key" equals \
  "$(snapshots | jq -r '.[] | select(.tags | index("db")) | .tags[] | select(startswith("dockey:"))')" \
  "dockey:$(tr -d '[:space:]' < "$infra/secrets/lifeos_documents_key" | sha256sum | cut -c1-12)"
check "the off-site copy holds the same run" equals "$(restic snapshots --repo "$RESTIC_COPY_REPOSITORY" \
  --password-file "$RESTIC_COPY_PASSWORD_FILE" --json | jq -r --arg r "$run" '[.[] | select(.tags | index($r))] | length')" 3
for metric in lifeos_backup_last_success_timestamp_seconds lifeos_backup_duration_seconds \
  lifeos_backup_offsite_last_success_timestamp_seconds 'lifeos_backup_missing_originals 0$'; do
  check "metric ${metric%% *}" has "^$metric" "$work/metrics/lifeos_backup.prom"
done
check "healthcheck pinged at start and on success" equals \
  "$(requests | jq -r 'select(.path | startswith("/ping/backup")) | .path' | paste -sd' ')" \
  "/ping/backup/start /ping/backup"

echo "# 3. Restore tests (production untouched)"
check "restore-check.sh: the latest run restores and every original decrypts" logged check-1 \
  "$infra/scripts/restore-check.sh"
check "... 1 original present and decrypted" has "originals: 1 present, 1 decrypted, 0 missing" \
  "$work/check-1.log"
check "restore-check.sh --offsite: the off-site copy restores too" logged check-offsite \
  "$infra/scripts/restore-check.sh" --offsite
check "metric lifeos_restore_check_last_success_timestamp_seconds" \
  has '^lifeos_restore_check_last_success_timestamp_seconds' "$work/metrics/lifeos_restore_check.prom"

echo "# 4. Coherence: missing originals, incomplete pairs"
key="$(compose exec -T postgres psql -U postgres -d lifeos -tAc \
  "SELECT storage_key FROM documents WHERE id = '$document'")"
compose exec -T worker rm "/data/documents/$key"
check "backup.sh still succeeds with an original missing on the server" logged backup-2 \
  "$infra/scripts/backup.sh"
check "... reports it by document id" has "missing original: document $document" "$work/backup-2.log"
check "... and in its metric" has '^lifeos_backup_missing_originals 1$' \
  "$work/metrics/lifeos_backup.prom"
check "restore-check.sh fails on that run" fails logged check-2 "$infra/scripts/restore-check.sh"
check "... and names the document" has "missing original: document $document" "$work/check-2.log"
check "restore-check.sh of the first run (by run id) still passes" logged check-3 \
  "$infra/scripts/restore-check.sh" "${run#run:}"
second="$(snapshots | jq -r 'map(select(.tags | index("documents"))) | sort_by(.time) | last | .id')"
restic forget -q "$second"
check "a run without its documents snapshot is refused by restore-check.sh" fails logged check-4 \
  "$infra/scripts/restore-check.sh"
check "... and by restore.sh, before anything is replaced" fails logged restore-0 \
  "$infra/scripts/restore.sh" latest --yes
check "... (the database is untouched)" equals "$(tasks_in_database)" 1

echo "# 5. Blank machine: restore.sh, then the owner signs in"
compose down -v >/dev/null 2>&1
check "restore.sh restores the first run on empty volumes" logged restore-1 \
  "$infra/scripts/restore.sh" "${run#run:}" --yes --no-safety-dump
check "... checked by verify-data (schema at head, original decrypted)" has "result: OK" \
  "$work/restore-1.log"
compose up -d --wait --wait-timeout 180 api >/dev/null 2>&1
compose up -d worker >/dev/null 2>&1
client check "$state" > "$work/client.log" 2>&1 || true
cat "$work/client.log"
check "the owner signs in, finds the task, the transaction and the original" \
  equals "$(grep -c '^ok - ' "$work/client.log")" 4

echo "# 6. Failures are refused or reported"
cp "$infra/secrets/lifeos_documents_key" "$work/key"
head -c 32 /dev/urandom | base64 > "$infra/secrets/lifeos_documents_key"
check "restore-check.sh refuses another documents key" fails logged check-5 \
  "$infra/scripts/restore-check.sh" "${run#run:}"
check "... saying so" has "is not the one of the backup" "$work/check-5.log"
check "restore.sh refuses it before replacing anything" fails logged restore-2 \
  "$infra/scripts/restore.sh" "${run#run:}" --yes
check "verify-data finds the originals unreadable with it" fails logged verify \
  compose run --rm --no-deps -T api lifeos-admin verify-data
check "... (status FAILED)" has "originals_unreadable" "$work/verify.log"
cp "$work/key" "$infra/secrets/lifeos_documents_key"
check "with a safety dump of the current database first" logged restore-3 \
  "$infra/scripts/restore.sh" "${run#run:}" --yes
check "... kept apart" test -s "$(find "$work/safety" -name 'pre-restore-*.pgdump' | head -1)"

head -c 32 /dev/urandom | base64 > "$work/wrong-password"
check "a backup with a wrong repository password fails" fails logged backup-3 env \
  RESTIC_PASSWORD_FILE="$work/wrong-password" "$infra/scripts/backup.sh"
check "... writes its failure metric" has '^lifeos_backup_last_failure_timestamp_seconds' \
  "$work/metrics/lifeos_backup.prom"
check "... and pings /fail" has '"/ping/backup/fail"' "$work/requests.log"
check "notify.sh posts the alert" logged notify "$infra/scripts/notify.sh" lifeos-backup.service
alert="$(requests | jq -c 'select(.path == "/alert")' | tail -1)"
check "... with a title and the token" equals \
  "$(jq -r '[.title, .authorization] | join("|")' <<<"$alert")" \
  "LifeOS: lifeos-backup failed|Bearer test-token"
check "... naming the unit and the host" has "lifeos-backup.service sur $(uname -n)" \
  <(jq -r .body <<<"$alert")
check "... and no personal data" lacks "owner@example.org\|passeport\|Dupain" <(requests)
check "notify.sh without a channel says so (status 2)" equals \
  "$(LIFEOS_ALERT_URL='' "$infra/scripts/notify.sh" --test >/dev/null 2>&1; echo $?)" 2

echo "# 7. Scripted drill on a second, blank machine (restore-drill.sh)"
compose down -v >/dev/null 2>&1  # the first machine is gone
mkdir -p "$work/vm2/backend"
cp -r "$root/infra" "$work/vm2/"
cp -r "$root/backend/migrations" "$root/backend/pyproject.toml" "$work/vm2/backend/"
find "$work/vm2/infra/secrets" "$work/vm2/infra/traefik/certs" -type f ! -name README.md \
  ! -name .gitkeep -delete 2>/dev/null || true
rm -f "$work/vm2/infra/.env" "$work/vm2/infra/traefik/dynamic/tls.yml"
cp "$infra/compose.test.yaml" "$work/vm2/infra/"
cat > "$work/vm2/infra/scripts/install.sh" <<'EOF'
#!/usr/bin/env bash
# Test stand-in for install.sh: the images are already built.
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/init-secrets.sh >/dev/null
docker compose up -d --wait --wait-timeout 180 api
docker compose up -d worker
EOF
drill() { COMPOSE_PROJECT_NAME="$prefix-vm2" "$work/vm2/infra/scripts/restore-drill.sh" "$@"; }
head -c 32 /dev/urandom | base64 > "$work/other-key"
check "restore-drill.sh refuses a key that is not the backup's" fails logged drill-0 \
  drill --domain lifeos.test --key-file "$work/other-key" --run "${run#run:}" --yes
check "... before installing anything" test ! -e "$work/vm2/infra/secrets/lifeos_documents_key"
check "restore-drill.sh restores the first run on the blank machine" logged drill-1 \
  drill --domain lifeos.test --key-file "$work/key" --run "${run#run:}" \
  --enroll-email owner@example.org --yes
check "... with the configuration of the backup" has "restored: infra/.env of the backup" \
  "$work/drill-1.log"
check "... the assistant disabled for the drill" equals \
  "$(sed -n 's/^LIFEOS_LLM_PROVIDER=//p' "$work/vm2/infra/.env")" disabled
check "... a sign-in link for the owner" has "enroll#token=" "$work/drill-1.log"
check "... and a line for the drill log" has "^| $(date +%Y-%m-%d) | ${run#run:} | " \
  "$work/drill-1.log"
vm_compose exec -T api python - check "$state" < "$root/scripts/test-backup-client.py" \
  > "$work/client-vm2.log" 2>&1 || true
check "on the drill machine, the owner signs in with the same passkey and finds everything" \
  equals "$(grep -c '^ok - ' "$work/client-vm2.log")" 4

if [[ "$failures" -gt 0 || -n "${LIFEOS_TEST_VERBOSE:-}" ]]; then
  for log in "$work"/*.log; do echo "== ${log##*/}"; tail -40 "$log"; done
fi
if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) failed."
  exit 1
fi
echo "All backup and restore checks passed."
