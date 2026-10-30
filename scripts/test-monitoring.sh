#!/usr/bin/env bash
# Monitoring and alerting test, with the real images and the real configuration (CI infra job;
# locally: Docker, jq, openssl). Everything runs in a throw-away copy of infra/ with its own
# Compose project: a local LifeOS is never touched.
#   1. Alert rules: promtool check config / rules, and the unit tests (alerts.test.yml): each
#      simulated failure raises its alert with its French texts; every alert is tested, and
#      the texts only use labels that hold no personal data.
#   2. install-monitoring.sh writes the Alertmanager configuration (ntfy, token, e-mail,
#      Telegram, dead man's switch), checked by amtool, never prints a secret, and refuses bad
#      settings.
#   3. Simulated outages, end to end, with the Compose services of the `monitoring` profile:
#      the backup metrics, written by the backup scripts' own helper, say that the last backup
#      is 40 hours old and that two originals are missing; the worker and the API are absent;
#      then Traefik stops. Prometheus (the rules' `for:` delays shortened) raises the alerts,
#      Alertmanager sends them to a stub ntfy server: one exploitable notification each (title,
#      summary, what to do, priority, token), the dead man's switch signal, and no personal
#      data, even from an alert whose labels carry some.
#   4. The public probes (blackbox exporter, real blackbox.yml): through LIFEOS_PROBE_ADDRESS
#      (above), and through the host's published port (host-gateway), with Traefik owning the
#      port and behind a TLS reverse proxy (Nginx Proxy Manager style); a certificate signed by
#      a local authority is trusted once its root is in blackbox/ca/, refused otherwise.
# LIFEOS_TEST_VERBOSE=1 prints every log at the end.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
prefix="lifeos-monitoring-test-$$"
work="$(mktemp -d)"
infra="$work/infra"
whoami_image=traefik/whoami:v1.11.0
nginx_image=nginx:1.29-alpine
# The stub ntfy server runs in the base image of the LifeOS images (backend/Dockerfile).
python_image=python:3.13-slim
token=tk_test_ntfy_secret

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
lacks() { ! grep -q -- "$1" "$2"; }
equals() { [[ "$1" == "$2" ]]; }
fails() { ! "$@"; }
logged() {
  local name="$1"
  shift
  "$@" > "$work/$name.log" 2>&1
}
# wait_until SECONDS COMMAND...: polls every 5 seconds.
wait_until() {
  local deadline=$((SECONDS + $1))
  shift
  until "$@"; do
    [[ "$SECONDS" -lt "$deadline" ]] || return 1
    sleep 5
  done
}

compose() { (cd "$infra" && docker compose --profile monitoring "$@"); }
# PromQL on the test Prometheus: the first value, or "none".
value() {
  compose exec -T prometheus wget -qO- --post-data "query=$(jq -rn --arg q "$1" '$q | @uri')" \
    http://127.0.0.1:9090/api/v1/query 2>/dev/null | jq -r '.data.result[0].value[1] // "none"'
}
# probe MODULE URL: probe_success of the blackbox exporter, asked directly.
probe() {
  compose exec -T blackbox-exporter wget -qO- \
    "http://127.0.0.1:9115/probe?module=$1&target=$(jq -rn --arg u "$2" '$u | @uri')" \
    2>/dev/null | sed -n 's/^probe_success //p'
}
requests="$work/ntfy/requests.jsonl"
# The ntfy messages (JSON), their texts as the reader sees them (no-break spaces as spaces).
messages() {
  [[ -f "$requests" ]] || return 0
  jq -c 'select(.path == "/") | .body | fromjson? | select(type == "object")
    | with_entries(if (.value | type) == "string" then .value |= gsub("\u00a0"; " ") else . end)' \
    "$requests"
}
# notification TITLE: the ntfy message published with this title, or nothing.
notification() { messages | jq -c --arg title "$1" 'select(.title == $title)' | head -1; }
notified() { [[ -n "$(notification "$1")" ]]; }
field() { jq -r --arg f "$2" '.[$f] | if type == "array" then join(",") else tostring end' <<<"$1"; }

cleanup() {
  local status=$?
  if [[ -d "$infra" && ( "$failures" -gt 0 || "$status" -ne 0 ) ]]; then
    echo "Containers and their last logs:" >&2
    compose ps -a >&2 2>/dev/null || true
    compose logs --no-color --tail 30 prometheus alertmanager blackbox-exporter >&2 2>/dev/null \
      || true
  fi
  docker rm -f "$prefix-traefik" "$prefix-proxied" "$prefix-npm" "$prefix-api" "$prefix-web" \
    >/dev/null 2>&1 || true
  [[ -d "$infra" ]] && compose down -v --remove-orphans >/dev/null 2>&1
  docker network rm "$prefix-edge" "$prefix-proxy" >/dev/null 2>&1 || true
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
prometheus_image="$(sed -n 's/^ *image: \(prom\/prometheus:.*\)$/\1/p' "$infra/compose.yaml")"
traefik_image="$(sed -n 's/^ *image: \(traefik:.*\)$/\1/p' "$infra/compose.yaml" | head -1)"

echo "# 1. Alert rules"
promtool() {
  docker run --rm --network none -v "$infra/prometheus:/etc/prometheus:ro" -w /etc/prometheus \
    --entrypoint promtool "$prometheus_image" "$@"
}
check "prometheus.yml is valid, with its rules" logged promtool-config \
  promtool check config prometheus.yml
check "alerts.yml is valid" logged promtool-rules promtool check rules alerts.yml
check "each simulated failure raises its alert, with its texts (alerts.test.yml)" \
  logged promtool-test promtool test rules alerts.test.yml
check "every alert has a unit test" equals \
  "$(sed -n 's/^ *alertname: //p' "$infra/prometheus/alerts.test.yml" | sort -u)" \
  "$(sed -n 's/^ *- alert: //p' "$infra/prometheus/alerts.yml" | sort -u)"
# shellcheck disable=SC2016  # $labels of the alert templates, not a shell variable
labels_used="$(grep -o '\$labels\.[a-z_]*' "$infra/prometheus/alerts.yml" | sed 's/^\$labels\.//' \
  | sort -u | paste -sd' ')"
safe_labels() {
  local label
  for label in $labels_used; do
    [[ " dependency job mountpoint purpose queue target task " == *" $label "* ]] || return 1
  done
}
check "the texts only use labels without personal data ($labels_used)" safe_labels
check "the probe targets refuse a domain that is not a host name" fails logged bad-domain \
  docker run --rm --network none -e LIFEOS_DOMAIN='lifeos.test/x?y' \
  -v "$infra/prometheus/entrypoint.sh:/etc/prometheus/entrypoint.sh:ro" \
  --entrypoint /bin/sh "$prometheus_image" /etc/prometheus/entrypoint.sh --version

echo "# 2. Alertmanager configuration (install-monitoring.sh)"
export COMPOSE_PROJECT_NAME="$prefix" LIFEOS_BACKUP_ENV=/nonexistent
"$infra/scripts/init-secrets.sh" >/dev/null
mkdir -m 755 "$work/metrics" "$work/ntfy"
{
  sed -e 's/lifeos\.example\.lan/lifeos.test/' "$infra/.env.example"
  echo "COMPOSE_FILE=compose.yaml:compose.test.yaml"
  echo "LIFEOS_METRICS_DIR=$work/metrics"
} > "$infra/.env"
# Test overlay: no published port, the rules with their delays shortened (alerts.fast.yml),
# the host's root without `rslave` (a test machine's / may be a private mount), a stub ntfy.
cat > "$infra/compose.test.yaml" <<EOF
services:
  prometheus:
    ports: !reset []
    volumes: !override
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./alerts.fast.yml:/etc/prometheus/alerts.yml:ro
      - ./prometheus/entrypoint.sh:/etc/prometheus/entrypoint.sh:ro
      - prometheusdata:/prometheus
  node-exporter:
    volumes: !override
      - /:/host:ro
      - \${LIFEOS_METRICS_DIR}:/textfile:ro
  ntfy:
    image: $python_image
    profiles: [monitoring]
    user: "$(id -u):$(id -g)"
    command: ["python", "-u", "/stub/ntfy.py"]
    volumes: ["$work/ntfy:/stub"]
    networks: [monitoring]
EOF
sed -E 's/^( +)for: .+$/\1for: 0s/' "$infra/prometheus/alerts.yml" > "$infra/alerts.fast.yml"
cat > "$work/ntfy/ntfy.py" <<'EOF'
# Stub ntfy server: records every POST (path, Authorization header, body) as a JSON line.
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        record = {"path": self.path, "authorization": self.headers.get("Authorization"),
                  "body": body}
        with open("/stub/requests.jsonl", "a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
EOF

monitoring() { "$infra/scripts/install-monitoring.sh" "$@"; }
config="$infra/secrets/alertmanager.yml"
printf '%s\n' "$token" > "$work/ntfy-token"
printf '%s\n' smtp-test-secret > "$work/smtp-password"
printf '%s\n' 123456:telegram-test-secret > "$work/telegram-token"
check "ntfy, token, e-mail, Telegram and dead man's switch: accepted by amtool" \
  logged config-full monitoring --config-only --alert-url https://ntfy.test/lifeos-alertes \
  --alert-token-file "$work/ntfy-token" --deadman-url https://hc.test/ping/0000 \
  --email-to owner@example.org --smtp-host smtp.test:587 --smtp-from lifeos@example.org \
  --smtp-user lifeos --smtp-password-file "$work/smtp-password" \
  --telegram-chat-id 123456 --telegram-token-file "$work/telegram-token"
every_channel() {
  grep -q 'url: "https://ntfy.test/"' "$config" && grep -q 'topic: "lifeos-alertes"' "$config" \
    && grep -q "credentials: \"$token\"" "$config" && grep -q 'email_configs:' "$config" \
    && grep -q 'auth_password: "smtp-test-secret"' "$config" \
    && grep -q 'telegram_configs:' "$config" && grep -q 'name: deadman' "$config"
}
check "... with every channel and its secrets" every_channel
check "... none of them printed" bash -c "! grep -q -e '$token' -e smtp-test-secret \
  -e telegram-test-secret '$work/config-full.log'"
check "... readable by Alertmanager (0644), in the closed secrets directory (0700)" equals \
  "$(stat -c %a "$config") $(stat -c %a "$infra/secrets")" "644 700"
check "an alert URL that is not an ntfy topic is refused" fails logged config-bad-url \
  monitoring --config-only --alert-url https://ntfy.test/
check "no alert channel at all is refused" fails logged config-none monitoring --config-only
check "e-mail without an SMTP server is refused" fails logged config-bad-mail \
  monitoring --config-only --alert-url https://ntfy.test/lifeos --email-to owner@example.org
check "... the last good configuration kept" has 'telegram_configs:' "$config"

echo "# 3. Simulated outages, end to end"
check "configuration: stub ntfy server, token, dead man's switch" logged config-e2e \
  monitoring --config-only --alert-url http://ntfy:8080/lifeos-alertes \
  --alert-token-file "$work/ntfy-token" --deadman-url http://ntfy:8080/deadman
# The backups, as the backup scripts report them: the last one 40 hours ago, two originals
# missing, the off-site copy fresh.
(
  # shellcheck source=/dev/null  # the copy of infra/scripts/lib/backup.sh
  . "$infra/scripts/lib/backup.sh"
  now="$(date +%s)"
  metrics="$work/metrics/lifeos_backup.prom"
  metric_set "$metrics" lifeos_backup_last_success_timestamp_seconds $((now - 40 * 3600)) test
  metric_set "$metrics" lifeos_backup_offsite_last_success_timestamp_seconds "$now" test
  metric_set "$metrics" lifeos_backup_missing_originals 2 test
)

# Certificates: a local authority (as mkcert), and a 30-day certificate for lifeos.test.
mkdir -m 755 "$work/certs"
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=LifeOS test authority" \
  -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign \
  -keyout "$work/ca.key" -out "$work/ca.pem" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=lifeos.test" -keyout "$work/certs/privkey.pem" \
  -out "$work/server.csr" 2>/dev/null
openssl x509 -req -in "$work/server.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" \
  -CAcreateserial -days 30 -out "$work/certs/fullchain.pem" \
  -extfile <(printf 'subjectAltName=DNS:lifeos.test\nextendedKeyUsage=serverAuth\n') 2>/dev/null
chmod 644 "$work/certs"/*.pem
install -m 644 "$work/ca.pem" "$infra/blackbox/ca/rootCA.pem"
cp "$infra/traefik/dynamic/tls.yml.example" "$infra/traefik/dynamic/tls.yml"

# LifeOS stand-ins behind the real Traefik (standard mode: it owns the HTTPS port).
docker network create "$prefix-edge" >/dev/null
docker network create "$prefix-proxy" >/dev/null
for service in api:8000 web:3000; do
  docker run -d --name "$prefix-${service%%:*}" --network "$prefix-edge" \
    --network-alias "${service%%:*}" -e WHOAMI_PORT_NUMBER="${service##*:}" "$whoami_image" \
    >/dev/null
done
docker run -d --name "$prefix-traefik" --network "$prefix-edge" --read-only -p 0.0.0.0::443 \
  -e LIFEOS_DOMAIN=lifeos.test \
  -v "$infra/traefik/traefik.yml:/etc/traefik/traefik.yml:ro" \
  -v "$infra/traefik/dynamic:/etc/traefik/dynamic:ro" -v "$work/certs:/certs:ro" \
  "$traefik_image" --configFile=/etc/traefik/traefik.yml >/dev/null

compose up -d ntfy >/dev/null 2>&1
# The probes reach Traefik at LIFEOS_PROBE_ADDRESS (a reverse proxy on another machine).
docker network connect "${prefix}_monitoring" "$prefix-traefik"
address="$(docker inspect --format \
  "{{(index .NetworkSettings.Networks \"${prefix}_monitoring\").IPAddress}}" "$prefix-traefik")"
echo "LIFEOS_PROBE_ADDRESS=$address" >> "$infra/.env"
check "Prometheus, Alertmanager, node exporter and blackbox exporter start healthy" logged up \
  compose up -d --wait --wait-timeout 180 prometheus alertmanager node-exporter blackbox-exporter
# An alert whose labels carry personal data: none of it may reach the notification.
compose exec -T alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 alert add \
  alertname=DonneesDeTest severity=warning email=owner@example.org "document=Relevé de compte" \
  --annotation=summary="Alerte de test." >/dev/null

public_up() { [[ "$(value 'sum(up{job="public"})')" == 3 ]]; }
check "the public probes are collected (targets written from LIFEOS_DOMAIN)" \
  wait_until 120 public_up
check "... LifeOS reachable through LIFEOS_PROBE_ADDRESS (API and web pages)" equals \
  "$(value 'sum(probe_success{job="public", module="https_reachable"})')" 2
check "... certificate of the local authority trusted (blackbox/ca/)" equals \
  "$(value 'probe_success{job="public", module="https_verified"}')" 1
days="$(value 'floor((probe_ssl_earliest_cert_expiry{module="https_reachable", target="api"} - time()) / 86400)')"
check "... certificate expiry measured ($days days left)" equals "$days" 29
check "node exporter measures the root file system (system mounts excluded)" equals \
  "$(value 'count(node_filesystem_size_bytes{job="node", mountpoint="/"})') $(value \
  'count(node_filesystem_size_bytes{job="node", mountpoint=~"/(proc|sys|dev|run|textfile)(/.*)?"})')" \
  "1 none"
check "... and reads the backup metrics" equals \
  "$(value 'lifeos_backup_missing_originals{job="node"}')" 2

all_notified() {
  notified "LifeOS : Worker arrêté" && notified "LifeOS : Sauvegarde trop ancienne" \
    && notified "LifeOS : Originaux absents de la sauvegarde" && notified "LifeOS : DonneesDeTest" \
    && grep -q '"path": "/deadman"' "$requests"
}
check "the alerts reach the stub ntfy server" wait_until 240 all_notified
worker="$(notification "LifeOS : Worker arrêté")"
check "... worker stopped: what is wrong, then what to do" equals "$(field "$worker" message)" \
  "Le worker ne répond plus : documents, dictées, rappels et tâches de ménage ne sont plus traités.
Que faire : docker compose ps worker ; docker compose logs --tail 100 worker."
check "... on the backups' topic, urgent (critical)" equals \
  "$(field "$worker" topic) $(field "$worker" priority) $(field "$worker" tags)" \
  "lifeos-alertes 5 lifeos,critical"
backup="$(notification "LifeOS : Sauvegarde trop ancienne")"
check "... backup too old: its age" has 'La dernière sauvegarde réussie date de 1d 16h' \
  <(field "$backup" message)
originals="$(notification "LifeOS : Originaux absents de la sauvegarde")"
check "... missing originals: how many, high priority (warning)" equals \
  "$(field "$originals" priority) $(field "$originals" message | head -1)" \
  "4 2 original(aux) de document référencé(s) par la base manque(nt) dans la dernière sauvegarde."
plain_texts() {
  jq -e -s 'length > 0 and all(.[]; (.title | type) == "string" and (.message | type) == "string"
    and (.title | startswith("LifeOS")) and (.message | test("^[\\[{\"-]") | not))' \
    <(messages) >/dev/null
}
check "... titles and messages are plain text (nothing left of JSON or YAML)" plain_texts
check "... every notification carries the topic's token" equals \
  "$(jq -r 'select(.path == "/") | .authorization' "$requests" | sort -u)" "Bearer $token"
check "the dead man's switch is pinged (Watchdog)" equals \
  "$(jq -r 'select(.path == "/deadman") | .body | fromjson | .status' "$requests" | sort -u)" firing

echo "# Traefik stops"
docker stop "$prefix-traefik" >/dev/null
check "LifeOS unreachable: alert within minutes" \
  wait_until 240 notified "LifeOS : LifeOS injoignable"
unreachable="$(notification "LifeOS : LifeOS injoignable")"
check "... naming the part that is down, and what to do" has \
  "Que faire : Sur la VM, docker compose ps puis docker compose logs" <(field "$unreachable" message)
check "no notification holds personal data, a label or a link" bash -c "! grep -q -i \
  -e 'owner@example.org' -e 'Relevé' -e 'instance' -e 'http://' -e ':9101' -e ':9100' \
  -e 'lifeos.test' '$requests'"

echo "# 4. Public probes through the host's port (host-gateway), both modes"
sed -i '/^LIFEOS_PROBE_ADDRESS=/d' "$infra/.env"
compose up -d --wait --wait-timeout 120 blackbox-exporter >/dev/null 2>&1
docker start "$prefix-traefik" >/dev/null
standard="https://lifeos.test:$(docker port "$prefix-traefik" 443/tcp | head -1 | sed 's/.*://')"
standard_up() { [[ "$(probe https_reachable "$standard/healthz")" == 1 ]]; }
check "standard mode (Traefik on the port): LifeOS reachable" wait_until 60 standard_up
check "... certificate trusted" equals "$(probe https_verified "$standard/healthz")" 1

# Behind a reverse proxy: TLS ends at an nginx sending the headers of Nginx Proxy Manager.
docker run -d --name "$prefix-proxied" --network "$prefix-edge" --read-only \
  -e LIFEOS_DOMAIN=lifeos.test -e LIFEOS_BEHIND_PROXY=true \
  -v "$infra/traefik/traefik.proxy.yml:/etc/traefik/traefik.proxy.yml:ro" \
  -v "$infra/traefik/dynamic:/etc/traefik/dynamic:ro" \
  "$traefik_image" --configFile=/etc/traefik/traefik.proxy.yml >/dev/null
docker network connect --alias lifeos-traefik "$prefix-proxy" "$prefix-proxied"
cat > "$work/npm.conf" <<'EOF'
server {
  listen 443 ssl;
  ssl_certificate     /certs/fullchain.pem;
  ssl_certificate_key /certs/privkey.pem;
  location / {
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Scheme https;
    proxy_set_header X-Forwarded-Proto  https;
    proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
    proxy_set_header X-Real-IP          $remote_addr;
    proxy_pass       http://lifeos-traefik:80;
  }
}
EOF
chmod 644 "$work/npm.conf"
docker run -d --name "$prefix-npm" --network "$prefix-proxy" -p 0.0.0.0::443 \
  -v "$work/npm.conf:/etc/nginx/conf.d/default.conf:ro" -v "$work/certs:/certs:ro" \
  "$nginx_image" >/dev/null
proxied="https://lifeos.test:$(docker port "$prefix-npm" 443/tcp | head -1 | sed 's/.*://')"
proxied_up() { [[ "$(probe https_reachable "$proxied/healthz")" == 1 ]]; }
check "behind a reverse proxy: LifeOS reachable" wait_until 60 proxied_up
check "... web pages too" equals "$(probe https_reachable "$proxied/login")" 1
check "... certificate trusted" equals "$(probe https_verified "$proxied/healthz")" 1

rm "$infra/blackbox/ca/rootCA.pem"
compose restart blackbox-exporter >/dev/null 2>&1
compose up -d --wait --wait-timeout 120 blackbox-exporter >/dev/null 2>&1
check "without its local authority: the certificate is refused" equals \
  "$(probe https_verified "$proxied/healthz")" 0
check "... while LifeOS is still seen up" equals "$(probe https_reachable "$proxied/healthz")" 1

if [[ "$failures" -gt 0 || -n "${LIFEOS_TEST_VERBOSE:-}" ]]; then
  for log in "$work"/*.log; do echo "== ${log##*/}"; tail -30 "$log"; done
  [[ -f "$requests" ]] && { echo "== notifications"; cat "$requests"; }
fi
if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) failed."
  exit 1
fi
echo "All monitoring checks passed."
