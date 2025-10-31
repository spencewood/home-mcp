#!/usr/bin/env python3
"""
Octez Tezos Node Monitor for Netdata
Monitors Octez (Tezos) node via RPC API
"""

import urllib.request
import json
import sys
import time

# Configuration
RPC_URL = "http://localhost:8732"
UPDATE_EVERY = 10  # seconds (Tezos blocks are slower)

# Chart definitions
CHARTS = {
    'octez_sync': {
        'options': ['octez.sync', 'Octez_Sync_Status', 'blocks_behind', 'sync', 'octez.sync', 'line'],
        'lines': [
            ['blocks_behind', 'blocks_behind', 'absolute']
        ]
    },
    'octez_peers': {
        'options': ['octez.peers', 'Octez_Connected_Peers', 'peers', 'network', 'octez.peers', 'line'],
        'lines': [
            ['connected', 'connected', 'absolute']
        ]
    },
    'octez_head': {
        'options': ['octez.head', 'Octez_Chain_Head', 'block_level', 'chain', 'octez.head', 'line'],
        'lines': [
            ['level', 'level', 'absolute']
        ]
    },
    'octez_operations': {
        'options': ['octez.operations', 'Octez_Operations_in_Head', 'operations', 'chain', 'octez.operations', 'line'],
        'lines': [
            ['operations', 'operations', 'absolute']
        ]
    }
}


def fetch_json(endpoint):
    """Fetch JSON from RPC endpoint"""
    url = f"{RPC_URL}{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"ERROR: Failed to fetch {endpoint}: {e}", file=sys.stderr)
        return None


def get_data():
    """Get current metrics data"""
    data = {}
    
    # Get head block
    head = fetch_json("/chains/main/blocks/head/header")
    if not head:
        return None
    
    data['level'] = int(head.get('level', 0))
    
    # Get network connections (peers)
    connections = fetch_json("/network/connections")
    if connections:
        data['connected'] = len(connections)
    else:
        data['connected'] = 0
    
    # Get operations in head block
    operations = fetch_json("/chains/main/blocks/head/operations")
    if operations:
        # Count total operations across all validation passes
        total_ops = sum(len(pass_ops) for pass_ops in operations)
        data['operations'] = total_ops
    else:
        data['operations'] = 0
    
    # Check sync status
    # Get bootstrapped status
    bootstrapped = fetch_json("/monitor/bootstrapped")
    if bootstrapped:
        is_bootstrapped = bootstrapped.get('bootstrapped', False)
        sync_state = bootstrapped.get('sync_state', 'unknown')
        
        # If not bootstrapped or syncing, estimate blocks behind
        if not is_bootstrapped or sync_state == 'syncing':
            data['blocks_behind'] = 10  # Placeholder - hard to get exact value
        else:
            data['blocks_behind'] = 0
    else:
        data['blocks_behind'] = 0
    
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
        chart_name = CHARTS[chart_id]["options"][0]
        print(f"BEGIN {chart_name}")
        for line in CHARTS[chart_id]['lines']:
            dim_id = line[0]
            value = data.get(dim_id, 0)
            print(f"SET {dim_id} = {value}")
        print("END")


def main():
    """Main plugin loop"""
    # Output update interval
    
    # Create charts
    create_charts()
    
    # Flush output
    sys.stdout.flush()
    
    # Main loop
    while True:
        start_time = time.time()
        
        # Get and update data
        data = get_data()
        update_charts(data)
        
        # Calculate execution time
        exec_time = int((time.time() - start_time) * 1000)
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
        sys.exit(1)