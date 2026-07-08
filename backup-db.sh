#!/usr/bin/env bash
# backup-db.sh - optional but recommended: a deploy pipeline that never
# deletes volumes still isn't a backup. This takes a daily pg_dump so a
# bad migration, disk failure, or host-level mistake isn't unrecoverable.
#
# Suggested cron (as the same user that runs deploy.sh):
#   0 3 * * * /path/to/backup-db.sh >> /var/log/icarus-backup.log 2>&1
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

ENV_SOURCE="/opt/icarus/shared/.env"
set -a
# shellcheck disable=SC1090
source "$ENV_SOURCE"
set +a

BACKUP_DIR="/opt/icarus/backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="$BACKUP_DIR/${POSTGRES_DB:-icarus}-${TIMESTAMP}.sql.gz"

docker compose exec -T db pg_dump -U "${POSTGRES_USER:-icarus}" "${POSTGRES_DB:-icarus}" | gzip > "$OUT_FILE"

# Keep the last 14 daily backups, drop anything older.
find "$BACKUP_DIR" -name "${POSTGRES_DB:-icarus}-*.sql.gz" -mtime +14 -delete

echo "Backed up to $OUT_FILE"
