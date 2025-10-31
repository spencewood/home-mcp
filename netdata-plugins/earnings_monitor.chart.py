#!/usr/bin/env python3
"""
Blockchain Earnings Monitor for Netdata
Tracks validator/baker earnings over time for Ethereum and Tezos
"""

import urllib.request
import json
import sys
import time
import os
import pickle

# Configuration
LIGHTHOUSE_VALIDATOR_URL = "http://localhost:8009/metrics"
OCTEZ_RPC_URL = "http://localhost:8732"
UPDATE_EVERY = 60  # Check every minute
STATE_FILE = "/tmp/netdata_blockchain_earnings.pkl"

# Chart definitions
CHARTS = {
    'eth_earnings_rate': {
        'options': [None, 'Ethereum Earnings Rate', 'gwei/hour', 'earnings', 'eth.earnings_rate', 'line'],
        'lines': [
            ['eth_hourly_rate', 'gwei per hour', 'absolute']
        ]
    },
    'eth_balance_change': {
        'options': [None, 'Ethereum Balance Change', 'gwei', 'earnings', 'eth.balance_change', 'line'],
        'lines': [
            ['eth_balance_diff', 'balance change', 'absolute']
        ]
    },
    'eth_daily_estimate': {
        'options': [None, 'Ethereum Daily Earnings Estimate', 'ETH', 'earnings', 'eth.daily_estimate', 'line'],
        'lines': [
            ['eth_daily_eth', 'ETH per day', 'absolute', 1, 1000000000]  # Convert gwei to ETH
        ]
    },
    'tezos_earnings_rate': {
        'options': [None, 'Tezos Earnings Rate', 'mutez/hour', 'earnings', 'tezos.earnings_rate', 'line'],
        'lines': [
            ['tezos_hourly_rate', 'mutez per hour', 'absolute']
        ]
    },
    'tezos_balance_change': {
        'options': [None, 'Tezos Balance Change', 'mutez', 'earnings', 'tezos.balance_change', 'line'],
        'lines': [
            ['tezos_balance_diff', 'balance change', 'absolute']
        ]
    },
    'tezos_daily_estimate': {
        'options': [None, 'Tezos Daily Earnings Estimate', 'XTZ', 'earnings', 'tezos.daily_estimate', 'line'],
        'lines': [
            ['tezos_daily_xtz', 'XTZ per day', 'absolute', 1, 1000000]  # Convert mutez to XTZ
        ]
    }
}


def load_state():
    """Load previous state from disk"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'rb') as f:
                return pickle.load(f)
        except:
            pass
    return {}


def save_state(state):
    """Save current state to disk"""
    try:
        with open(STATE_FILE, 'wb') as f:
            pickle.dump(state, f)
    except Exception as e:
        print(f"WARNING: Could not save state: {e}", file=sys.stderr)


def parse_prometheus_metrics(text):
    """Parse Prometheus metrics format"""
    metrics = {}
    for line in text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        
        try:
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


def fetch_eth_balance():
    """Fetch total Ethereum validator balance in gwei"""
    try:
        with urllib.request.urlopen(LIGHTHOUSE_VALIDATOR_URL, timeout=5) as response:
            text = response.read().decode('utf-8')
        
        metrics = parse_prometheus_metrics(text)
        
        # Sum all validator balances
        total_balance = 0
        for key, value in metrics.items():
            if key.startswith('validator_balance_gwei'):
                total_balance += value
        
        return total_balance
    except Exception as e:
        print(f"ERROR: Failed to fetch ETH balance: {e}", file=sys.stderr)
        return None


def fetch_tezos_balance():
    """Fetch Tezos baker balance in mutez"""
    try:
        # Get the baker address first (you'll need to configure this)
        # For now, we'll try to get it from the node or use a placeholder
        
        # Get staking balance endpoint
        url = f"{OCTEZ_RPC_URL}/chains/main/blocks/head/context/delegates"
        with urllib.request.urlopen(url, timeout=10) as response:
            delegates = json.loads(response.read().decode('utf-8'))
        
        if not delegates:
            return None
        
        # Get balance of first delegate (you may need to configure which one)
        delegate = delegates[0] if isinstance(delegates, list) else delegates
        
        url = f"{OCTEZ_RPC_URL}/chains/main/blocks/head/context/delegates/{delegate}/balance"
        with urllib.request.urlopen(url, timeout=10) as response:
            balance = int(response.read().decode('utf-8').strip('"'))
        
        return balance
    except Exception as e:
        print(f"ERROR: Failed to fetch Tezos balance: {e}", file=sys.stderr)
        return None


def calculate_earnings(current_balance, state, key_prefix):
    """Calculate earnings based on balance changes"""
    now = time.time()
    
    if key_prefix not in state:
        state[key_prefix] = {
            'last_balance': current_balance,
            'last_time': now,
            'hourly_rate': 0,
            'balance_diff': 0
        }
        return 0, 0, 0
    
    prev_data = state[key_prefix]
    time_diff = now - prev_data['last_time']
    
    # Calculate balance change
    balance_diff = current_balance - prev_data['last_balance']
    
    # Calculate hourly rate (extrapolate to 1 hour)
    if time_diff > 0:
        hours = time_diff / 3600
        hourly_rate = balance_diff / hours
    else:
        hourly_rate = 0
    
    # Calculate daily estimate (24 hours)
    daily_estimate = hourly_rate * 24
    
    # Update state
    state[key_prefix] = {
        'last_balance': current_balance,
        'last_time': now,
        'hourly_rate': hourly_rate,
        'balance_diff': balance_diff
    }
    
    return hourly_rate, balance_diff, daily_estimate


def get_data():
    """Get current earnings data"""
    state = load_state()
    data = {}
    
    # Ethereum earnings
    eth_balance = fetch_eth_balance()
    if eth_balance is not None:
        hourly, diff, daily = calculate_earnings(eth_balance, state, 'eth')
        data['eth_hourly_rate'] = int(hourly)
        data['eth_balance_diff'] = int(diff)
        data['eth_daily_eth'] = int(daily)
    else:
        data['eth_hourly_rate'] = 0
        data['eth_balance_diff'] = 0
        data['eth_daily_eth'] = 0
    
    # Tezos earnings
    tezos_balance = fetch_tezos_balance()
    if tezos_balance is not None:
        hourly, diff, daily = calculate_earnings(tezos_balance, state, 'tezos')
        data['tezos_hourly_rate'] = int(hourly)
        data['tezos_balance_diff'] = int(diff)
        data['tezos_daily_xtz'] = int(daily)
    else:
        data['tezos_hourly_rate'] = 0
        data['tezos_balance_diff'] = 0
        data['tezos_daily_xtz'] = 0
    
    save_state(state)
    return data


def create_charts():
    """Output chart definitions"""
    for chart_id, chart in CHARTS.items():
        options = chart['options']
        print(f"CHART {options[0] or chart_id} {options[1]} {options[2]} {options[3]} {options[4]} {options[5]}")
        for line in chart['lines']:
            dim_args = ' '.join(str(x) for x in line[2:])
            print(f"DIMENSION {line[0]} {line[1]} {dim_args}")


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
    print("DIMENSION earnings_monitor 'earnings monitor' absolute 1 1")
    
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
        print(f"SET earnings_monitor = {exec_time}")
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