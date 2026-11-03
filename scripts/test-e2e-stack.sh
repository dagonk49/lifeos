#!/usr/bin/env bash
# Browser journeys on a production-like installation (CI e2e-stack job; locally: Docker, jq,
# openssl, curl, certutil from libnss3-tools, restic >= 0.17, and apps/web's dependencies with
# Playwright's Chromium). Everything runs on a throw-away copy of infra/ with its own Compose
# project, so a local LifeOS is never touched:
#   1. LifeOS up as deployed: the built images, Traefik with TLS from a local authority (its
#      root trusted in the NSS database of the browser: WebAuthn refuses certificate errors),
#      LIFEOS_ENVIRONMENT=production, secure cookies, worker and scheduler; fake providers
#      for the LLM, embeddings, extraction and speech (no ml/stt service, no network).
#   2. HTTPS answers with HSTS, and the owner is created with lifeos-admin, as on a server.
#   3. Playwright, project `stack`: the priority journeys (mvp, notifications, outage, purge,
#      security, session, spaces-agents), then the first phase of the restore journey (data,
#      and the virtual passkey kept aside). Backend commands go through `docker compose exec`.
#   4. backup.sh, volumes deleted (blank machine), restore.sh, LifeOS up again.
#   5. Playwright, second phase: the same passkey signs in; task, transaction and original back.
# Reports: apps/web/playwright-report/stack-*/ and apps/web/test-results/stack-*/ (JUnit too).
# LIFEOS_TEST_BACKEND_IMAGE (default lifeos-backend:local), LIFEOS_TEST_WEB_IMAGE (default
# lifeos-web:local); LIFEOS_TEST_NSSDB (default ~/.pki/nssdb); LIFEOS_TEST_VERBOSE=1 prints
# every log at the end (LIFEOS_TEST_LOG_LINES lines of each, default 40). Extra arguments go
# to the first Playwright run (e.g. a spec file).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
backend_image="${LIFEOS_TEST_BACKEND_IMAGE:-lifeos-backend:local}"
web_image="${LIFEOS_TEST_WEB_IMAGE:-lifeos-web:local}"
nssdb="${LIFEOS_TEST_NSSDB:-$HOME/.pki/nssdb}"
prefix="lifeos-e2e-stack-$$"
work="$(mktemp -d)"
infra="$work/infra"
domain=lifeos.test
email=e2e@example.org

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
logged() {
  local name="$1"
  shift
  "$@" > "$work/$name.log" 2>&1
}
compose() { (cd "$infra" && docker compose "$@"); }
# wait_until SECONDS COMMAND...: polls every 2 seconds.
wait_until() {
  local deadline=$((SECONDS + $1))
  shift
  until "$@"; do
    [[ "$SECONDS" -lt "$deadline" ]] || return 1
    sleep 2
  done
}

cleanup() {
  local status=$?
  if [[ -d "$infra" && ( "$failures" -gt 0 || "$status" -ne 0 ) ]]; then
    echo "Containers and their last logs:" >&2
    compose ps -a >&2 2>/dev/null || true
    compose logs --no-color --tail 40 >&2 2>/dev/null || true
  fi
  [[ -d "$infra" ]] && compose down -v --remove-orphans >/dev/null 2>&1
  certutil -d "sql:$nssdb" -D -n "$prefix" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
trap 'echo "Unexpected failure, line $LINENO: $BASH_COMMAND" >&2' ERR

# --- A throw-away copy of infra/ -----------------------------------------------------------
cp -r "$root/infra" "$work/"
mkdir -p "$work/backend"
cp -r "$root/backend/migrations" "$root/backend/pyproject.toml" "$work/backend/"
find "$infra" -name __pycache__ -prune -exec rm -rf {} +
# Never the developer's own secrets, configuration or certificates.
find "$infra/secrets" "$infra/traefik/certs" "$infra/blackbox/ca" -type f ! -name README.md \
  ! -name .gitkeep -delete 2>/dev/null || true
rm -rf "$infra/.env" "$infra/state" "$infra/traefik/dynamic/tls.yml"
export COMPOSE_PROJECT_NAME="$prefix"
"$infra/scripts/init-secrets.sh" >/dev/null
{
  # LIFEOS_ENVIRONMENT=production and the secure cookies come from the example as deployed.
  sed -e "s/lifeos\.example\.lan/$domain/" "$infra/.env.example"
  echo "COMPOSE_FILE=compose.yaml:compose.test.yaml"
  echo "LIFEOS_LOG_LEVEL=WARNING"
  echo "LIFEOS_LLM_PROVIDER=fake"
  echo "LIFEOS_EMBEDDINGS_PROVIDER=fake"
  echo "LIFEOS_EXTRACTION_PROVIDER=fake"
  echo "LIFEOS_STT_PROVIDER=fake"
} > "$infra/.env"
# Test overlay: the given images (never built nor pulled), no published port, no model
# service (the fake providers run in the api and the worker).
cat > "$infra/compose.test.yaml" <<EOF
x-test-backend: &test-backend
  build: !reset null
  image: $backend_image
  pull_policy: never
services:
  migrate: *test-backend
  api: *test-backend
  worker: *test-backend
  scheduler: *test-backend
  web:
    build: !reset null
    image: $web_image
    pull_policy: never
  traefik:
    ports: !reset []
  ml: { profiles: [disabled] }
  stt: { profiles: [disabled] }
EOF

# Certificates: a local authority (as mkcert) and a certificate for the domain.
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=LifeOS e2e authority $$" \
  -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign \
  -keyout "$work/ca.key" -out "$work/ca.pem" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=$domain" -keyout "$infra/traefik/certs/privkey.pem" \
  -out "$work/server.csr" 2>/dev/null
openssl x509 -req -in "$work/server.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" \
  -CAcreateserial -days 30 -out "$infra/traefik/certs/fullchain.pem" \
  -extfile <(printf 'subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\n' "$domain") 2>/dev/null
chmod 644 "$infra/traefik/certs"/*.pem
cp "$infra/traefik/dynamic/tls.yml.example" "$infra/traefik/dynamic/tls.yml"
# The one change to the deployed configuration: the sign-in rate limit (30 a minute per
# address, checked by scripts/test-traefik.sh) would refuse a suite that signs in from one
# address dozens of times in a few minutes.
sed -i 's/^\( *average:\) 30$/\1 3000/' "$infra/traefik/dynamic/lifeos.yml"
grep -q 'average: 3000$' "$infra/traefik/dynamic/lifeos.yml"
# Trusted by Chromium for this run only (removed by the cleanup).
mkdir -p "$nssdb"
[[ -f "$nssdb/cert9.db" ]] || certutil -d "sql:$nssdb" -N --empty-password
certutil -d "sql:$nssdb" -A -t "C,," -n "$prefix" -i "$work/ca.pem"

# up NAME: the stack started, then Traefik's address (a new container after a restore may
# get another one) in $address.
up() {
  logged "up-$1" compose up -d
  logged "wait-$1" compose up -d --wait --wait-timeout 240 api web
  address="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' \
    "$(compose ps -q traefik)" | awk '{ print $1 }')"
}

echo "# 1. LifeOS up, as deployed (production settings, Traefik, TLS, worker, scheduler)"
address=""
up 1
https_up() {
  curl -fsS -o /dev/null --noproxy '*' --cacert "$work/ca.pem" \
    --resolve "$domain:443:$address" "https://$domain/healthz" 2>/dev/null
}
check "Traefik answers https://$domain/ with the local authority's certificate" wait_until 60 https_up
curl -fsS -D "$work/headers.txt" -o /dev/null --noproxy '*' --cacert "$work/ca.pem" \
  --resolve "$domain:443:$address" "https://$domain/login" || true
check "... with HSTS" grep -qi '^strict-transport-security: max-age=' "$work/headers.txt"
check "... in production mode, scheduler running" bash -c "[[ \"\$(cd '$infra' && docker compose \
  exec -T api printenv LIFEOS_ENVIRONMENT)\" == production && -n \"\$(cd '$infra' && docker compose \
  ps -q --status running scheduler)\" ]]"
compose exec -T api lifeos-admin create-owner --email "$email" --name Camille >/dev/null

# Playwright against the stack (apps/web/playwright.config.ts, project `stack`).
playwright() {
  local phase="$1"
  shift
  # Node's own requests (`request` fixture, route.fetch) reach Traefik and trust the
  # authority too (e2e/stack/resolve.cjs), as Chromium does.
  (cd "$root/apps/web" && LIFEOS_E2E_STACK="$infra" LIFEOS_E2E_BASE_URL="https://$domain" \
    LIFEOS_E2E_STACK_HOST="$domain" LIFEOS_E2E_STACK_ADDRESS="$address" \
    NODE_OPTIONS="--require $root/apps/web/e2e/stack/resolve.cjs" NODE_EXTRA_CA_CERTS="$work/ca.pem" \
    LIFEOS_E2E_PHASE="$phase" LIFEOS_E2E_RUN="stack-$phase" LIFEOS_E2E_STATE="$work/credential.json" \
    pnpm exec playwright test --project=stack "$@")
}

echo "# 2. The priority journeys in the browser, then the data of the restore journey"
check "the journeys pass on the stack" playwright backup "$@"
check "... the passkey is kept aside for after the restore" test -s "$work/credential.json"

echo "# 3. Backup, blank machine, restore"
export RESTIC_REPOSITORY="$work/repository" RESTIC_PASSWORD_FILE="$work/restic-password"
export LIFEOS_BACKUP_ENV=/nonexistent LIFEOS_METRICS_DIR="$work/metrics"
export LIFEOS_SAFETY_DIR="$work/safety" LIFEOS_RESTORE_TMPDIR="$work" TMPDIR="$work"
head -c 32 /dev/urandom | base64 > "$RESTIC_PASSWORD_FILE"
restic init -q
check "backup.sh succeeds" logged backup "$infra/scripts/backup.sh"
compose down -v >/dev/null 2>&1
check "restore.sh restores the latest run on empty volumes" logged restore \
  "$infra/scripts/restore.sh" latest --yes --no-safety-dump
check "... checked by verify-data" has "result: OK" "$work/restore.log"
up 2
check "LifeOS answers again after the restore" wait_until 60 https_up

echo "# 4. After the restore, in the browser"
check "the same passkey signs in and finds the task, the transaction and the original" \
  playwright restored stack/restore.spec.ts

if [[ "$failures" -gt 0 || -n "${LIFEOS_TEST_VERBOSE:-}" ]]; then
  for log in "$work"/*.log; do echo "== ${log##*/}"; tail -"${LIFEOS_TEST_LOG_LINES:-40}" "$log"; done
fi
if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) failed."
  exit 1
fi
echo "All production-like end-to-end checks passed."
