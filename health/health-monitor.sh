#!/bin/bash
#
# Chronicle Health Monitor Plugin
# Place in your Chronicle plugins directory (/opt/cronicle/plugins/)
#
# Dependencies (for full health-check.sh functionality):
#   sudo apt install smartmontools sysstat lm-sensors jq
#   sudo sensors-detect  # after installing lm-sensors
#
# All checks gracefully skip if their tools aren't available.
#
# Configure in Chronicle UI with these parameters:
# - health_script: Path to health-check.sh (default: /opt/cronicle/scripts/health-check.sh)
# - alert_cooldown: Hours between duplicate alerts (default: 6)

set -uo pipefail

# Read JSON input from Cronicle
JSON_INPUT=$(cat)

# Parse parameters using jq if available, otherwise grep
if command -v jq >/dev/null 2>&1; then
    HEALTH_SCRIPT=$(echo "$JSON_INPUT" | jq -r '.params.health_script // "/opt/cronicle/scripts/health-check.sh"')
    ALERT_COOLDOWN=$(echo "$JSON_INPUT" | jq -r '.params.alert_cooldown // "6"')
else
    HEALTH_SCRIPT=$(echo "$JSON_INPUT" | grep -o '"health_script":"[^"]*"' | cut -d'"' -f4)
    ALERT_COOLDOWN=$(echo "$JSON_INPUT" | grep -o '"alert_cooldown":"[^"]*"' | cut -d'"' -f4)

    # Set defaults if not found
    HEALTH_SCRIPT=${HEALTH_SCRIPT:-"/opt/cronicle/scripts/health-check.sh"}
    ALERT_COOLDOWN=${ALERT_COOLDOWN:-"6"}
fi

# State file for tracking alerts
HOSTNAME=$(hostname)
STATE_FILE="/var/tmp/health-monitor-${HOSTNAME}.state"

# Load state
if [ -f "$STATE_FILE" ]; then
    source "$STATE_FILE"
else
    LAST_ALERT_HASH=""
    LAST_ALERT_TIME=0
    CONSECUTIVE_FAILURES=0
fi

# Simple hash function
hash_string() {
    echo -n "$1" | md5sum | cut -d' ' -f1
}

# Run health check
echo "Running health check on ${HOSTNAME}..."

# Use 'set +e' temporarily to prevent script exit on health check failure
set +e
HEALTH_OUTPUT=$("$HEALTH_SCRIPT" 2>&1)
HEALTH_EXIT_CODE=$?
set -e

if [ $HEALTH_EXIT_CODE -eq 0 ]; then
    # Health check passed
    echo "✓ Health check passed"

    # Log recovery if we were previously failing
    if [ "$CONSECUTIVE_FAILURES" -gt 0 ]; then
        echo "✅ ${HOSTNAME} - Health Recovered after ${CONSECUTIVE_FAILURES} failure(s)"
    fi

    # Reset state
    CONSECUTIVE_FAILURES=0
    LAST_ALERT_HASH=""
    cat > "$STATE_FILE" <<EOF
LAST_ALERT_HASH=""
LAST_ALERT_TIME=0
CONSECUTIVE_FAILURES=0
EOF

    # Tell Cronicle the job succeeded
    echo '{"complete":1,"code":0,"description":"Health check passed"}'
    exit 0
else
    # Health check failed
    echo "✗ Health check failed"
    echo "$HEALTH_OUTPUT"

    ERROR_HASH=$(hash_string "$HEALTH_OUTPUT")
    CURRENT_TIME=$(date +%s)
    CONSECUTIVE_FAILURES=$((CONSECUTIVE_FAILURES + 1))

    # Calculate time since last alert (in hours)
    HOURS_SINCE_ALERT=999
    if [ "$LAST_ALERT_TIME" -gt 0 ]; then
        HOURS_SINCE_ALERT=$(( (CURRENT_TIME - LAST_ALERT_TIME) / 3600 ))
    fi

    # Determine if we should log an alert
    SHOULD_ALERT=false
    if [ "$LAST_ALERT_HASH" != "$ERROR_HASH" ]; then
        # New type of error
        SHOULD_ALERT=true
        echo "New error detected"
    elif [ "$HOURS_SINCE_ALERT" -ge "$ALERT_COOLDOWN" ]; then
        # Same error, but cooldown period has passed
        SHOULD_ALERT=true
        echo "Cooldown period passed, logging reminder"
    else
        echo "Alert suppressed (duplicate, ${HOURS_SINCE_ALERT}h since last alert)"
    fi

    if [ "$SHOULD_ALERT" = true ]; then
        echo "⚠️ ${HOSTNAME} - Health Check Failed"
        echo "Consecutive failures: ${CONSECUTIVE_FAILURES}"

        # Update state
        LAST_ALERT_HASH="$ERROR_HASH"
        LAST_ALERT_TIME=$CURRENT_TIME
    fi

    # Save state
    cat > "$STATE_FILE" <<EOF
LAST_ALERT_HASH="$LAST_ALERT_HASH"
LAST_ALERT_TIME=$LAST_ALERT_TIME
CONSECUTIVE_FAILURES=$CONSECUTIVE_FAILURES
EOF

    # Tell Cronicle the job succeeded (monitoring job succeeded, even though health failed)
    echo '{"complete":1,"code":0,"description":"Health check failed, alert sent"}'
    exit 0
fi
