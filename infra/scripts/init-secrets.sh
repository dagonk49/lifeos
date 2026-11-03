#!/usr/bin/env bash
# Creates missing secret files with random passwords. Never overwrites an existing file.
#
# Compose bind-mounts file secrets with their host permissions, and the containers run as
# non-root users: files are world-readable (0644) but the directory is closed (0700), so no
# other user of the host can reach them.
set -euo pipefail
dir="$(cd "$(dirname "$0")/../secrets" && pwd)"
chmod 700 "$dir"
umask 022

new_secret() {
  local file="$dir/$1"
  if [[ -e "$file" ]]; then
    echo "exists:  $1"
  else
    head -c 32 /dev/urandom | base64 | tr -d '/+=\n' > "$file"
    echo "created: $1"
  fi
}

new_secret postgres_superuser_password
new_secret lifeos_database_password
# AES-256-GCM key of the document originals (ADR 0008): 32 random bytes, base64. Keep a
# copy OUTSIDE the backups: without it, the originals cannot be decrypted.
if [[ -e "$dir/lifeos_documents_key" ]]; then
  echo "exists:  lifeos_documents_key"
else
  head -c 32 /dev/urandom | base64 | tr -d '\n' > "$dir/lifeos_documents_key"
  echo "created: lifeos_documents_key (save a copy outside the backups)"
fi
if [[ ! -e "$dir/lifeos_gemini_api_key" ]]; then
  : > "$dir/lifeos_gemini_api_key"
  echo "created: lifeos_gemini_api_key (empty: paste your key there to enable Gemini)"
fi
if [[ ! -e "$dir/lifeos_hf_token" ]]; then
  : > "$dir/lifeos_hf_token"
  echo "created: lifeos_hf_token (empty: only needed if the embedding model is gated)"
fi
# AES-256-GCM key of the OAuth tokens of the connected accounts (ADR 0012). Losing it only
# forces reconnecting the accounts: never store it with the backups.
if [[ -e "$dir/lifeos_connectors_key" ]]; then
  echo "exists:  lifeos_connectors_key"
else
  head -c 32 /dev/urandom | base64 | tr -d '\n' > "$dir/lifeos_connectors_key"
  echo "created: lifeos_connectors_key"
fi
if [[ ! -e "$dir/lifeos_google_client_secret" ]]; then
  : > "$dir/lifeos_google_client_secret"
  echo "created: lifeos_google_client_secret (empty: paste the Google OAuth client secret to connect Google accounts)"
fi
# VAPID key of Web Push (ADR 0019): a P-256 private key, PEM (PKCS#8). Losing it only forces
# every phone to enable notifications again; the worker signs with it, the API publishes its
# public half.
if [[ -e "$dir/lifeos_vapid_private_key" ]]; then
  echo "exists:  lifeos_vapid_private_key"
elif command -v openssl >/dev/null 2>&1; then
  openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt > "$dir/lifeos_vapid_private_key"
  echo "created: lifeos_vapid_private_key"
else
  # A missing file is created by the next run; compose needs it even with push disabled.
  : > "$dir/lifeos_vapid_private_key"
  echo "created: lifeos_vapid_private_key (empty: install openssl, delete it and run this script again before LIFEOS_PUSH_PROVIDER=webpush)" >&2
fi
