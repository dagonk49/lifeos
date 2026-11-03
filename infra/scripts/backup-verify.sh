#!/usr/bin/env bash
# Reads back part of the backed-up data (restic check --read-data-subset) in the main
# repository and in the off-site copy: by default the twelfth of the month's number, so that
# a year reads everything once without paying a full download every month (cloud egress).
#
#   infra/scripts/backup-verify.sh [SUBSET]     e.g. 100% to read everything now
#
# Monthly: lifeos-backup-verify.timer (a failure sends an alert).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/backup.sh
. scripts/lib/backup.sh
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { sed -n '2,8p' "$0"; exit 0; }
subset="${1:-$(date +%-m)/12}"
load_backup_env
require_restic_env
require_tools restic flock
take_lock

say "main repository: reading $subset of the data"
restic check --retry-lock 30m --read-data-subset="$subset"
if [[ -n "${RESTIC_COPY_REPOSITORY:-}" ]]; then
  (
    use_copy_repository
    say "off-site copy: reading $subset of the data"
    restic check --retry-lock 30m --read-data-subset="$subset"
  )
fi
say "backup data check OK"
