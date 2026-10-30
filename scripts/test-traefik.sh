#!/usr/bin/env bash
# Traefik routing test, with the real Traefik image and the real configuration (CI infra job;
# locally: needs Docker). Two modes:
#   - standard: Traefik owns ports 80/443 (traefik.yml);
#   - behind an existing reverse proxy (compose.proxy.yaml, traefik.proxy.yml), with an nginx
#     that sends the same headers as Nginx Proxy Manager.
# Checks the routing (api / web), the security headers, the client address handed to the API
# and the /v1/auth rate limit: per real client, whatever X-Forwarded-For the client sends.
set -euo pipefail
cd "$(dirname "$0")/../infra"

traefik_image="$(sed -n 's/^ *image: \(traefik:.*\)$/\1/p' compose.yaml | head -1)"
whoami_image=traefik/whoami:v1.11.0
nginx_image=nginx:1.29-alpine
curl_image=curlimages/curl:8.11.1
prefix="lifeos-traefik-test-$$"
http_port=18080
https_port=18443
work="$(mktemp -d)"

cleanup() {
  docker rm -f "$prefix-api" "$prefix-web" "$prefix-proxied" "$prefix-npm" "$prefix-direct" \
    >/dev/null 2>&1 || true
  docker network rm "$prefix-edge" "$prefix-proxy" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

failures=0
# check DESCRIPTION COMMAND [ARGS...]: the command reads the caller's standard input.
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
has() { grep -qi -- "$1"; }
lacks() { ! grep -qi -- "$1"; }
equals() { [[ "$1" == "$2" ]]; }

curl_local() { curl --noproxy '*' -s --max-time 5 "$@"; }
status() { curl_local -o /dev/null -w '%{http_code}' "$@"; }

docker network create "$prefix-edge" >/dev/null
docker network create "$prefix-proxy" >/dev/null
for service in api:8000 web:3000; do
  name="${service%%:*}"
  docker run -d --name "$prefix-$name" --network "$prefix-edge" --network-alias "$name" \
    -e WHOAMI_PORT_NUMBER="${service##*:}" -e WHOAMI_NAME="$name" "$whoami_image" >/dev/null
done

echo "# Behind a reverse proxy (Nginx Proxy Manager headers)"
docker create --name "$prefix-proxied" --network "$prefix-edge" --read-only \
  -e LIFEOS_DOMAIN=lifeos.test -e LIFEOS_BEHIND_PROXY=true \
  -v "$PWD/traefik/traefik.proxy.yml:/etc/traefik/traefik.proxy.yml:ro" \
  -v "$PWD/traefik/dynamic:/etc/traefik/dynamic:ro" \
  "$traefik_image" --configFile=/etc/traefik/traefik.proxy.yml >/dev/null
docker network connect --alias lifeos-traefik "$prefix-proxy" "$prefix-proxied"
docker start "$prefix-proxied" >/dev/null
cat > "$work/npm.conf" <<'EOF'
# Same headers as Nginx Proxy Manager (conf.d/include/proxy.conf).
server {
  listen 80;
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
docker run -d --name "$prefix-npm" --network "$prefix-proxy" -p "127.0.0.1:$http_port:80" \
  -v "$work/npm.conf:/etc/nginx/conf.d/default.conf:ro" "$nginx_image" >/dev/null

proxied="http://127.0.0.1:$http_port"
for _ in $(seq 1 30); do
  [[ "$(curl_local -o /dev/null -w '%{http_code}' -H 'Host: lifeos.test' "$proxied/healthz")" == 200 ]] && break
  sleep 1
done
body="$(curl_local -H 'Host: lifeos.test' -H 'X-Forwarded-For: 6.6.6.6' "$proxied/healthz")"
headers="$(curl_local -D - -o /dev/null -H 'Host: lifeos.test' "$proxied/")"
check "/healthz goes to the API" has '^Name: api' <<<"$body"
check "/ goes to the web client" has '^Name: web' <<<"$(curl_local -H 'Host: lifeos.test' "$proxied/")"
check "another host name is not routed" equals "$(status -H 'Host: other.test' "$proxied/")" 404
check "HSTS is sent (the browser sees HTTPS at the proxy)" \
  has '^strict-transport-security: max-age=31536000' <<<"$headers"
check "frames are denied" has '^x-frame-options: DENY' <<<"$headers"
check "no MIME sniffing" has '^x-content-type-options: nosniff' <<<"$headers"
check "the API gets a client address" has '^X-Real-Ip: ' <<<"$body"
check "... the proxy's one, not the address forged by the client" lacks '^X-Real-Ip: 6.6.6.6' <<<"$body"
check "the proxy's https scheme reaches the API" has '^X-Forwarded-Proto: https' <<<"$body"
codes="$(for i in $(seq 1 40); do
  status -H 'Host: lifeos.test' -H "X-Forwarded-For: 10.9.$i.1" "$proxied/v1/auth/login/options"
  echo
done)"
check "a client changing X-Forwarded-For is still rate-limited on /v1/auth" has '^429$' <<<"$codes"
other="$(docker run --rm --network "$prefix-proxy" "$curl_image" -s -o /dev/null -w '%{http_code}' \
  -H 'Host: lifeos.test' "http://$prefix-npm/v1/auth/login/options")"
check "another client is not affected" equals "$other" 200

echo "# Standard mode (Traefik on 80/443)"
docker run -d --name "$prefix-direct" --network "$prefix-edge" --read-only \
  -p "127.0.0.1:$https_port:443" -e LIFEOS_DOMAIN=lifeos.test \
  -v "$PWD/traefik/traefik.yml:/etc/traefik/traefik.yml:ro" \
  -v "$PWD/traefik/dynamic:/etc/traefik/dynamic:ro" \
  "$traefik_image" --configFile=/etc/traefik/traefik.yml >/dev/null
direct=(--resolve "lifeos.test:$https_port:127.0.0.1" -k)
url="https://lifeos.test:$https_port"
for _ in $(seq 1 30); do
  [[ "$(curl_local "${direct[@]}" -o /dev/null -w '%{http_code}' "$url/healthz")" == 200 ]] && break
  sleep 1
done
body="$(curl_local "${direct[@]}" -H 'X-Real-Ip: 6.6.6.6' -H 'X-Forwarded-For: 6.6.6.6' "$url/healthz")"
check "/healthz goes to the API over HTTPS" has '^Name: api' <<<"$body"
check "HSTS is sent" has '^strict-transport-security: max-age=31536000' \
  <<<"$(curl_local "${direct[@]}" -D - -o /dev/null "$url/")"
check "client-forged X-Real-Ip / X-Forwarded-For are replaced" lacks '6.6.6.6' <<<"$body"
check "HTTP is redirected to HTTPS" equals "$(docker run --rm --network "$prefix-edge" "$curl_image" \
  -s -o /dev/null -w '%{http_code}' -H 'Host: lifeos.test' "http://$prefix-direct/")" 301
codes="$(for i in $(seq 1 40); do
  status "${direct[@]}" -H "X-Forwarded-For: 10.8.$i.1" "$url/v1/auth/login/options"
  echo
done)"
check "/v1/auth is rate-limited" has '^429$' <<<"$codes"

if [[ "$failures" -gt 0 ]]; then
  echo "$failures check(s) failed. Traefik logs:"
  docker logs "$prefix-proxied" 2>&1 | tail -20
  docker logs "$prefix-direct" 2>&1 | tail -20
  exit 1
fi
echo "All Traefik checks passed."
