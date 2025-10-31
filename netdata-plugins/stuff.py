import re

for filename in ['earnings_monitor.chart.py', 'helium_monitor.chart.py', 'lighthouse_monitor.chart.py', 'octez_monitor.chart.py', 'reth_monitor.chart.py']:
    with open(filename, 'r') as f:
        lines = f.readlines()
    
    output = []
    i = 0
    while i < len(lines):
        output.append(lines[i])
        # If we see a duplicate chart_name line, skip the second one
        if 'chart_name = CHARTS[chart_id]["options"][0]' in lines[i]:
            if i + 1 < len(lines) and 'chart_name = CHARTS[chart_id]["options"][0]' in lines[i+1]:
                i += 1  # Skip the duplicate
        i += 1
    
    with open(filename, 'w') as f:
        f.writelines(output)
    print(f"Fixed {filename}")
