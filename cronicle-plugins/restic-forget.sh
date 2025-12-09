#!/bin/bash
# Cronicle Plugin: Restic Forget/Prune
# Cleans up old snapshots based on retention policy
#
# Required Cronicle job parameters:
#   REPO_NAME - Repository name (usually hostname)
#
# Optional parameters:
#   KEEP_LAST    - Snapshots to keep (default: 3)
#   KEEP_DAILY   - Days to keep (default: 7)
#   KEEP_WEEKLY  - Weeks to keep (default: 4)
#   KEEP_MONTHLY - Months to keep (default: 6)

set -euo pipefail

# REST server (set via env var or Cronicle settings)
RESTIC_REST_URL="${RESTIC_REST_URL:-http://your-nas:8000}"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/host/root/restic.creds}"

# REST server auth (for --private-repos mode)
RESTIC_REST_PASSWORD_FILE="${RESTIC_REST_PASSWORD_FILE:-/host/root/restic-rest.creds}"
if [[ -f "$RESTIC_REST_PASSWORD_FILE" ]]; then
    export RESTIC_REST_PASSWORD="$(cat "$RESTIC_REST_PASSWORD_FILE")"
fi

# Defaults
KEEP_LAST="${KEEP_LAST:-3}"
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${KEEP_MONTHLY:-6}"

# Validate required params
if [[ -z "${REPO_NAME:-}" ]]; then
    REPO_NAME="$(hostname)"
fi

REPO="rest:${RESTIC_REST_URL}/${REPO_NAME}/"

export RESTIC_PASSWORD_FILE
export RESTIC_REPOSITORY="$REPO"
export RESTIC_REST_USERNAME="$REPO_NAME"

echo "Starting Restic forget/prune..."
echo "Repository: $REPO"
echo "Retention policy:"
echo "  - Keep last: $KEEP_LAST"
echo "  - Keep daily: $KEEP_DAILY"
echo "  - Keep weekly: $KEEP_WEEKLY"
echo "  - Keep monthly: $KEEP_MONTHLY"
echo ""

# Update progress
echo '{"progress":0.1,"description":"Connecting to repository..."}'

# Run restic forget with prune
restic forget \
    --keep-last "$KEEP_LAST" \
    --keep-daily "$KEEP_DAILY" \
    --keep-weekly "$KEEP_WEEKLY" \
    --keep-monthly "$KEEP_MONTHLY" \
    --prune

echo ""
echo "Forget/prune completed successfully!"
echo '{"complete":1,"code":0,"description":"Forget/prune completed successfully"}'
