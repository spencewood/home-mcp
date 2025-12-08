#!/bin/bash
# Cronicle Plugin: Restic Backup
# Deploy to: /opt/stacks/chronicle/data/plugins/restic-backup.sh on Cronicle servers
#
# Required Cronicle job parameters:
#   BACKUP_PATHS - Space-separated paths to back up
#   REPO_NAME    - Repository name (usually hostname, for --private-repos)
#
# Optional parameters:
#   EXCLUDE_PATTERNS - Space-separated exclude patterns
#   KEEP_DAILY       - Days to keep (default: 7)
#   KEEP_WEEKLY      - Weeks to keep (default: 4)
#   KEEP_MONTHLY     - Months to keep (default: 6)

set -euo pipefail

# REST server (override with RESTIC_REST_URL env var)
RESTIC_REST_URL="${RESTIC_REST_URL:-http://nas:8000}"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/root/restic.creds}"

# Defaults
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${KEEP_MONTHLY:-6}"

# Validate required params
if [[ -z "${BACKUP_PATHS:-}" ]]; then
    echo '{"complete":1,"code":1,"description":"BACKUP_PATHS not set"}'
    exit 1
fi

if [[ -z "${REPO_NAME:-}" ]]; then
    REPO_NAME="$(hostname)"
fi

REPO="rest:${RESTIC_REST_URL}/${REPO_NAME}/"

export RESTIC_PASSWORD_FILE
export RESTIC_REPOSITORY="$REPO"

# Build exclude args
EXCLUDE_ARGS=""
if [[ -n "${EXCLUDE_PATTERNS:-}" ]]; then
    for pattern in $EXCLUDE_PATTERNS; do
        EXCLUDE_ARGS="$EXCLUDE_ARGS --exclude=$pattern"
    done
fi

# Initialize repo if needed (ignore error if already initialized)
restic init 2>/dev/null || true

# Run backup
echo "Backing up: $BACKUP_PATHS"
echo "Repository: $REPO"

# shellcheck disable=SC2086
restic backup $BACKUP_PATHS $EXCLUDE_ARGS --verbose

# Prune old snapshots
echo "Pruning snapshots (keep daily:$KEEP_DAILY weekly:$KEEP_WEEKLY monthly:$KEEP_MONTHLY)"
restic forget \
    --keep-daily "$KEEP_DAILY" \
    --keep-weekly "$KEEP_WEEKLY" \
    --keep-monthly "$KEEP_MONTHLY" \
    --prune

# Report success to Cronicle
SNAPSHOT_COUNT=$(restic snapshots --json | jq 'length')
echo "{\"complete\":1,\"code\":0,\"description\":\"Backup complete. $SNAPSHOT_COUNT snapshots in repo.\"}"
