#!/usr/bin/env bash
# Alerts (docs/exploitation.md §5): writes the Alertmanager configuration, enables the
# `monitoring` profile in infra/.env and starts Prometheus, Alertmanager, node exporter and
# blackbox exporter. Idempotent: run it again after changing a setting.
#
#   sudo infra/scripts/install-monitoring.sh [--test]
#
# The alert channel is the one of the backups (/etc/lifeos/backup.env, install-backups.sh):
# LIFEOS_ALERT_URL, an ntfy topic URL (https://ntfy.sh/<long random topic>, or your own ntfy
# server), LIFEOS_ALERT_TOKEN if the topic is protected, and LIFEOS_MONITORING_HEALTHCHECK_URL.
# Options (they override that file):
#   --alert-url URL              ntfy topic URL
#   --alert-token-file FILE      ntfy access token, read from FILE (never on the command line)
#   --deadman-url URL            dead man's switch on ANOTHER machine (healthchecks.io style),
#                                pinged every 5 minutes while the monitoring works
#   --email-to ADDR --smtp-host HOST:PORT --smtp-from ADDR [--smtp-user USER]
#   [--smtp-password-file FILE]  also by e-mail (optional; STARTTLS required)
#   --telegram-chat-id ID --telegram-token-file FILE
#                                also by Telegram (optional)
#   --config-only                write the configuration, start nothing
#   --test                       then send a test alert through Alertmanager
#
# The configuration holds the channel's secrets: infra/secrets/alertmanager.yml (mode 0644 in
# the closed infra/secrets/ directory, like the other secrets). Notifications carry the
# alert's title, summary and action only (infra/alertmanager/lifeos.tmpl): no personal data.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/common.sh
. scripts/lib/common.sh

alert_url="" token_file="" deadman_url="" email_to="" smtp_host="" smtp_from="" smtp_user=""
smtp_password_file="" telegram_chat="" telegram_token_file="" config_only=0 test_alert=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --alert-url) alert_url="${2:?--alert-url needs a value}"; shift 2 ;;
    --alert-token-file) token_file="${2:?--alert-token-file needs a value}"; shift 2 ;;
    --deadman-url) deadman_url="${2:?--deadman-url needs a value}"; shift 2 ;;
    --email-to) email_to="${2:?--email-to needs a value}"; shift 2 ;;
    --smtp-host) smtp_host="${2:?--smtp-host needs a value}"; shift 2 ;;
    --smtp-from) smtp_from="${2:?--smtp-from needs a value}"; shift 2 ;;
    --smtp-user) smtp_user="${2:?--smtp-user needs a value}"; shift 2 ;;
    --smtp-password-file) smtp_password_file="${2:?--smtp-password-file needs a value}"; shift 2 ;;
    --telegram-chat-id) telegram_chat="${2:?--telegram-chat-id needs a value}"; shift 2 ;;
    --telegram-token-file) telegram_token_file="${2:?--telegram-token-file needs a value}"; shift 2 ;;
    --config-only) config_only=1; shift ;;
    --test) test_alert=1; shift ;;
    -h | --help) sed -n '2,27p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done
require_tools docker jq

# The channel of the backups, unless given here (the file is root's: sudo, or the options).
load_backup_env always
alert_url="${alert_url:-${LIFEOS_ALERT_URL:-}}"
deadman_url="${deadman_url:-${LIFEOS_MONITORING_HEALTHCHECK_URL:-}}"
token="${LIFEOS_ALERT_TOKEN:-}"
read_secret() {
  [[ -r "$1" ]] || die "cannot read $1"
  tr -d '\r\n' < "$1"
}
[[ -z "$token_file" ]] || token="$(read_secret "$token_file")"
[[ -n "$alert_url" ]] || die "no alert channel: --alert-url https://ntfy.sh/<topic>, or" \
  "LIFEOS_ALERT_URL in /etc/lifeos/backup.env (install-backups.sh --alert-url; run with sudo)."
# ntfy publishes JSON on the server's root, the topic in the body.
alert_url="${alert_url%%[?#]*}"
topic="${alert_url##*/}"
server="${alert_url%/*}/"
[[ "$alert_url" =~ ^https?://[^/]+(/.*)?/[A-Za-z0-9_-]{1,64}$ ]] \
  || die "LIFEOS_ALERT_URL must be an ntfy topic URL: https://<server>/<topic>."
if [[ -n "$email_to" && ( -z "$smtp_host" || -z "$smtp_from" ) ]]; then
  die "--email-to needs --smtp-host HOST:PORT and --smtp-from ADDR."
fi
if [[ -n "$telegram_chat" ]]; then
  [[ "$telegram_chat" =~ ^-?[0-9]+$ ]] || die "--telegram-chat-id must be a number."
  [[ -n "$telegram_token_file" ]] || die "--telegram-chat-id needs --telegram-token-file."
fi

step "Alertmanager configuration (infra/secrets/alertmanager.yml)"
config=secrets/alertmanager.yml
umask 022
tmp="$(mktemp secrets/.alertmanager.XXXXXX)"
trap 'rm -f "$tmp"' EXIT
{
  cat <<EOF
# Written by infra/scripts/install-monitoring.sh on $(date -u +%Y-%m-%d): run it again rather
# than editing this file. It holds the alert channel's secrets (never commit it).
global:
  resolve_timeout: 5m
templates:
  - /etc/alertmanager/lifeos.tmpl
route:
  receiver: owner
  group_by: [alertname]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - matchers: ['alertname="Watchdog"']
      receiver: $([[ -n "$deadman_url" ]] && echo deadman || echo none)
      group_wait: 0s
      group_interval: 1m
      repeat_interval: 5m
    - matchers: ['severity="warning"']
      receiver: owner
      repeat_interval: 12h
inhibit_rules:
  # The critical level of an alert silences its warning level.
  - source_matchers: ['severity="critical"']
    target_matchers: ['severity="warning"']
    equal: [alertname]
  # A stopped worker explains the waiting documents and dictations.
  - source_matchers: ['alertname="WorkerArrete"']
    target_matchers: ['alertname=~"DocumentsEnAttente|DicteesEnAttente|FileDeTachesEngorgee"']
receivers:
  - name: none
  - name: owner
    # ntfy (JSON publication): https://docs.ntfy.sh/publish/#publish-as-json
    webhook_configs:
      - url: $(yaml_string "$server")
        send_resolved: true
        max_alerts: 20
EOF
  if [[ -n "$token" ]]; then
    cat <<EOF
        http_config:
          authorization:
            credentials: $(yaml_string "$token")
EOF
  fi
  cat <<EOF
        payload:
          topic: $(yaml_string "$topic")
          title: '{{ template "lifeos.title.ntfy" . }}'
          message: '{{ template "lifeos.message.ntfy" . }}'
          priority: '{{ template "lifeos.priority" . }}'
          tags: [lifeos, '{{ or .CommonLabels.severity "info" }}']
EOF
  if [[ -n "$email_to" ]]; then
    cat <<EOF
    email_configs:
      - to: $(yaml_string "$email_to")
        from: $(yaml_string "$smtp_from")
        smarthost: $(yaml_string "$smtp_host")
        require_tls: true
        send_resolved: true
        headers:
          Subject: '{{ template "lifeos.title" . }}'
        text: '{{ template "lifeos.message" . }}'
        html: ''
EOF
    [[ -z "$smtp_user" ]] || echo "        auth_username: $(yaml_string "$smtp_user")"
    if [[ -n "$smtp_password_file" ]]; then
      echo "        auth_password: $(yaml_string "$(read_secret "$smtp_password_file")")"
    fi
  fi
  if [[ -n "$telegram_chat" ]]; then
    cat <<EOF
    telegram_configs:
      - bot_token: $(yaml_string "$(read_secret "$telegram_token_file")")
        chat_id: $telegram_chat
        send_resolved: true
        parse_mode: ''
        message: '{{ template "lifeos.title" . }}

          {{ template "lifeos.message" . }}'
EOF
  fi
  if [[ -n "$deadman_url" ]]; then
    cat <<EOF
  # Dead man's switch: the Watchdog alert, every 5 minutes; nothing else is sent there.
  - name: deadman
    webhook_configs:
      - url: $(yaml_string "$deadman_url")
        send_resolved: false
        max_alerts: 1
        payload:
          status: '{{ .Status }}'
EOF
  fi
} > "$tmp"
chmod 644 "$tmp"  # read by amtool as `nobody`

# Checked by amtool before it replaces anything (same image as the service).
image="$(docker compose --profile monitoring config --format json | jq -r '.services.alertmanager.image')"
docker run --rm --network none -v "$PWD/$tmp:/check/alertmanager.yml:ro" \
  -v "$PWD/alertmanager/lifeos.tmpl:/etc/alertmanager/lifeos.tmpl:ro" \
  --entrypoint amtool "$image" check-config /check/alertmanager.yml >/dev/null \
  || die "the generated configuration is invalid (amtool check-config)."
# Written in place: the running container sees the new content (same file).
cat "$tmp" > "$config"
chmod 644 "$config"
echo "channel: ntfy topic on ${server}$([[ -n "$token" ]] && echo ' (token)')$([[ -n "$email_to" ]] \
  && echo ', e-mail')$([[ -n "$telegram_chat" ]] && echo ', Telegram')$([[ -n "$deadman_url" ]] \
  && echo "; dead man's switch")"
[[ -n "$deadman_url" ]] || echo "Note: no dead man's switch (--deadman-url): nothing warns you" \
  "if this whole machine stops."
[[ "$config_only" -eq 0 ]] || exit 0

step "Compose profile and metrics"
profiles="$(env_value COMPOSE_PROFILES)"
if [[ ",$profiles," != *",monitoring,"* ]]; then
  set_env COMPOSE_PROFILES "${profiles:+$profiles,}monitoring"
  echo "infra/.env: COMPOSE_PROFILES=${profiles:+$profiles,}monitoring"
fi
# The node exporter reads the backup metrics where the backup scripts write them.
if [[ "$(metrics_dir)" != /var/lib/lifeos/metrics || -n "$(env_value LIFEOS_METRICS_DIR)" ]]; then
  set_env LIFEOS_METRICS_DIR "$(metrics_dir)"
fi
if [[ ! -d "$(metrics_dir)" ]]; then
  echo "Warning: $(metrics_dir) does not exist yet: the backup metrics appear once"
  echo "install-backups.sh has run (alert SauvegardeAbsente until then)."
fi

step "Start"
docker compose up -d --wait --wait-timeout 180 prometheus alertmanager node-exporter \
  blackbox-exporter
# A running Alertmanager reloads its configuration (no restart, silences kept).
docker compose kill -s HUP alertmanager >/dev/null
docker compose --profile monitoring ps prometheus alertmanager node-exporter blackbox-exporter

if [[ "$test_alert" -eq 1 ]]; then
  step "Test alert"
  sleep 2
  docker compose exec -T alertmanager amtool --alertmanager.url=http://127.0.0.1:9093 \
    alert add alertname=TestDesAlertes severity=warning \
    --annotation=title="Test des alertes" \
    --annotation=summary="Alerte de test de LifeOS : le canal fonctionne." \
    --end="$(date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%SZ)"
  echo "Test alert sent: it reaches the channel within a minute (then « résolu »)."
fi

step "Done"
cat <<EOF
Prometheus: http://127.0.0.1:9090 on this machine (SSH tunnel), alerts under "Alerts".
Simulated outages to try once (docs/exploitation.md §5): docker compose stop worker, then
docker compose start worker after the alert.
EOF
