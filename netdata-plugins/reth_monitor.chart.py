#!/usr/bin/env python3
"""
Reth Execution Layer Monitor for Netdata
Monitors Reth Ethereum execution client metrics
"""

import urllib.request
import json
import sys
import time

# Configuration
METRICS_URL = "http://localhost:6060/metrics"
UPDATE_EVERY = 5  # seconds

# Chart definitions
CHARTS = {
    'reth_sync': {
        'options': [None, 'Reth_Sync_Status', 'boolean', 'sync', 'reth.sync', 'line'],
        'lines': [
            ['syncing', 'syncing', 'absolute']
        ]
    },
    'reth_peers': {
        'options': [None, 'Reth_Peer_Count', 'peers', 'network', 'reth.peers', 'line'],
        'lines': [
            ['active_peers', 'active_peers', 'absolute']
        ]
    },
    'reth_chain': {
        'options': [None, 'Reth_Chain_Head', 'block_number', 'chain', 'reth.chain', 'line'],
        'lines': [
            ['head_block', 'head_block', 'absolute']
        ]
    },
    'reth_gas': {
        'options': [None, 'Reth_Gas_Usage', 'gas', 'chain', 'reth.gas', 'line'],
        'lines': [
            ['gas_used', 'gas_used', 'absolute']
        ]
    }
}


def parse_prometheus_metrics(text):
    """Parse Prometheus metrics format"""
    metrics = {}
    for line in text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        try:
            # Simple parsing - metric_name{labels} value
            if '{' in line:
                metric_name = line.split('{')[0]
                value_part = line.split('}')[-1].strip()
                value = value_part.split()[0]
            else:
                parts = line.split()
                if len(parts) >= 2:
                    metric_name = parts[0]
                    value = parts[1]
                else:
                    continue
            
            try:
                metrics[metric_name] = float(value)
            except ValueError:
                pass
        except:
            pass
    
    return metrics


def fetch_metrics():
    """Fetch metrics from Reth"""
    try:
        with urllib.request.urlopen(METRICS_URL, timeout=5) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        print(f"ERROR: Failed to fetch metrics: {e}", file=sys.stderr)
        return None


def get_data():
    """Get current metrics data"""
    text = fetch_metrics()
    if not text:
        return None
    
    metrics = parse_prometheus_metrics(text)
    
    # Extract relevant metrics
    data = {}
    
    # Sync status (1 if syncing, 0 if synced)
    # Look for sync-related metrics
    data['syncing'] = 1 if metrics.get('reth_stages_checkpoint_sync', 0) > 0 else 0
    
    # Peer count
    data['active_peers'] = int(metrics.get('reth_network_active_sessions', 0))
    
    # Chain head block
    data['head_block'] = int(metrics.get('reth_consensus_engine_head_block_number', 0))
    
    # Gas used (from last processed block)
    data['gas_used'] = int(metrics.get('reth_executor_block_gas_used', 0))
    
    return data


def create_charts():
    """Output chart definitions"""
    for chart_id, chart in CHARTS.items():
        opts = chart['options']
        print(f"CHART {opts[0] or chart_id} {opts[1]} {opts[2]} {opts[3]} {opts[4]} {opts[5]}")
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
    # Output update interval
    print(f"CHART netdata.plugin_pythond Execution_time milliseconds plugins netdata.plugin_python line 145000 {UPDATE_EVERY}")
    print("DIMENSION reth_monitor reth_monitor absolute 1 1")
    
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
        print("BEGIN netdata.plugin_pythond")
        print(f"SET reth_monitor = {exec_time}")
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