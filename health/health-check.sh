#!/bin/bash
#
# System Health Check Script
# Returns 0 if all checks pass, 1 if any check fails
# Outputs problems to stdout for alerting
#

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

DISK_SPACE_THRESHOLD=90      # Alert if disk usage exceeds this percentage
MEMORY_THRESHOLD=95          # Alert if memory usage exceeds this percentage
SWAP_THRESHOLD=50            # Alert if swap usage exceeds this percentage
LOAD_THRESHOLD=8.0           # Alert if 5-min load average exceeds this
IOWAIT_THRESHOLD=20          # Alert if I/O wait exceeds this percentage
CPU_TEMP_THRESHOLD=80        # Alert if CPU temp exceeds this (Celsius)
SSD_WEAR_THRESHOLD=90        # Alert if SSD wear level exceeds this percentage
ZOMBIE_THRESHOLD=5           # Alert if zombie process count exceeds this
PING_TARGET="1.1.1.1"        # Target for network connectivity check
PING_TIMEOUT=5               # Seconds to wait for ping response

# =============================================================================
# State tracking
# =============================================================================

ISSUES_FOUND=0
OUTPUT=""

add_issue() {
    local severity=$1
    local message=$2
    ISSUES_FOUND=1
    if [ "$severity" = "CRITICAL" ]; then
        OUTPUT="${OUTPUT}🔴 ${message}\n"
    else
        OUTPUT="${OUTPUT}⚠️  ${message}\n"
    fi
}

# =============================================================================
# Check 1: SMART status on all drives
# =============================================================================

check_smart() {
    if ! command -v smartctl &> /dev/null; then
        return
    fi

    if ! command -v lsblk &> /dev/null; then
        return
    fi

    for drive in $(lsblk -d -n -o NAME,TYPE 2>/dev/null | awk '$2=="disk" {print "/dev/"$1}'); do
        if smartctl -H "$drive" 2>/dev/null | grep -q "PASSED"; then
            : # Drive is healthy
        else
            if smartctl -i "$drive" 2>/dev/null | grep -q "SMART support is: Available"; then
                add_issue "CRITICAL" "SMART status FAILED on $drive"
            fi
        fi
    done
}

# =============================================================================
# Check 2: SSD wear level
# =============================================================================

check_ssd_wear() {
    if ! command -v smartctl &> /dev/null; then
        return
    fi

    if ! command -v lsblk &> /dev/null; then
        return
    fi

    for drive in $(lsblk -d -n -o NAME,TYPE 2>/dev/null | awk '$2=="disk" {print "/dev/"$1}'); do
        # Try to get wear level from various SMART attributes
        # Different SSDs report this differently
        local smart_output
        smart_output=$(smartctl -A "$drive" 2>/dev/null) || continue

        # Check for NVMe drives (Percentage Used)
        local nvme_wear
        nvme_wear=$(smartctl -A "$drive" 2>/dev/null | grep -i "Percentage Used" | awk '{print $3}' | tr -d '%' || true)
        if [ -n "$nvme_wear" ] && [ "$nvme_wear" -ge "$SSD_WEAR_THRESHOLD" ] 2>/dev/null; then
            add_issue "WARNING" "SSD wear at ${nvme_wear}% on $drive"
            continue
        fi

        # Check for SATA SSDs (Wear_Leveling_Count or Media_Wearout_Indicator)
        # These typically count DOWN from 100
        local wear_value
        wear_value=$(echo "$smart_output" | grep -E "(Wear_Leveling_Count|Media_Wearout_Indicator)" | awk '{print $4}' || true)
        if [ -n "$wear_value" ] && [ "$wear_value" -le $((100 - SSD_WEAR_THRESHOLD)) ] 2>/dev/null; then
            local wear_percent=$((100 - wear_value))
            add_issue "WARNING" "SSD wear at ${wear_percent}% on $drive"
        fi
    done
}

# =============================================================================
# Check 3: Disk space usage
# =============================================================================

check_disk_space() {
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        usage=$(echo "$line" | awk '{print $5}' | sed 's/%//')
        mount=$(echo "$line" | awk '{print $6}')

        if [ "$usage" -ge "$DISK_SPACE_THRESHOLD" ]; then
            add_issue "WARNING" "Disk space at ${usage}% on ${mount}"
        fi
    done < <(df -h | grep -E '^/dev/' || true)
}

# =============================================================================
# Check 4: Memory usage
# =============================================================================

check_memory() {
    local mem_info total used percent
    mem_info=$(free | grep Mem)
    total=$(echo "$mem_info" | awk '{print $2}')
    used=$(echo "$mem_info" | awk '{print $3}')
    percent=$((used * 100 / total))

    if [ "$percent" -ge "$MEMORY_THRESHOLD" ]; then
        add_issue "WARNING" "Memory usage at ${percent}%"
    fi
}

# =============================================================================
# Check 5: Swap usage
# =============================================================================

check_swap() {
    local swap_info total used percent
    swap_info=$(free | grep Swap)
    total=$(echo "$swap_info" | awk '{print $2}')
    used=$(echo "$swap_info" | awk '{print $3}')

    # Skip if no swap configured
    if [ "$total" -eq 0 ] 2>/dev/null; then
        return
    fi

    percent=$((used * 100 / total))

    if [ "$percent" -ge "$SWAP_THRESHOLD" ]; then
        add_issue "WARNING" "Swap usage at ${percent}% (possible memory pressure)"
    fi
}

# =============================================================================
# Check 6: Load average
# =============================================================================

check_load() {
    local load_5min
    load_5min=$(uptime | awk -F'load average:' '{print $2}' | awk -F',' '{print $2}' | xargs)

    if awk "BEGIN {exit !($load_5min > $LOAD_THRESHOLD)}"; then
        add_issue "WARNING" "High load average: $load_5min (5-min)"
    fi
}

# =============================================================================
# Check 7: I/O wait
# =============================================================================

check_iowait() {
    if ! command -v iostat &> /dev/null; then
        return
    fi

    # Get current I/O wait percentage (second sample for accuracy)
    local iowait
    iowait=$(iostat -c 1 2 2>/dev/null | tail -1 | awk '{print $4}')

    if [ -n "$iowait" ]; then
        # Compare floats using awk
        if awk "BEGIN {exit !($iowait > $IOWAIT_THRESHOLD)}"; then
            add_issue "WARNING" "High I/O wait: ${iowait}%"
        fi
    fi
}

# =============================================================================
# Check 8: CPU temperature
# =============================================================================

check_cpu_temp() {
    local temp=""

    # Try sensors command first
    if command -v sensors &> /dev/null; then
        # Get highest CPU temperature reading
        temp=$(sensors 2>/dev/null | grep -E "(Core|Tctl|CPU)" | grep -oP '\+\K[0-9]+(?=\.[0-9]*°C)' | sort -rn | head -1 || true)
    fi

    # Fallback: Try reading from thermal zones (Linux)
    if [ -z "$temp" ] && [ -d /sys/class/thermal ]; then
        for zone in /sys/class/thermal/thermal_zone*/temp; do
            if [ -f "$zone" ]; then
                local zone_temp
                zone_temp=$(cat "$zone" 2>/dev/null)
                if [ -n "$zone_temp" ]; then
                    # Convert from millidegrees
                    zone_temp=$((zone_temp / 1000))
                    if [ -z "$temp" ] || [ "$zone_temp" -gt "$temp" ]; then
                        temp=$zone_temp
                    fi
                fi
            fi
        done
    fi

    if [ -n "$temp" ] && [ "$temp" -ge "$CPU_TEMP_THRESHOLD" ] 2>/dev/null; then
        add_issue "WARNING" "High CPU temperature: ${temp}°C"
    fi
}

# =============================================================================
# Check 9: Recent dmesg errors
# =============================================================================

check_dmesg() {
    # WHITELIST approach: only alert on truly critical hardware issues
    local errors=""

    # Try journalctl first (systemd systems), fall back to dmesg
    if command -v journalctl &> /dev/null; then
        errors=$(journalctl -k -p err --since "24 hours ago" --no-pager -q 2>/dev/null | \
            grep -iE "(ata[0-9]|sata|nvme|scsi|blk_update|I/O error|ext4|xfs|btrfs|filesystem|mce|ecc|hardware error|critical|failed.*drive|sector)" | \
            tail -20 || true)
    elif command -v dmesg &> /dev/null; then
        # Fallback to dmesg (no time filtering, but better than nothing)
        errors=$(dmesg 2>/dev/null | \
            grep -iE "(ata[0-9]|sata|nvme|scsi|blk_update|I/O error|ext4|xfs|btrfs|filesystem|mce|ecc|hardware error|critical|failed.*drive|sector)" | \
            tail -20 || true)
    else
        return
    fi

    if [ -n "$errors" ]; then
        local error_count
        error_count=$(echo "$errors" | wc -l)
        add_issue "CRITICAL" "Found ${error_count} hardware/disk error(s):"
        while IFS= read -r line; do
            OUTPUT="${OUTPUT}    ${line}\n"
        done <<< "$errors"
    fi
}

# =============================================================================
# Check 9b: OOM kills (separate check - these are always important)
# =============================================================================

check_oom() {
    local oom_events=""

    # Try journalctl first, fall back to dmesg
    if command -v journalctl &> /dev/null; then
        oom_events=$(journalctl -k --since "7 days ago" --no-pager -q 2>/dev/null | \
            grep -i "Out of memory: Killed process" | tail -5 || true)
    elif command -v dmesg &> /dev/null; then
        oom_events=$(dmesg 2>/dev/null | \
            grep -i "Out of memory: Killed process" | tail -5 || true)
    else
        return
    fi

    if [ -n "$oom_events" ]; then
        local oom_count
        oom_count=$(echo "$oom_events" | wc -l)
        add_issue "CRITICAL" "OOM killer invoked ${oom_count} time(s):"
        while IFS= read -r line; do
            # Extract just the process name
            local summary
            summary=$(echo "$line" | sed 's/.*Out of memory: Killed process [0-9]* (\([^)]*\)).*/\1/' | head -c100)
            OUTPUT="${OUTPUT}    Killed: ${summary}\n"
        done <<< "$oom_events"
    fi
}

# =============================================================================
# Check 10: Failed systemd services
# =============================================================================

check_failed_services() {
    if ! command -v systemctl &> /dev/null; then
        return
    fi

    local failed_services
    failed_services=$(systemctl list-units --state=failed --no-pager --no-legend 2>/dev/null | wc -l)

    if [ "$failed_services" -gt 0 ]; then
        local services
        services=$(systemctl list-units --state=failed --no-pager --no-legend | awk '{print $1}' | head -3 | tr '\n' ', ' | sed 's/,$//')
        add_issue "WARNING" "${failed_services} failed systemd service(s): ${services}"
    fi
}

# =============================================================================
# Check 11: Zombie processes
# =============================================================================

check_zombies() {
    local zombie_count
    zombie_count=$(ps aux 2>/dev/null | awk '$8 ~ /^Z/ {count++} END {print count+0}')

    if [ "$zombie_count" -gt "$ZOMBIE_THRESHOLD" ]; then
        add_issue "WARNING" "${zombie_count} zombie processes detected"
    fi
}

# =============================================================================
# Check 12: Network connectivity
# =============================================================================

check_network() {
    if ! ping -c 1 -W "$PING_TIMEOUT" "$PING_TARGET" &> /dev/null; then
        add_issue "CRITICAL" "Network connectivity failed (cannot reach $PING_TARGET)"
    fi
}

# =============================================================================
# Run all checks
# =============================================================================

check_smart
check_ssd_wear
check_disk_space
check_memory
check_swap
check_load
check_iowait
check_cpu_temp
check_dmesg
check_oom
check_failed_services
check_zombies
check_network

# =============================================================================
# Output results
# =============================================================================

if [ $ISSUES_FOUND -eq 1 ]; then
    echo -e "${OUTPUT}" | sed 's/\\n$//'
    exit 1
else
    echo "All health checks passed"
    exit 0
fi
