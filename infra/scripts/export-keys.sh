#!/usr/bin/env bash
# Recovery sheet: what a restore on a new machine needs and the backups deliberately do not
# hold. Printed ON THE TERMINAL ONLY: copy it into your password manager, never into a file of
# this server (docs/restauration.md).
#
#   sudo infra/scripts/export-keys.sh            the sheet (refused if the output is not a terminal)
#   sudo infra/scripts/export-keys.sh --check    no secret shown: is this server's documents key
#                                                the one of the latest backup, in each repository?
#   sudo infra/scripts/export-keys.sh --verify   paste your saved copies (hidden): do they match?
#
# The sheet: lifeos_documents_key and its fingerprint, the restic passwords, the backup settings
# (/etc/lifeos/backup.env: repositories and their credentials, alerts), the instance settings
# (domain and WebAuthn RP ID, overlays) and the version. Not on it: the database passwords
# (a restore creates new ones) and the Gemini key (another one can be created in AI Studio).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh
mode="${1:-sheet}"
[[ "$mode" == "-h" || "$mode" == "--help" ]] && { sed -n '2,15p' "$0"; exit 0; }
load_backup_env
require_tools sha256sum
key_file=secrets/lifeos_documents_key
fingerprint="$(documents_key_fingerprint "$key_file")" || die "$key_file is missing or empty."
env_value() { sed -n "s/^$1=//p" .env 2>/dev/null | tail -1; }

case "$mode" in
  --check)
    require_restic_env
    require_tools restic jq
    status=0
    check_repository() {
      local name="$1" pair dockey
      if ! pair="$(pick_pair latest 2>/dev/null)"; then
        echo "$name: no backup found"
        return 1
      fi
      dockey="$(jq -r '.dockey // empty' <<<"$pair")"
      if [[ "$dockey" == "$fingerprint" ]]; then
        echo "$name: OK, the latest backup ($(jq -r .time <<<"$pair")) uses this key"
      elif [[ -z "$dockey" ]]; then
        echo "$name: the latest backup predates key fingerprints (run a backup)"
      else
        echo "$name: MISMATCH: backups made with key $dockey, this server has $fingerprint"
        return 1
      fi
    }
    echo "documents key on this server: fingerprint $fingerprint"
    check_repository "main repository" || status=1
    if [[ -n "${RESTIC_COPY_REPOSITORY:-}" ]]; then
      (use_copy_repository && check_repository "off-site copy") || status=1
    fi
    exit "$status"
    ;;
  --verify)
    [[ -t 0 ]] || die "--verify reads your copies from the keyboard."
    status=0
    read -r -s -p "Saved lifeos_documents_key (hidden, Enter to skip): " saved
    echo
    if [[ -n "$saved" ]]; then
      if [[ "$(printf '%s' "$saved" | tr -d '[:space:]' | sha256sum | cut -c1-12)" == "$fingerprint" ]]; then
        echo "documents key: your copy matches"
      else
        echo "documents key: your copy does NOT match this server's key"
        status=1
      fi
    fi
    for variable in RESTIC_PASSWORD_FILE RESTIC_COPY_PASSWORD_FILE; do
      file="${!variable:-}"
      [[ -n "$file" && -s "$file" ]] || continue
      [[ "$variable" == RESTIC_COPY_PASSWORD_FILE && -z "${RESTIC_COPY_REPOSITORY:-}" ]] && continue
      read -r -s -p "Saved password of $file (hidden, Enter to skip): " saved
      echo
      [[ -n "$saved" ]] || continue
      if [[ "$saved" == "$(head -n 1 "$file")" ]]; then
        echo "$file: your copy matches"
      else
        echo "$file: your copy does NOT match"
        status=1
      fi
    done
    exit "$status"
    ;;
  sheet) ;;
  *) die "unknown option $mode (see --help)" ;;
esac

[[ -t 1 ]] || die "the recovery sheet is printed on a terminal only (no redirection, no pipe)."
version="$(git -C .. -c safe.directory='*' describe --tags --always --dirty 2>/dev/null || true)"
cat <<EOF
==================== LifeOS recovery sheet: $(uname -n), $(date -u '+%Y-%m-%d %H:%M UTC')
Copy it into your password manager (one entry), then clear this terminal (clear).
Restore procedure: docs/restauration.md.

1. Documents key (infra/secrets/lifeos_documents_key): without it, the originals of the
   documents are lost, backups included. Fingerprint: $fingerprint
$(tr -d '[:space:]' < "$key_file")

2. Restic repositories
   main:     ${RESTIC_REPOSITORY:-(not configured: install-backups.sh)}
   password: $( [[ -s "${RESTIC_PASSWORD_FILE:-}" ]] && head -n 1 "$RESTIC_PASSWORD_FILE" || echo "(none)")
EOF
if [[ -n "${RESTIC_COPY_REPOSITORY:-}" ]]; then
  copy_password="${RESTIC_COPY_PASSWORD_FILE:-${RESTIC_PASSWORD_FILE:-}}"
  echo "   off-site: $RESTIC_COPY_REPOSITORY"
  echo "   password: $( [[ -s "$copy_password" ]] && head -n 1 "$copy_password" || echo "(none)")"
fi
backup_env="${LIFEOS_BACKUP_ENV:-/etc/lifeos/backup.env}"
echo
echo "3. Backup settings ($backup_env: credentials, alerts):"
if [[ -r "$backup_env" ]]; then
  grep -Ev '^[[:space:]]*(#|$)' "$backup_env" | grep -v '=$' | sed 's/^/   /'
else
  echo "   (not readable: run with sudo)"
fi
cat <<EOF

4. Instance (infra/.env; also in every backup run, config snapshot)
   LIFEOS_DOMAIN=$(env_value LIFEOS_DOMAIN)
   LIFEOS_WEBAUTHN_RP_ID=$(env_value LIFEOS_WEBAUTHN_RP_ID)   (passkeys only work on it)
   COMPOSE_FILE=$(env_value COMPOSE_FILE)
   LIFEOS_PROXY_NETWORK=$(env_value LIFEOS_PROXY_NETWORK)

5. Version: ${version:-unknown}   ($(sed -n 's/^version = "\(.*\)"$/\1/p' ../backend/pyproject.toml))
   Repository SSH keys, if any (sftp:): keep them too (/root/.ssh).
====================
EOF
