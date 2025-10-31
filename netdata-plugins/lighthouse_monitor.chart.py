#!/usr/bin/env python3
"""
Lighthouse Consensus & Validator Monitor for Netdata
Monitors Lighthouse Ethereum consensus and validator clients
"""

import urllib.request
import json
import sys
import time

# Configuration
CONSENSUS_METRICS_URL = "http://localhost:8008/metrics"
VALIDATOR_METRICS_URL = "http://localhost:8009/metrics"
UPDATE_EVERY = 5  # seconds

# Chart definitions
CHARTS = {
    'lighthouse_sync': {
        'options': [None, 'Lighthouse Sync Status', 'slots behind', 'sync', 'lighthouse.sync', 'line'],
        'lines': [
            ['slots_behind', 'slots behind', 'absolute']
        ]
    },
    'lighthouse_peers': {
        'options': [None, 'Lighthouse Peer Count', 'peers', 'network', 'lighthouse.peers', 'line'],
        'lines': [
            ['connected_peers', 'connected peers', 'absolute']
        ]
    },
    'lighthouse_attestations': {
        'options': [None, 'Validator Attestations', 'attestations', 'validator', 'lighthouse.attestations', 'line'],
        'lines': [
            ['successful', 'successful', 'absolute'],
            ['failed', 'failed', 'absolute']
        ]
    },
    'lighthouse_validator_balance': {
        'options': [None, 'Validator Balance', 'gwei', 'validator', 'lighthouse.balance', 'line'],
        'lines': [
            ['total_balance', 'total balance', 'absolute']
        ]
    },
    'lighthouse_proposals': {
        'options': [None, 'Block Proposals', 'proposals', 'validator', 'lighthouse.proposals', 'line'],
        'lines': [
            ['successful_proposals', 'successful', 'absolute'],
            ['failed_proposals', 'failed', 'absolute']
        ]
    },
    'lighthouse_sync_committee': {
        'options': [None, 'Sync Committee Participation', 'contributions', 'validator', 'lighthouse.sync_committee', 'line'],
        'lines': [
            ['sync_committee_messages', 'messages', 'absolute']
        ]
    },
    'lighthouse_head': {
        'options': [None, 'Beacon Chain Head', 'slot', 'chain', 'lighthouse.head', 'line'],
        'lines': [
            ['head_slot', 'head slot', 'absolute']
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
            # Handle metrics with labels
            if '{' in line:
                metric_name = line.split('{')[0]
                labels_part = line.split('{')[1].split('}')[0]
                value_part = line.split('}')[-1].strip()
                value = value_part.split()[0]
                
                # Create a key with labels for differentiation
                key = f"{metric_name}"
                if labels_part:
                    key = f"{metric_name}{{{labels_part}}}"
            else:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0]
                    value = parts[1]
                else:
                    continue
            
            try:
                metrics[key] = float(value)
            except ValueError:
                pass
        except:
            pass
    
    return metrics


def fetch_metrics(url):
    """Fetch metrics from URL"""
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        print(f"ERROR: Failed to fetch metrics from {url}: {e}", file=sys.stderr)
        return None


def get_data():
    """Get current metrics data"""
    # Fetch both consensus and validator metrics
    consensus_text = fetch_metrics(CONSENSUS_METRICS_URL)
    validator_text = fetch_metrics(VALIDATOR_METRICS_URL)
    
    if not consensus_text:
        return None
    
    consensus_metrics = parse_prometheus_metrics(consensus_text)
    validator_metrics = parse_prometheus_metrics(validator_text) if validator_text else {}
    
    data = {}
    
    # Sync status - distance from head
    head_slot = consensus_metrics.get('beacon_head_slot', 0)
    sync_eth1_fallback_connected = consensus_metrics.get('sync_eth1_fallback_connected', 0)
    
    # Check if synced (various indicators)
    slots_behind = 0
    for key, value in consensus_metrics.items():
        if 'sync_slots_per_second' in key:
            if value > 0:
                slots_behind = 100  # Actively syncing
    
    data['slots_behind'] = int(slots_behind)
    
    # Peer count
    data['connected_peers'] = int(consensus_metrics.get('libp2p_peers', 0))
    
    # Head slot
    data['head_slot'] = int(head_slot)
    
    # Validator attestations
    # Sum up successful and failed attestations
    successful = 0
    failed = 0
    
    for key, value in validator_metrics.items():
        if 'validator_monitor_prev_epoch_on_chain_attester_hit' in key:
            successful += value
        elif 'validator_monitor_prev_epoch_on_chain_attester_miss' in key:
            failed += value
    
    data['successful'] = int(successful)
    data['failed'] = int(failed)
    
    # Validator balance (sum of all validators)
    total_balance = 0
    for key, value in validator_metrics.items():
        if key.startswith('validator_balance_gwei'):
            total_balance += value
    
    data['total_balance'] = int(total_balance)
    
    # Block proposals
    successful_proposals = 0
    failed_proposals = 0
    
    for key, value in validator_metrics.items():
        if 'validator_monitor_prev_epoch_on_chain_proposer_hit' in key:
            successful_proposals += value
        elif 'validator_monitor_prev_epoch_on_chain_proposer_miss' in key:
            failed_proposals += value
    
    data['successful_proposals'] = int(successful_proposals)
    data['failed_proposals'] = int(failed_proposals)
    
    # Sync committee participation
    sync_messages = 0
    for key, value in validator_metrics.items():
        if 'validator_monitor_prev_epoch_on_chain_sync_committee_messages' in key:
            sync_messages += value
    
    data['sync_committee_messages'] = int(sync_messages)
    
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
    # Output update interval
    print(f"CHART netdata.plugin_pythond Execution_time milliseconds plugins netdata.plugin_python line 145000 {UPDATE_EVERY}")
    print("DIMENSION lighthouse_monitor 'lighthouse monitor' absolute 1 1")
    
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
        print(f"SET lighthouse_monitor = {exec_time}")
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