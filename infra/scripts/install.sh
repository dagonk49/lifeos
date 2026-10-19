#!/usr/bin/env bash
# First installation of LifeOS on the VM (idempotent: safe to run again).
#
#   infra/scripts/install.sh --domain lifeos.example.org --email moi@example.org --name "Moi"
#
# Options:
#   --domain NAME    host name of LifeOS (HTTPS, passkeys: never change it afterwards)
#   --email ADDR     owner's e-mail (account created once)
#   --name NAME      owner's display name
#   --gpu            transcription on the NVIDIA GPU (compose.gpu.yaml, see docs/exploitation.md)
#   --behind-proxy   an existing reverse proxy (e.g. Nginx Proxy Manager) owns ports 80/443 and
#                    terminates TLS: Traefik publishes no port (compose.proxy.yaml)
#   --proxy-network NAME
#                    Docker network shared with that proxy (default: lifeos-proxy, created if
#                    needed); implies --behind-proxy
#   --no-models      do not download the embedding (1.2 GB) and speech (1.6 GB) models now
#   --yes            no question: keep the Gemini key file as it is
#
# Steps: checks, secrets, .env, build and start, model downloads, OCR check, owner account
# (prints the single-use enrollment link of the first passkey).
set -euo pipefail
script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$script")/.."

domain="" email="" name="" gpu=0 models=1 assume_yes=0 behind_proxy=0 proxy_network=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) domain="${2:?--domain needs a value}"; shift 2 ;;
    --email) email="${2:?--email needs a value}"; shift 2 ;;
    --name) name="${2:?--name needs a value}"; shift 2 ;;
    --gpu) gpu=1; shift ;;
    --behind-proxy) behind_proxy=1; shift ;;
    --proxy-network) proxy_network="${2:?--proxy-network needs a value}"; behind_proxy=1; shift 2 ;;
    --no-models) models=0; shift ;;
    --yes) assume_yes=1; shift ;;
    -h|--help) sed -n '2,20p' "$script"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$1"; }
fail() { echo "Error: $1" >&2; exit 1; }

step "Checks"
command -v docker >/dev/null || fail "Docker is not installed (docs/exploitation.md §1)."
docker compose version >/dev/null 2>&1 || fail "the Docker Compose plugin is missing."
docker info >/dev/null 2>&1 || fail "Docker is not running, or this user may not use it."
if ! grep -qw avx2 /proc/cpuinfo 2>/dev/null; then
  echo "Warning: no AVX2 on this CPU. In Proxmox, set the VM CPU type to 'host' (embeddings and"
  echo "transcription are much slower without it)."
fi
memory_gb=$(awk '/MemTotal/ {printf "%d", $2 / 1048576}' /proc/meminfo 2>/dev/null || echo 0)
if [[ "$memory_gb" -lt 8 ]]; then
  echo "Warning: ${memory_gb} GB of RAM detected; 10 GB are recommended (docs/exploitation.md §7)."
fi
if [[ "$behind_proxy" -eq 0 ]] && ! grep -qs '^COMPOSE_FILE=.*compose\.proxy\.yaml' .env \
  && [[ -z "$(docker compose ps -q traefik 2>/dev/null)" ]] && command -v ss >/dev/null \
  && [[ -n "$(ss -Hltn '( sport = :80 or sport = :443 )' 2>/dev/null)" ]]; then
  echo "Warning: ports 80/443 are already in use on this machine. If a reverse proxy (e.g. Nginx"
  echo "Proxy Manager) serves other sites there, run again with --behind-proxy."
fi
if [[ "$gpu" -eq 1 ]]; then
  docker info --format '{{json .Runtimes}}' | grep -q nvidia \
    || fail "--gpu: the NVIDIA Container Toolkit is not configured (docs/exploitation.md §7)."
fi

step "Secrets"
./scripts/init-secrets.sh
gemini_key=secrets/lifeos_gemini_api_key
if [[ ! -s "$gemini_key" && "$assume_yes" -eq 0 && -t 0 ]]; then
  echo "Gemini API key (project WITHOUT billing account, docs/guide-gemini.md)."
  read -r -s -p "Paste it, or press Enter to configure it later: " key
  echo
  if [[ -n "$key" ]]; then
    printf '%s' "$key" > "$gemini_key"
    echo "saved: $gemini_key"
  fi
fi

step "Configuration (.env)"
if [[ ! -e .env ]]; then
  cp .env.example .env
  echo "created: infra/.env from .env.example"
fi
set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}
if [[ -n "$domain" ]]; then
  set_env LIFEOS_DOMAIN "$domain"
  set_env LIFEOS_PUBLIC_ORIGIN "https://$domain"
  set_env LIFEOS_WEBAUTHN_RP_ID "$domain"
fi
current_domain="$(sed -n 's/^LIFEOS_DOMAIN=//p' .env)"
if [[ -z "$current_domain" || "$current_domain" == "lifeos.example.lan" ]]; then
  fail "set the domain: --domain lifeos.example.org (or edit LIFEOS_DOMAIN in infra/.env)."
fi
if [[ -s "$gemini_key" ]] && grep -q '^LIFEOS_LLM_PROVIDER=disabled' .env; then
  set_env LIFEOS_LLM_PROVIDER gemini
  echo "Gemini enabled (free tier, daily caps: see LIFEOS_LLM_DAILY_REQUESTS_* in .env)."
fi
# Overlays (GPU, reverse proxy) are kept in COMPOSE_FILE: every later `docker compose`
# command (updates, backups) then uses them too. Running the script again only adds overlays.
compose_files="$(sed -n 's/^COMPOSE_FILE=//p' .env)"
compose_files="${compose_files:-compose.yaml}"
add_overlay() { [[ ":$compose_files:" == *":$1:"* ]] || compose_files="$compose_files:$1"; }
[[ "$gpu" -eq 1 ]] && add_overlay compose.gpu.yaml
[[ "$behind_proxy" -eq 1 ]] && add_overlay compose.proxy.yaml
[[ "$compose_files" != "compose.yaml" ]] && set_env COMPOSE_FILE "$compose_files"
[[ ":$compose_files:" == *":compose.proxy.yaml:"* ]] && behind_proxy=1
echo "domain: $current_domain"

if [[ "$behind_proxy" -eq 1 ]]; then
  step "Reverse proxy network"
  [[ -n "$proxy_network" ]] && set_env LIFEOS_PROXY_NETWORK "$proxy_network"
  proxy_network="$(sed -n 's/^LIFEOS_PROXY_NETWORK=//p' .env)"
  proxy_network="${proxy_network:-lifeos-proxy}"
  if ! docker network inspect "$proxy_network" >/dev/null 2>&1; then
    docker network create "$proxy_network" >/dev/null
    echo "created Docker network: $proxy_network"
  fi
  npm_container="$(docker ps --format '{{.Names}}\t{{.Image}}' \
    | awk -F '\t' '$2 ~ /nginx-proxy-manager/ { print $1; exit }')"
  if [[ -z "$npm_container" ]]; then
    echo "No Nginx Proxy Manager container found: connect your proxy to the network yourself:"
    echo "  docker network connect $proxy_network <proxy-container>"
  elif docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' \
    "$npm_container" | tr ' ' '\n' | grep -qx "$proxy_network"; then
    echo "$npm_container is already on $proxy_network."
  else
    docker network connect "$proxy_network" "$npm_container"
    echo "connected $npm_container to $proxy_network."
    echo "To keep it when that container is recreated, add the network to its compose file"
    echo "(docs/installation.md, « Derrière un reverse proxy existant »)."
  fi
fi

step "Build and start (first build: several minutes)"
docker compose build
docker compose up -d
# Waits for the services that report their health. The worker and the scheduler report
# none (healthcheck disabled): Docker Compose 2.x refuses to wait for them.
docker compose up -d --wait --wait-timeout 300 api web

if [[ "$models" -eq 1 ]]; then
  step "Models (downloaded once, then everything runs offline)"
  docker compose --profile setup run --rm ml-download
  docker compose --profile setup run --rm stt-download
  docker compose restart ml stt
fi

step "Local OCR check"
docker compose exec -T ml lifeos-ml selftest || echo "Warning: OCR check failed (see docker compose logs ml)."

step "Owner account"
if [[ -n "$email" ]]; then
  if docker compose exec -T api lifeos-admin create-owner --email "$email" --name "${name:-$email}"; then
    echo "Open the link above in the browser of the device that will hold your first passkey."
  else
    echo "Account not created (it may already exist). New enrollment link:"
    echo "  docker compose exec api lifeos-admin enrollment-token --email $email"
  fi
else
  echo "Create it with:"
  echo "  docker compose exec api lifeos-admin create-owner --email you@example.org --name \"You\""
fi

step "Done"
if [[ "$behind_proxy" -eq 1 ]]; then
  cat <<EOF
In your reverse proxy, add a proxy host (Nginx Proxy Manager: Hosts > Proxy Hosts > Add):
- Domain: $current_domain ; Scheme: http ; Forward host: lifeos-traefik ; Port: 80
- SSL: request a Let's Encrypt certificate, enable "Force SSL" and "HTTP/2".
EOF
else
  echo "TLS certificate: docs/exploitation.md §2 (without it, Traefik serves a self-signed one)."
fi
cat <<EOF
LifeOS runs on https://$current_domain
- Backups (nightly, off-site copy, restore tests, alerts on failure), docs/exploitation.md §4:
    sudo infra/scripts/install-backups.sh --repository <restic repository> \\
      --copy-repository <off-site repository> --alert-url <ntfy topic URL>
- Then put the recovery sheet (documents key, restic passwords) in your password manager:
    sudo infra/scripts/export-keys.sh
EOF
