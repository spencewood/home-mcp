#!/bin/bash
# Cronicle Plugin: Restic Stdin Backup
# Streams a command's stdout directly into a restic snapshot (no local staging).
# The snapshot fails if the command exits non-zero.
#
# Required Cronicle job parameters:
#   REPO_URL        - Restic repository URL (e.g., rest:http://fries:8000/backups)
#   STDIN_COMMAND   - Command whose stdout is backed up (run via bash -o pipefail -c)
#   STDIN_FILENAME  - Filename recorded in the snapshot (e.g., fours.pgdump)
#
# Optional parameters:
#   TAGS            - Space-separated tags

set -euo pipefail

for var in REPO_URL STDIN_COMMAND STDIN_FILENAME; do
    if [[ -z "${!var:-}" ]]; then
        echo "{\"complete\":1,\"code\":1,\"description\":\"$var not set\"}"
        exit 1
    fi
done

export RESTIC_REPOSITORY="$REPO_URL"
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/host/root/restic.creds}"

TAG_ARGS=()
for tag in ${TAGS:-}; do
    TAG_ARGS+=(--tag "$tag")
done

echo "Streaming: $STDIN_COMMAND"
echo "As file:   $STDIN_FILENAME"
echo "Repository: $RESTIC_REPOSITORY"
echo "Hostname: $(hostname)"
[[ -n "${TAGS:-}" ]] && echo "Tags: $TAGS"

# Remove only stale locks (never --remove-all on a shared repo)
restic unlock 2>/dev/null || true

restic backup \
    --stdin-filename "$STDIN_FILENAME" \
    "${TAG_ARGS[@]}" \
    --verbose \
    --stdin-from-command -- \
    bash -o pipefail -c "$STDIN_COMMAND"

echo "{\"complete\":1,\"code\":0,\"description\":\"Streamed $STDIN_FILENAME to restic.\"}"