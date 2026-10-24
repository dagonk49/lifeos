#!/usr/bin/env bash
# Automated backups (idempotent: run it again after changing a setting). Installs and starts
# the systemd timers of infra/systemd: nightly backup with off-site copy, weekly restore test,
# monthly restore test from the off-site copy, monthly read-back of the data; any failure
# sends an alert. Then runs a first backup and restore tests, unless --no-first-run.
#
#   sudo infra/scripts/install-backups.sh --repository sftp:backup@nas.lan:/srv/restic/lifeos \
#     --copy-repository b2:my-bucket:lifeos --alert-url https://ntfy.sh/<long random topic> \
#     --healthcheck-url https://hc-ping.com/<uuid>
#
# Options (each one sets its line of /etc/lifeos/backup.env, created from
# infra/backup.env.example, mode 0600; repository credentials such as B2_ACCOUNT_ID go there
# by hand):
#   --repository URL        main restic repository (any restic backend)
#   --copy-repository URL   off-site copy (recommended unless the main one is off-site)
#   --alert-url URL         ntfy topic or webhook for failures
#   --healthcheck-url URL   dead man's switch (healthchecks.io style) for the nightly backup
#   --no-first-run          do not run a backup and the restore tests now
#
# Restic passwords are generated in /etc/lifeos when missing, repositories initialised when
# new. Afterwards: copy the recovery sheet (sudo infra/scripts/export-keys.sh) into your
# password manager.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh

first_run=1
declare -A settings=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repository) settings[RESTIC_REPOSITORY]="${2:?--repository needs a value}"; shift 2 ;;
    --copy-repository) settings[RESTIC_COPY_REPOSITORY]="${2:?--copy-repository needs a value}"; shift 2 ;;
    --alert-url) settings[LIFEOS_ALERT_URL]="${2:?--alert-url needs a value}"; shift 2 ;;
    --healthcheck-url) settings[LIFEOS_HEALTHCHECK_URL]="${2:?--healthcheck-url needs a value}"; shift 2 ;;
    --no-first-run) first_run=0; shift ;;
    -h | --help) sed -n '2,24p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done
[[ "$(id -u)" -eq 0 ]] || die "run it as root (sudo): it installs systemd units and /etc/lifeos."
require_tools restic jq curl flock docker sha256sum systemctl
root="$(cd .. && pwd)"
env_file="${LIFEOS_BACKUP_ENV:-/etc/lifeos/backup.env}"

step() { printf '\n== %s\n' "$1"; }

step "Settings ($env_file)"
install -d -m 700 "$(dirname "$env_file")"
if [[ ! -e "$env_file" ]]; then
  install -m 600 backup.env.example "$env_file"
  echo "created: $env_file"
fi
# set_conf KEY VALUE: replaces the KEY line (commented or not), or appends it.
set_conf() {
  local tmp
  tmp="$(mktemp "$env_file.XXXXXX")"
  awk -v k="$1" -v v="$2" '
    !done && ($0 ~ "^#?" k "=") { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }' "$env_file" > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$env_file"
}
for key in "${!settings[@]}"; do
  set_conf "$key" "${settings[$key]}"
  echo "set: $key"
done
chmod 600 "$env_file"
LIFEOS_BACKUP_ENV="$env_file" load_backup_env always
[[ -n "${RESTIC_REPOSITORY:-}" ]] || die "no repository: --repository URL (or RESTIC_REPOSITORY in $env_file)."
: "${RESTIC_PASSWORD_FILE:=/etc/lifeos/restic-password}"
export RESTIC_PASSWORD_FILE

# open_or_init NAME PASSWORD_FILE [restic init options]: the repository of RESTIC_REPOSITORY.
open_or_init() {
  local name="$1" password="$2"
  shift 2
  if [[ ! -s "$password" ]]; then
    (umask 077 && head -c 32 /dev/urandom | base64 > "$password")
    echo "$name: new password in $password (it goes on the recovery sheet)"
  fi
  if RESTIC_PASSWORD_FILE="$password" restic cat config >/dev/null 2>&1; then
    echo "$name: repository reachable"
  elif RESTIC_PASSWORD_FILE="$password" restic init -q "$@"; then
    echo "$name: repository created"
  else
    die "$name: cannot open nor create the repository. If it already exists, put its password" \
      "in $password; check the URL and the credentials in $env_file."
  fi
}
step "Repositories"
open_or_init "main repository" "$RESTIC_PASSWORD_FILE"
if [[ -n "${RESTIC_COPY_REPOSITORY:-}" ]]; then
  : "${RESTIC_COPY_PASSWORD_FILE:=/etc/lifeos/restic-copy-password}"
  (
    primary="$RESTIC_REPOSITORY"
    export RESTIC_REPOSITORY="$RESTIC_COPY_REPOSITORY"
    # Same chunking as the main repository: the copy stays deduplicated.
    open_or_init "off-site copy" "$RESTIC_COPY_PASSWORD_FILE" --from-repo "$primary" \
      --from-password-file "$RESTIC_PASSWORD_FILE" --copy-chunker-params
  )
  set_conf RESTIC_COPY_PASSWORD_FILE "$RESTIC_COPY_PASSWORD_FILE"
elif ! primary_is_offsite; then
  echo "Warning: the main repository is on this machine and there is no off-site copy"
  echo "(--copy-repository): a disk or server loss would take the backups with it."
fi
set_conf RESTIC_PASSWORD_FILE "$RESTIC_PASSWORD_FILE"
install -d -m 755 "$(metrics_dir)"

step "Timers (systemd)"
for unit in systemd/*.service systemd/*.timer; do
  sed "s|/opt/lifeos|$root|g" "$unit" > "/etc/systemd/system/${unit##*/}"
  chmod 644 "/etc/systemd/system/${unit##*/}"
done
systemctl daemon-reload
timers=(lifeos-backup.timer lifeos-restore-check.timer lifeos-backup-verify.timer)
if [[ -n "${RESTIC_COPY_REPOSITORY:-}" ]]; then
  timers+=(lifeos-restore-check-offsite.timer)
else
  systemctl disable --now lifeos-restore-check-offsite.timer >/dev/null 2>&1 || true
fi
systemctl enable --now "${timers[@]}"
systemctl list-timers 'lifeos-*' --no-pager

step "Alerts"
if [[ -n "${LIFEOS_ALERT_URL:-}" ]]; then
  ./scripts/notify.sh --test || echo "Warning: the test alert failed: check LIFEOS_ALERT_URL."
else
  echo "Warning: no LIFEOS_ALERT_URL: a failure would only show in 'systemctl --failed'."
fi
[[ -n "${LIFEOS_HEALTHCHECK_URL:-}" ]] \
  || echo "Note: no LIFEOS_HEALTHCHECK_URL: nothing warns you if this server stops altogether."

if [[ "$first_run" -eq 1 ]]; then
  step "First backup and restore tests (a few minutes)"
  runs=(lifeos-backup.service lifeos-restore-check.service)
  [[ -n "${RESTIC_COPY_REPOSITORY:-}" ]] && runs+=(lifeos-restore-check-offsite.service)
  for service in "${runs[@]}"; do
    if systemctl start "$service"; then
      echo "$service: OK"
    else
      die "$service failed: journalctl -u $service"
    fi
  done
fi

step "Done"
cat <<EOF
Backups run every night at 02:30; restore tests on Sundays (and monthly from the off-site copy).
Now copy the recovery sheet into your password manager:
  sudo $root/infra/scripts/export-keys.sh
Metrics for Prometheus: $(metrics_dir) (node exporter textfile collector).
EOF
