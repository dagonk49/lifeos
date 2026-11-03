#!/usr/bin/env bash
# Sends a LifeOS alert to LIFEOS_ALERT_URL: an ntfy topic (https://ntfy.sh/<secret topic>, or
# your own ntfy server) or any webhook taking a plain-text POST, with a Title header. The
# message names the unit, the host and the time only: never any personal data.
#
#   infra/scripts/notify.sh UNIT      a failed systemd unit (OnFailure=lifeos-alert@%n.service)
#   infra/scripts/notify.sh --test    check the channel (install-backups.sh does it)
#
# Settings (environment, or /etc/lifeos/backup.env): LIFEOS_ALERT_URL; optional
# LIFEOS_ALERT_TOKEN (ntfy access token, sent as "Authorization: Bearer", never on the
# command line). Exit status 2 when no channel is configured.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { sed -n '2,12p' "$0"; exit 0; }
event="${1:?unit name or --test required (see --help)}"
[[ -n "${LIFEOS_ALERT_URL:-}" ]] || load_backup_env always
if [[ -z "${LIFEOS_ALERT_URL:-}" ]]; then
  echo "LIFEOS_ALERT_URL is not set: alert not sent ($event)." >&2
  exit 2
fi
require_tools curl

host="$(uname -n)"
when="$(date -u '+%Y-%m-%d %H:%M UTC')"
# Headers stay ASCII (HTTP); the body is UTF-8, in French: it is read by the owner.
if [[ "$event" == "--test" ]]; then
  title="LifeOS: test"
  body="Test d'alerte de LifeOS ($host, $when) : le canal fonctionne."
  priority=default
else
  unit="${event//[^A-Za-z0-9@._-]/}"
  title="LifeOS: ${unit%.service} failed"
  body="Échec de $unit sur $host ($when). Détails sur le serveur : journalctl -u $unit"
  priority=high
fi

headers="$(mktemp)"
trap 'rm -f "$headers"' EXIT
chmod 600 "$headers"
{
  printf 'Title: %s\n' "$title"
  printf 'Priority: %s\n' "$priority"
  printf 'Tags: lifeos\n'
  if [[ -n "${LIFEOS_ALERT_TOKEN:-}" ]]; then printf 'Authorization: Bearer %s\n' "$LIFEOS_ALERT_TOKEN"; fi
} > "$headers"
curl -fsS -m 15 --retry 3 -o /dev/null -H @"$headers" -H 'Content-Type: text/plain; charset=utf-8' \
  --data-binary "$body" "$LIFEOS_ALERT_URL"
echo "alert sent: $title"
