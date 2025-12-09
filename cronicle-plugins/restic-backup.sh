#!/bin/bash
# Cronicle Plugin: Restic Backup
# Deploy to: /opt/stacks/cronicle/data/plugins/restic-backup.sh on Cronicle servers
#
# Required Cronicle job parameters:
#   BACKUP_PATHS - Space-separated paths to back up
#
# Optional parameters:
#   REPO_NAME        - Repository name (defaults to hostname)
#   TAGS             - Space-separated tags (e.g., "daily stacks" or "weekly databases")
#   EXCLUDE_PATTERNS - Space-separated exclude patterns
#   KEEP_DAILY       - Days to keep (default: 7)
#   KEEP_WEEKLY      - Weeks to keep (default: 4)
#   KEEP_MONTHLY     - Months to keep (default: 6)

set -euo pipefail

# REST server (set via env var or Cronicle settings)
RESTIC_REST_URL="${RESTIC_REST_URL:-http://your-nas:8000}"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/host/root/restic.creds}"

# REST server auth (for --private-repos mode)
# Username = repo name, password from file or env
RESTIC_REST_PASSWORD_FILE="${RESTIC_REST_PASSWORD_FILE:-/host/root/restic-rest.creds}"
if [[ -f "$RESTIC_REST_PASSWORD_FILE" ]]; then
    export RESTIC_REST_PASSWORD="$(cat "$RESTIC_REST_PASSWORD_FILE")"
fi

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
export RESTIC_REST_USERNAME="$REPO_NAME"

# Build tag args
TAG_ARGS=""
if [[ -n "${TAGS:-}" ]]; then
    for tag in $TAGS; do
        TAG_ARGS="$TAG_ARGS --tag $tag"
    done
fi

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
[[ -n "${TAGS:-}" ]] && echo "Tags: $TAGS"

# shellcheck disable=SC2086
restic backup $BACKUP_PATHS $TAG_ARGS $EXCLUDE_ARGS --verbose

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
