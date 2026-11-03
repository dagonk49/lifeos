#!/usr/bin/env bash
# Acceptance tool test (CI infra job; locally: Docker, jq, openssl, curl): the real
# infra/scripts/recette.sh against a LifeOS started by Compose with the real configuration, in
# a throw-away copy of infra/ with its own Compose project: a local LifeOS is never touched.
#   1. LifeOS up: Traefik with the certificate of a local authority (as mkcert), the API, the
#      worker, the scheduler, the web client, the ml service when its image is given (OCR), and
#      the fake LLM provider in a space where the AI is allowed.
#   2. recette.sh with a browser session (an owner, and a session made in the database): every
#      check passes (exit status 0); the report has the machine, the verdict, the times of each
#      phase and the memory peak of each service against its limit; the raw measurements have
#      the expected operations; the document and the conversation of the run are deleted; no
#      file of the report holds the domain, the e-mail, the session, a title or a reply.
#   3. A session that is not valid and a certificate the machine does not trust: the API
#      measurements are left out and said so, the HTTPS check fails, and so does the run.
# LIFEOS_TEST_BACKEND_IMAGE (default lifeos-backend:local), LIFEOS_TEST_WEB_IMAGE (default
# lifeos-web:local), LIFEOS_TEST_ML_IMAGE (optional: the OCR phases and a document read by the
# worker); LIFEOS_TEST_VERBOSE=1 prints every log at the end.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
backend_image="${LIFEOS_TEST_BACKEND_IMAGE:-lifeos-backend:local}"
web_image="${LIFEOS_TEST_WEB_IMAGE:-lifeos-web:local}"
ml_image="${LIFEOS_TEST_ML_IMAGE:-}"
prefix="lifeos-recette-test-$$"
work="$(mktemp -d)"
infra="$work/infra"
domain=lifeos.test
email=owner@example.org
owner_name=Proprietaire

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
recette() { "$infra/scripts/recette.sh" "$@"; }
# ops FILE [PHASE]: the operations measured (in PHASE), sorted and unique, comma separated.
ops() { jq -r --arg p "${2:-}" 'select($p == "" or .phase == $p) | .op' "$1" | sort -u | paste -sd,; }
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
    compose logs --no-color --tail 30 >&2 2>/dev/null || true
  fi
  [[ -d "$infra" ]] && compose down -v --remove-orphans >/dev/null 2>&1
  rm -rf "$work"
}
trap cleanup EXIT
trap 'echo "Unexpected failure, line $LINENO: $BASH_COMMAND" >&2' ERR

# --- A throw-away copy of infra/ -----------------------------------------------------------
cp -r "$root/infra" "$work/"
find "$infra" -name __pycache__ -prune -exec rm -rf {} +
find "$infra/secrets" "$infra/traefik/certs" "$infra/blackbox/ca" -type f ! -name README.md \
  ! -name .gitkeep -delete 2>/dev/null || true
rm -rf "$infra/.env" "$infra/state" "$infra/traefik/dynamic/tls.yml"
export COMPOSE_PROJECT_NAME="$prefix"
"$infra/scripts/init-secrets.sh" >/dev/null
extraction=disabled
[[ -z "$ml_image" ]] || extraction=remote
{
  sed -e "s/lifeos\.example\.lan/$domain/" "$infra/.env.example"
  echo "COMPOSE_FILE=compose.yaml:compose.test.yaml"
  echo "LIFEOS_LLM_PROVIDER=fake"
  echo "LIFEOS_EMBEDDINGS_PROVIDER=disabled"
  echo "LIFEOS_EXTRACTION_PROVIDER=$extraction"
  echo "LIFEOS_STT_PROVIDER=disabled"
} > "$infra/.env"
# Test overlay: the given images (never built), no published port, no speech model.
{
  cat <<EOF
x-test-backend: &test-backend
  build: !reset null
  image: $backend_image
services:
  migrate: *test-backend
  api: *test-backend
  worker: *test-backend
  scheduler: *test-backend
  web:
    build: !reset null
    image: $web_image
  traefik:
    ports: !reset []
  stt: { profiles: [disabled] }
EOF
  if [[ -n "$ml_image" ]]; then
    printf '  ml:\n    build: !reset null\n    image: %s\n' "$ml_image"
  else
    echo "  ml: { profiles: [disabled] }"
  fi
} > "$infra/compose.test.yaml"

# Certificates: a local authority (as mkcert), its root where recette.sh (and the monitoring)
# look for it, and a 30-day certificate for the domain.
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=LifeOS test authority" \
  -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign \
  -keyout "$work/ca.key" -out "$work/ca.pem" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=$domain" -keyout "$infra/traefik/certs/privkey.pem" \
  -out "$work/server.csr" 2>/dev/null
openssl x509 -req -in "$work/server.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" \
  -CAcreateserial -days 30 -out "$infra/traefik/certs/fullchain.pem" \
  -extfile <(printf 'subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\n' "$domain") 2>/dev/null
chmod 644 "$infra/traefik/certs"/*.pem
cp "$infra/traefik/dynamic/tls.yml.example" "$infra/traefik/dynamic/tls.yml"
install -m 644 "$work/ca.pem" "$infra/blackbox/ca/rootCA.pem"

echo "# 1. LifeOS up$([[ -n "$ml_image" ]] && echo ", with ml" || echo ", without ml")"
# Waits for the services with a health check only (Traefik has none: Compose 2.x refuses it).
waited=(api web)
[[ -z "$ml_image" ]] || waited+=(ml)
logged up compose up -d
logged wait compose up -d --wait --wait-timeout 240 "${waited[@]}"
compose exec -T api lifeos-admin create-owner --email "$email" --name "$owner_name" >/dev/null
# The AI allowed in the personal space (the fake provider answers), and a browser session: its
# token in a file, only its SHA-256 in the database (as a sign-in leaves it).
sql "UPDATE spaces SET cloud_ai_enabled = true WHERE kind = 'personal'" >/dev/null
token="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')"
(umask 077 && printf '%s\n' "$token" > "$work/session")
sql "INSERT INTO user_sessions (id, user_id, token_hash, authenticated_at, last_seen_at,
  expires_at, created_at) SELECT gen_random_uuid(), id, sha256(convert_to('$token', 'UTF8')),
  now(), now(), now() + interval '1 hour', now() FROM users WHERE email = '$email'" >/dev/null
# The domain resolves to Traefik's address, as LIFEOS_PROBE_ADDRESS (a reverse proxy elsewhere).
address="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' \
  "$(compose ps -q traefik)" | awk '{ print $1 }')"
echo "LIFEOS_PROBE_ADDRESS=$address" >> "$infra/.env"
traefik_up() {
  curl -fsS -o /dev/null --noproxy '*' --cacert "$work/ca.pem" \
    --resolve "$domain:443:$address" "https://$domain/healthz" 2>/dev/null
}
check "Traefik answers https://$domain/ with the local authority's certificate" \
  wait_until 60 traefik_up

echo "# 2. recette.sh with a session"
out="$work/out"
check "recette.sh succeeds (exit status 0)" logged run recette --rounds 1 --load-seconds 15 \
  --chat 1 --session-file "$work/session" --out "$out"
report="$out/rapport.md"
check "... a report, with the machine and its settings" has "| Paramètres | --rounds 1" "$report"
check "... every verdict passed" bash -c "! grep -q '❌' '$report'"
for verdict in "Toutes les mesures ont abouti" "Aucun processus tué faute de mémoire" \
  "Aucun service redémarré" "Chaque service reste sous 90 % de sa limite mémoire" \
  "Certificat accepté" "HSTS envoyé" "CSP avec nonce sur les pages" "/metrics non publié" \
  "Certificat valable encore 29 jours"; do
  check "... ✅ $verdict" has "✅ $verdict" "$report"
done
expected="api_chat,api_document,api_healthz,api_search,ocr_photo,ocr_scan_3p,web_login"
[[ -n "$ml_image" ]] || expected="api_chat,api_healthz,api_search,web_login"
check "... the operations measured ($expected)" equals "$(ops "$out/timings.jsonl")" "$expected"
check "... all of them during the load too" equals "$(ops "$out/timings.jsonl" charge)" "$expected"
peaks() {
  local service
  for service in postgres redis api worker scheduler web traefik ${ml_image:+ml}; do
    grep -Eq "^\| $service \| [0-9]+ \| [0-9]+ \| [0-9]+ % \| repos " "$report" || return 1
  done
}
check "... the memory peak of every service, against its limit, by phase" peaks
check "... with the limits of compose.yaml (api 768 MiB, postgres 2 GiB)" bash -c \
  "grep -q '^| api | 768 |' '$report' && grep -q '^| postgres | 2048 |' '$report'"
check "... the machine's memory and the skipped phases" bash -c "grep -q 'mémoire disponible minimale' \
  '$report' && grep -q '⏭️ Non mesuré : dictée' '$report'"
if [[ -n "$ml_image" ]]; then
  check "... OCR seconds per page" has "s par page en moyenne" "$report"
fi
check "the document and the conversation of the run are deleted" equals \
  "$(sql 'SELECT count(*) FROM documents')/$(sql 'SELECT count(*) FROM conversations')" "0/0"
check "... the assistant did answer (fake provider)" equals \
  "$(jq -r 'select(.op == "api_chat") | .outcome' "$out/timings.jsonl" | sort -u)" ok
private() {
  grep -rqiF -e "$domain" -e "$email" -e "$token" -e "$address" -e "$owner_name" \
    -e "Recette technique" -e "[fake]" "$out"
}
check "no file of the report holds the domain, the e-mail, the session, a title or a reply" \
  fails private
check "... nor the console output" bash -c "! grep -q -e '$token' -e '$email' '$work/run.log'"

echo "# 3. A session that is not valid, a certificate the machine does not trust"
head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=' > "$work/stale-session"
rm "$infra/blackbox/ca/rootCA.pem"
check "recette.sh fails (exit status 1)" fails logged run-bad recette --rounds 1 \
  --load-seconds 0 --session-file "$work/stale-session" --out "$work/out-bad"
bad="$work/out-bad/rapport.md"
check "... the certificate refused" has "❌ Certificat accepté" "$bad"
check "... the API measurements left out, and why" has \
  "⏭️ Non mesuré : mesures par l'API (session refusée" "$bad"
expected="api_healthz,web_login"
[[ -z "$ml_image" ]] || expected="api_healthz,ocr_photo,ocr_scan_3p,web_login"
check "... the other measurements still made ($expected)" equals \
  "$(ops "$work/out-bad/timings.jsonl")" "$expected"
check "bad usage is refused (exit status 2)" bash -c \
  "'$infra/scripts/recette.sh' --rounds 0 >/dev/null 2>&1; [[ \$? -eq 2 ]]"

if [[ "$failures" -gt 0 || -n "${LIFEOS_TEST_VERBOSE:-}" ]]; then
  for log in "$work"/*.log; do echo "== ${log##*/}"; tail -40 "$log"; done
  [[ -f "$report" ]] && { echo "== rapport.md"; cat "$report"; }
fi
if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) failed."
  exit 1
fi
echo "All acceptance tool checks passed."
