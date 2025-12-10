#!/bin/bash
# Cronicle Plugin: Restic Backup
# Deploy to: /opt/stacks/cronicle/data/plugins/restic-backup.sh on Cronicle servers
#
# Uses a single shared repository - all hosts back up to the same repo.
# Restic automatically tags snapshots with the hostname.
#
# Required Cronicle job parameters:
#   BACKUP_PATHS - Space-separated paths to back up
#   REPO_URL     - Restic repository URL (e.g., rest:http://nas:8000/backups)
#
# Optional parameters:
#   TAGS             - Space-separated tags (e.g., "daily stacks" or "weekly databases")
#   EXCLUDE_PATTERNS - Space-separated exclude patterns
#   KEEP_DAILY       - Days to keep (default: 7)
#   KEEP_WEEKLY      - Weeks to keep (default: 4)
#   KEEP_MONTHLY     - Months to keep (default: 6)

set -euo pipefail

# Repository URL - from job param, env var, or fail
if [[ -n "${REPO_URL:-}" ]]; then
    RESTIC_REPO_URL="$REPO_URL"
elif [[ -n "${RESTIC_REPO_URL:-}" ]]; then
    RESTIC_REPO_URL="$RESTIC_REPO_URL"
else
    echo '{"complete":1,"code":1,"description":"REPO_URL not set"}'
    exit 1
fi
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/host/root/restic.creds}"

# Validate required params
if [[ -z "${BACKUP_PATHS:-}" ]]; then
    echo '{"complete":1,"code":1,"description":"BACKUP_PATHS not set"}'
    exit 1
fi

export RESTIC_PASSWORD_FILE
export RESTIC_REPOSITORY="$RESTIC_REPO_URL"

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
echo "Repository: $RESTIC_REPO_URL"
echo "Hostname: $(hostname)"
[[ -n "${TAGS:-}" ]] && echo "Tags: $TAGS"

# shellcheck disable=SC2086
restic backup $BACKUP_PATHS $TAG_ARGS $EXCLUDE_ARGS --verbose

# Report success to Cronicle
echo '{"complete":1,"code":0,"description":"Backup complete."}'
