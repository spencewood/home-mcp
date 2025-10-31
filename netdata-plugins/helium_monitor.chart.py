#!/usr/bin/env python3
"""
Helium Hotspot Monitor for Netdata
Monitors Helium (RAK/any hotspot) via Helium Public API
"""

import urllib.request
import json
import sys
import time
import os

# Configuration - SET YOUR HOTSPOT ADDRESS HERE
HOTSPOT_ADDRESS = os.environ.get('HELIUM_HOTSPOT_ADDRESS', 'YOUR_HOTSPOT_ADDRESS_HERE')
HELIUM_API_BASE = "https://api.helium.io/v1"
UPDATE_EVERY = 60  # Check every minute (API rate limits apply)

# Chart definitions
CHARTS = {
    'helium_status': {
        'options': [None, 'Helium Hotspot Status', 'status', 'status', 'helium.status', 'line'],
        'lines': [
            ['online', 'online', 'absolute'],
            ['synced', 'synced', 'absolute']
        ]
    },
    'helium_block_height': {
        'options': ['helium.height', 'Helium_Block_Height', 'blocks', 'sync', 'helium.block_height', 'line'],
        'lines': [
            ['block_height', 'block_height', 'absolute']
        ]
    },
    'helium_witnesses': {
        'options': [None, 'Helium Witness Activity', 'witnesses', 'activity', 'helium.witnesses', 'line'],
        'lines': [
            ['witness_count', 'witness count', 'absolute']
        ]
    },
    'helium_rewards_24h': {
        'options': ['helium.rewards_24h', 'Helium_Rewards_24h', 'HNT_bones', 'earnings', 'helium.rewards_24h', 'line'],
        'lines': [
            ['rewards_24h', 'rewards 24h', 'absolute']
        ]
    },
    'helium_rewards_30d': {
        'options': ['helium.rewards_30d', 'Helium_Rewards_30d', 'HNT_bones', 'earnings', 'helium.rewards_30d', 'line'],
        'lines': [
            ['rewards_30d', 'rewards 30d', 'absolute']
        ]
    },
    'helium_challenger_activity': {
        'options': [None, 'Helium Challenger Activity (24h)', 'challenges', 'activity', 'helium.challenger', 'line'],
        'lines': [
            ['challenges_24h', 'challenges', 'absolute']
        ]
    }
}


def fetch_helium_api(endpoint):
    """Fetch data from Helium API"""
    url = f"{HELIUM_API_BASE}{endpoint}"
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Netdata-Helium-Monitor/1.0')
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"ERROR: Failed to fetch {endpoint}: {e}", file=sys.stderr)
        return None


def get_hotspot_info():
    """Get hotspot information"""
    data = fetch_helium_api(f"/hotspots/{HOTSPOT_ADDRESS}")
    if data and 'data' in data:
        return data['data']
    return None


def get_rewards_sum(min_time=None, max_time=None):
    """Get sum of rewards for a time period"""
    endpoint = f"/hotspots/{HOTSPOT_ADDRESS}/rewards/sum"
    params = []
    if min_time:
        params.append(f"min_time={min_time}")
    if max_time:
        params.append(f"max_time={max_time}")
    
    if params:
        endpoint += "?" + "&".join(params)
    
    data = fetch_helium_api(endpoint)
    if data and 'data' in data:
        return int(data['data'].get('total', 0))
    return 0


def get_witness_list():
    """Get list of recent witnesses"""
    data = fetch_helium_api(f"/hotspots/{HOTSPOT_ADDRESS}/witnessed")
    if data and 'data' in data:
        return data['data']
    return []


def get_activity_count(filter_type='all'):
    """Get activity count for last 24h"""
    data = fetch_helium_api(f"/hotspots/{HOTSPOT_ADDRESS}/activity/count")
    if data and 'data' in data:
        if filter_type == 'all':
            return sum(data['data'].values())
        return data['data'].get(filter_type, 0)
    return 0


def get_data():
    """Get current metrics data"""
    data = {}
    
    # Get hotspot info
    hotspot = get_hotspot_info()
    if not hotspot:
        print("ERROR: Could not fetch hotspot info. Check HOTSPOT_ADDRESS.", file=sys.stderr)
        return None
    
    # Status
    status = hotspot.get('status', {})
    data['online'] = 1 if status.get('online') == 'online' else 0
    
    # Block height
    data['block_height'] = hotspot.get('block', 0)
    
    # For "synced" we check if the hotspot is listening (not relayed)
    # and if it has recent activity
    listen_addrs = hotspot.get('listen_addrs', [])
    is_listening = any('/p2p/' not in addr for addr in listen_addrs) if listen_addrs else False
    data['synced'] = 1 if is_listening and data['online'] else 0
    
    # Witness count (from recent witnessed list)
    witnesses = get_witness_list()
    data['witness_count'] = len(witnesses)
    
    # Rewards - last 24 hours
    import datetime
    now = datetime.datetime.utcnow()
    yesterday = now - datetime.timedelta(days=1)
    thirty_days_ago = now - datetime.timedelta(days=30)
    
    # Format times for API (ISO 8601)
    max_time = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    min_time_24h = yesterday.strftime('%Y-%m-%dT%H:%M:%SZ')
    min_time_30d = thirty_days_ago.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    data['rewards_24h'] = get_rewards_sum(min_time_24h, max_time)
    data['rewards_30d'] = get_rewards_sum(min_time_30d, max_time)
    
    # Activity counts
    data['challenges_24h'] = get_activity_count('poc_request_v1')
    
    return data


def create_charts():
    """Output chart definitions"""
    for chart_id, chart in CHARTS.items():
        options = chart['options']
        print(f"CHART {options[0] or chart_id} {options[1]} {options[2]} {options[3]} {options[4]} {options[5]}")
        for line in chart['lines']:
            print(f"DIMENSION {line[0]} {line[1]} {line[2]}")


def update_charts(data):
    """Output data for charts"""
    if not data:
        return
    
    for chart_id in CHARTS.keys():
        print(f"BEGIN {chart_id}")
        for line in CHARTS[chart_id]['lines']:
            dim_id = line[0]
            value = data.get(dim_id, 0)
            print(f"SET {dim_id} = {value}")
        print("END")


def main():
    """Main plugin loop"""
    # Check if hotspot address is configured
    if HOTSPOT_ADDRESS == 'YOUR_HOTSPOT_ADDRESS_HERE':
        print("ERROR: Please set HELIUM_HOTSPOT_ADDRESS environment variable or edit the script", file=sys.stderr)
        sys.exit(1)
    
    # Output update interval
    print(f"CHART netdata.plugin_pythond Execution_time milliseconds plugins netdata.plugin_python line 145000 {UPDATE_EVERY}")
    print("DIMENSION helium_monitor helium_monitor absolute 1 1")
    
    # Create charts
    create_charts()
    
    # Flush output
    sys.stdout.flush()
    
    # Main loop
    while True:
        start_time = time.time()
        
        # Get and update data
        data = get_data()
        if data:
            update_charts(data)
        
        # Calculate execution time
        exec_time = int((time.time() - start_time) * 1000)
        print("BEGIN netdata.plugin_pythond")
        print(f"SET helium_monitor = {exec_time}")
        print("END")
        
        sys.stdout.flush()
        time.sleep(UPDATE_EVERY)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("INFO: Received SIGINT, exiting...", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)