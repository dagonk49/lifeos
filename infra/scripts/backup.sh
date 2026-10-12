#!/usr/bin/env bash
# Encrypted, deduplicated backup with restic (>= 0.17): the database, then the encrypted
# document originals; then retention.
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

# Document originals (F10): already encrypted (AES-256-GCM) with lifeos_documents_key, which
# is NOT part of these backups: keep a copy of that key apart (docs/exploitation.md).
# Files created or removed while tar reads (uploads, purges) make it exit with status 1:
# harmless here (originals are written atomically), only status 2 or more is a failure.
restic backup --host lifeos --tag lifeos --tag documents \
  --stdin-filename lifeos-documents.tar \
  --stdin-from-command -- \
  docker compose exec -T worker sh -c \
  'tar -C /data/documents --exclude="*.tmp" --warning=no-file-changed --warning=no-file-removed -cf - .; [ "$?" -le 1 ]'

# Retention (docs/politique-de-donnees.md): 14 daily + 4 weekly. Deleted data leaves the
# backups when the last snapshot that contains it is pruned (about 35 days at most).
restic forget --host lifeos --tag db --keep-daily 14 --keep-weekly 4 --prune
restic forget --host lifeos --tag documents --keep-daily 14 --keep-weekly 4 --prune

# Light integrity check of the repository structure.
restic check
