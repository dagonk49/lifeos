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
if [[ ! -e "$dir/lifeos_gemini_api_key" ]]; then
  : > "$dir/lifeos_gemini_api_key"
  echo "created: lifeos_gemini_api_key (empty: paste your key there to enable Gemini)"
fi
