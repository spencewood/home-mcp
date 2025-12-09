#!/bin/bash
# Cronicle Plugin: Restic Forget/Prune
# Cleans up old snapshots based on retention policy
#
# Uses a single shared repository - operates on the calling host's snapshots only.
#
# Optional parameters:
#   HOST         - Target hostname (defaults to current hostname)
#   KEEP_LAST    - Snapshots to keep (default: 3)
#   KEEP_DAILY   - Days to keep (default: 7)
#   KEEP_WEEKLY  - Weeks to keep (default: 4)
#   KEEP_MONTHLY - Months to keep (default: 6)

set -euo pipefail

# Repository URL (set via env var or Cronicle settings)
RESTIC_REPO_URL="${RESTIC_REPO_URL:-rest:http://your-nas:8000/backups}"
RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/host/root/restic.creds}"

# Defaults
KEEP_LAST="${KEEP_LAST:-3}"
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${KEEP_MONTHLY:-6}"
TARGET_HOST="${HOST:-$(hostname)}"

export RESTIC_PASSWORD_FILE
export RESTIC_REPOSITORY="$RESTIC_REPO_URL"

echo "Starting Restic forget/prune..."
echo "Repository: $RESTIC_REPO_URL"
echo "Target host: $TARGET_HOST"
echo "Retention policy:"
echo "  - Keep last: $KEEP_LAST"
echo "  - Keep daily: $KEEP_DAILY"
echo "  - Keep weekly: $KEEP_WEEKLY"
echo "  - Keep monthly: $KEEP_MONTHLY"
echo ""

# Update progress
echo '{"progress":0.1,"description":"Connecting to repository..."}'

# Run restic forget with prune for the target host only
restic forget \
    --host "$TARGET_HOST" \
    --keep-last "$KEEP_LAST" \
    --keep-daily "$KEEP_DAILY" \
    --keep-weekly "$KEEP_WEEKLY" \
    --keep-monthly "$KEEP_MONTHLY" \
    --prune

echo ""
echo "Forget/prune completed successfully for $TARGET_HOST!"
echo "{\"complete\":1,\"code\":0,\"description\":\"Forget/prune completed for $TARGET_HOST\"}"
