#!/usr/bin/env bash
# Restore drill, and disaster recovery, on a NEW machine: from a clone of the repository and
# the recovery sheet (export-keys.sh) to a running, checked LifeOS. Step by step and by hand:
# docs/restauration.md.
#
#   infra/scripts/restore-drill.sh --domain lifeos.example.org --key-file /root/documents-key --yes
#
#   --domain NAME        the original domain (passkeys are bound to it), or a test domain for
#                        a drill next to production (then sign in with --enroll-email)
#   --key-file FILE      the saved lifeos_documents_key (checked against the backup)
#   --run ID             backup run to restore (default: latest)
#   --offsite            from the off-site copy (RESTIC_COPY_REPOSITORY)
#   --behind-proxy       an existing reverse proxy owns ports 80/443 (as install.sh)
#   --models             download the embedding and speech models (about 3 GB) at the end
#   --enroll-email ADDR  print a sign-in link for the owner (other domain, lost devices)
#   --keep-llm           keep the backup's LLM settings (default: disabled, so that a drill
#                        running next to production never uses its quota)
#   --yes                required: any LifeOS data of this machine is replaced
#
# Needs Docker, restic >= 0.17, jq, and the restic settings in the environment (or in
# /etc/lifeos/backup.env). Prints the timings, the age of the restored data, and a line for
# the drill log of docs/restauration.md.
set -euo pipefail
script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$script")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh

domain="" key_file="" selector=latest offsite=0 behind_proxy=0 models=0 enroll_email=""
keep_llm=0 assume_yes=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) domain="${2:?--domain needs a value}"; shift 2 ;;
    --key-file) key_file="${2:?--key-file needs a value}"; shift 2 ;;
    --run) selector="${2:?--run needs a value}"; shift 2 ;;
    --offsite) offsite=1; shift ;;
    --behind-proxy) behind_proxy=1; shift ;;
    --models) models=1; shift ;;
    --enroll-email) enroll_email="${2:?--enroll-email needs a value}"; shift 2 ;;
    --keep-llm) keep_llm=1; shift ;;
    --yes) assume_yes=1; shift ;;
    -h | --help) sed -n '2,23p' "$script"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done
[[ -n "$domain" && -n "$key_file" ]] || die "--domain and --key-file are required (see --help)."
[[ "$assume_yes" -eq 1 ]] || die "refusing to run without --yes (LifeOS data of this machine is replaced)."
load_backup_env
require_restic_env
require_tools restic jq docker flock sha256sum git
started="$(date +%s)"
# From here on, the off-site copy is THE repository (restore.sh included).
[[ "$offsite" -eq 0 ]] || use_copy_repository
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
restore_options=(--yes)

step() { printf '\n== %s\n' "$1"; }
set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

step "Backup to restore"
pair="$(pick_pair "$selector")" || die "no backup run matches '$selector'."
field() { jq -r --arg f "$1" '.[$f] // empty' <<<"$pair"; }
run="$(field run)"
echo "run ${run:-(legacy)}: database of $(field time), LifeOS $(field version), schema $(field rev)"
saved_key="$(documents_key_fingerprint "$key_file")" || die "$key_file is empty or unreadable."
if [[ -n "$(field dockey)" && "$(field dockey)" != "$saved_key" ]]; then
  die "$key_file (fingerprint $saved_key) is not the documents key of this backup" \
    "($(field dockey)). Look for the right one in your password manager."
fi
echo "documents key: fingerprint $saved_key matches the backup"
revision="$(field rev)"
if [[ -n "$revision" ]] && ! grep -qx -- "$revision" \
  < <(sed -n 's/^revision[^=]*= *"\([^"]*\)".*/\1/p' ../backend/migrations/versions/*.py); then
  die "this code does not know the backup's schema ($revision): git checkout $(field version)"
fi
here="$(git -C .. -c safe.directory='*' describe --tags --always --dirty 2>/dev/null || echo unknown)"
[[ "$here" == "$(field version)" ]] \
  || echo "note: this checkout is $here, the backup was made by $(field version) (migrations apply)."

step "Secrets"
install -d -m 700 secrets
if [[ -s secrets/lifeos_documents_key ]]; then
  [[ "$(documents_key_fingerprint secrets/lifeos_documents_key)" == "$saved_key" ]] \
    || die "infra/secrets/lifeos_documents_key exists and is another key: never overwritten."
else
  install -m 644 "$key_file" secrets/lifeos_documents_key
  echo "installed: infra/secrets/lifeos_documents_key"
fi
./scripts/init-secrets.sh

step "Configuration (infra/.env)"
config="$(field config)"
if [[ ! -e .env && -n "$config" ]]; then
  # Same settings as the original instance (overlays, LLM caps, time zone...).
  mkdir "$work/config"
  restic dump --retry-lock 30m "$config" lifeos-config.tar | tar -C "$work/config" -xf -
  install -m 644 "$work/config/.env" .env
  echo "restored: infra/.env of the backup"
  if [[ -f "$work/config/traefik/dynamic/tls.yml" ]]; then
    install -m 644 "$work/config/traefik/dynamic/tls.yml" traefik/dynamic/tls.yml
    echo "restored: traefik/dynamic/tls.yml: put the certificate files back in traefik/certs/"
  fi
fi
if [[ -e .env ]]; then
  original="$(sed -n 's/^LIFEOS_WEBAUTHN_RP_ID=//p' .env)"
  if [[ -n "$original" && "$original" != "$domain" ]]; then
    echo "note: the original domain was $original: passkeys only work there."
    [[ -n "$enroll_email" ]] || echo "      Use --enroll-email to sign in on $domain."
  fi
  files="$(sed -n 's/^COMPOSE_FILE=//p' .env)"
  if [[ ":$files:" == *":compose.gpu.yaml:"* ]] \
    && ! docker info --format '{{json .Runtimes}}' | grep -q nvidia; then
    set_env COMPOSE_FILE "$(tr ':' '\n' <<<"$files" | grep -vx compose.gpu.yaml | paste -sd:)"
    echo "no NVIDIA runtime here: compose.gpu.yaml removed from COMPOSE_FILE"
  fi
  if [[ "$keep_llm" -eq 0 ]]; then
    set_env LIFEOS_LLM_PROVIDER disabled
    echo "LLM disabled for the drill (--keep-llm to keep it)"
  fi
  # The Google OAuth client secret is neither in the backups nor on the recovery sheet: the
  # API would not start without it. Connected accounts stay off until it is put back.
  if grep -q '^LIFEOS_CONNECTORS_PROVIDER=google' .env \
    && [[ ! -s secrets/lifeos_google_client_secret ]]; then
    set_env LIFEOS_CONNECTORS_PROVIDER disabled
    echo "connected accounts disabled: put the Google client secret back (docs/guide-google.md)"
  fi
fi
# A machine that already holds LifeOS data gets a safety dump of it first (restore.sh).
project="$(compose_json 2>/dev/null | jq -r '.name // empty')" || project=""
if ! docker volume inspect "${project:-lifeos}_pgdata" >/dev/null 2>&1; then
  restore_options+=(--no-safety-dump)
fi

step "Install (images, services)"
install_options=(--domain "$domain" --no-models --yes)
[[ "$behind_proxy" -eq 1 ]] && install_options+=(--behind-proxy)
./scripts/install.sh "${install_options[@]}"
if [[ "$keep_llm" -eq 0 ]]; then
  set_env LIFEOS_LLM_PROVIDER disabled
fi
installed="$(date +%s)"

step "Restore (database, originals, migrations, check)"
status=0
./scripts/restore.sh "${run:-$(field db)}" "${restore_options[@]}" || status=$?
# 2: restored, but some originals are missing (listed above): the drill goes on.
[[ "$status" -eq 0 || "$status" -eq 2 ]] || die "restore failed (see above)."
restored="$(date +%s)"

if [[ "$models" -eq 1 ]]; then
  step "Models"
  docker compose --profile setup run --rm ml-download
  docker compose --profile setup run --rm stt-download
  docker compose restart ml stt
fi
if [[ -n "$enroll_email" ]]; then
  step "Owner access"
  docker compose up -d --wait api >/dev/null
  docker compose exec -T api lifeos-admin enrollment-token --email "$enroll_email"
fi

finished="$(date +%s)"
minutes() { echo "$(( ($2 - $1) / 60 )) min"; }
age="$(( (restored - $(date -d "$(field time)" +%s)) / 3600 )) h"
result="OK"
[[ "$status" -eq 2 ]] && result="originaux manquants"
cat <<EOF

== Drill result
install: $(minutes "$started" "$installed"), restore and check: $(minutes "$installed" "$restored"), total: $(minutes "$started" "$finished")
data age: $age (backup of $(field time))
Check by hand in the browser (https://$domain): sign-in, the tasks, this month's finance
totals, a search, opening a restored original (docs/restauration.md, step 9).
Line for the drill log (docs/restauration.md, total time as RTO):
| $(date +%Y-%m-%d) | ${run:-$(field db_short)} | $(minutes "$started" "$finished") | $age | $result | |
EOF
