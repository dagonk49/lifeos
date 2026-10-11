#!/usr/bin/env bash
# Encrypted, deduplicated database backup with restic (>= 0.17), then retention.
#
# Required environment: RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE (keep the password file
# outside the backed-up data and outside the repository host). Example (cron, daily):
#   RESTIC_REPOSITORY=sftp:backup@nas:/srv/restic/lifeos \
#   RESTIC_PASSWORD_FILE=/root/.config/lifeos/restic-password \
#   /opt/lifeos/infra/scripts/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"

# --stdin-from-command: if pg_dump fails, restic fails and keeps no partial snapshot.
restic backup --host lifeos --tag lifeos --tag db \
  --stdin-filename lifeos-db.pgdump \
  --stdin-from-command -- \
  docker compose exec -T postgres pg_dump -U postgres -d lifeos --format=custom

# Retention (docs/politique-de-donnees.md): 14 daily + 4 weekly. Deleted data leaves the
# backups when the last snapshot that contains it is pruned (about 35 days at most).
restic forget --host lifeos --tag db --keep-daily 14 --keep-weekly 4 --prune

# Light integrity check of the repository structure.
restic check
