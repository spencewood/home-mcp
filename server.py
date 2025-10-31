# server.py

import asyncio
import aiohttp
import json
import sys
from mcp.server import Server
from mcp.types import Tool, TextContent
import librouteros

# Load config
try:
    with open('/app/config.json', 'r') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print("ERROR: config.json not found", file=sys.stderr)
    sys.exit(1)

server = Server("home-mcp")

# === Helper Functions ===

async def query_netdata(server_name: str, endpoint: str):
    """Query a Netdata instance"""
    if server_name not in CONFIG['servers']:
        return {'error': f'Unknown server: {server_name}'}
    
    netdata_url = CONFIG['servers'][server_name]['netdata_url']
    url = f"{netdata_url}/api/v1/{endpoint}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return {'error': f'HTTP {resp.status}'}
    except asyncio.TimeoutError:
        return {'error': 'Request timed out'}
    except Exception as e:
        return {'error': str(e)}

async def query_mikrotik(path: str):
    """Query MikroTik router via API"""
    if not CONFIG.get('mikrotik', {}).get('enabled'):
        return {'error': 'MikroTik not enabled in config'}
    
    mt_config = CONFIG['mikrotik']
    
    def _query():
        """Inner sync function to run in executor"""
        try:
            # Connect to MikroTik
            api = librouteros.connect(
                host=mt_config['host'],
                username=mt_config['username'],
                password=mt_config['password'],
                port=mt_config.get('port', 8728)
            )
            
            # Execute command - path should be like 'system/resource' or 'interface'
            path_parts = path.strip('/').split('/')
            result = list(api.path(*path_parts))
            
            # Convert to serializable format
            serialized_result = []
            for item in result:
                serialized_item = {}
                for key, value in item.items():
                    # Convert values to strings for JSON serialization
                    serialized_item[key] = str(value) if value is not None else None
                serialized_result.append(serialized_item)
            
            api.close()
            
            return {'data': serialized_result}
            
        except Exception as e:
            return {'error': f'MikroTik query failed: {str(e)}'}
    
    # Run the sync function in an executor
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)

def get_server_context(server_name: str) -> str:
    """Get context about a server from config"""
    if server_name not in CONFIG['servers']:
        return ""
    
    srv = CONFIG['servers'][server_name]
    return f"{srv.get('description', '')} ({srv.get('role', 'unknown role')})"

def parse_netdata_metric(data: dict, metric_name: str = "value") -> dict:
    """Parse Netdata data response into a structured format with labels and values"""
    if 'error' in data:
        return {'error': data['error']}
    
    try:
        result = {
            'labels': data.get('labels', []),
            'data': data.get('data', []),
            'points': len(data.get('data', [])),
            'after': data.get('after'),
            'before': data.get('before'),
            'dimension_names': data.get('dimension_names', []),
            'dimension_ids': data.get('dimension_ids', [])
        }
        
        # Add latest values with labels
        if result['data'] and result['labels']:
            latest = result['data'][0]
            result['latest'] = dict(zip(result['labels'], latest))
        
        return result
    except Exception as e:
        return {'error': f'Parse error: {str(e)}', 'raw_data': data}

# === MCP Tools ===

@server.list_tools()
async def list_tools():
    server_list = ', '.join(CONFIG['servers'].keys())
    
    tools = [
        Tool(
            name="get_all_servers_overview",
            description=f"Get health overview of all homelab servers: {server_list}",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="get_server_health",
            description="Get detailed health stats for a specific server",
            inputSchema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "enum": list(CONFIG['servers'].keys()),
                        "description": "Which server to check"
                    }
                },
                "required": ["server_name"]
            }
        ),
        Tool(
            name="get_network_stats",
            description="Get network interface statistics (bandwidth, packets, errors) for a server. For cheese server, this includes bonded interface stats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "enum": list(CONFIG['servers'].keys()),
                        "description": "Which server to check"
                    },
                    "time_range": {
                        "type": "integer",
                        "description": "Seconds of historical data to retrieve (default: 600 = 10 minutes)",
                        "default": 600
                    }
                },
                "required": ["server_name"]
            }
        ),
        Tool(
            name="list_containers",
            description="List all Docker containers running on a server",
            inputSchema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "enum": list(CONFIG['servers'].keys())
                    }
                },
                "required": ["server_name"]
            }
        ),
        Tool(
            name="get_container_stats",
            description="Get CPU and memory stats for containers on a server",
            inputSchema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "enum": list(CONFIG['servers'].keys())
                    }
                },
                "required": ["server_name"]
            }
        ),
    ]
    
    # Add MikroTik tools if enabled
    if CONFIG.get('mikrotik', {}).get('enabled'):
        tools.extend([
            Tool(
                name="get_mikrotik_interfaces",
                description="Get all network interfaces on the MikroTik router including status, traffic stats, and bonding info",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_mikrotik_resources",
                description="Get MikroTik system resources (CPU, memory, uptime, temperature)",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_mikrotik_dhcp_leases",
                description="Get all DHCP leases from the MikroTik router",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_mikrotik_traffic",
                description="Get interface traffic statistics from MikroTik",
                inputSchema={"type": "object", "properties": {}}
            ),
        ])
    
    return tools

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    
    if name == "get_all_servers_overview":
        results = {}
        
        for server_name in CONFIG['servers'].keys():
            # Get basic info
            info = await query_netdata(server_name, "info")
            
            # Get current CPU usage
            cpu_data = await query_netdata(server_name, "data?chart=system.cpu&points=1&after=-60")
            cpu_parsed = parse_netdata_metric(cpu_data, "cpu")
            
            # Get current RAM usage
            ram_data = await query_netdata(server_name, "data?chart=system.ram&points=1&after=-60")
            ram_parsed = parse_netdata_metric(ram_data, "ram")
            
            status = "online" if 'error' not in info else "offline"
            
            results[server_name] = {
                'status': status,
                'context': get_server_context(server_name),
                'hostname': info.get('hostname', 'unknown') if status == 'online' else None,
                'cpu': cpu_parsed,
                'ram': ram_parsed
            }
        
        return [TextContent(type="text", text=json.dumps(results, indent=2))]
    
    elif name == "get_server_health":
        server_name = arguments["server_name"]
        
        # Get system info
        info = await query_netdata(server_name, "info")
        
        # Get CPU data (last 10 minutes)
        cpu_data = await query_netdata(server_name, "data?chart=system.cpu&after=-600")
        cpu_parsed = parse_netdata_metric(cpu_data)
        
        # Get RAM data
        ram_data = await query_netdata(server_name, "data?chart=system.ram&after=-600")
        ram_parsed = parse_netdata_metric(ram_data)
        
        # Get disk usage
        disk_data = await query_netdata(server_name, "data?chart=disk_space._&after=-600")
        disk_parsed = parse_netdata_metric(disk_data)
        
        result = {
            'server_name': server_name,
            'context': get_server_context(server_name),
            'info': info,
            'cpu': cpu_parsed,
            'ram': ram_parsed,
            'disk': disk_parsed
        }
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "get_network_stats":
        server_name = arguments["server_name"]
        time_range = arguments.get("time_range", 600)
        
        # First, get list of all charts to find network interfaces
        charts = await query_netdata(server_name, "charts")
        
        if 'error' in charts:
            return [TextContent(type="text", text=json.dumps({'error': charts['error']}, indent=2))]
        
        # Find network interface charts
        network_charts = {}
        if 'charts' in charts:
            for chart_id, chart_info in charts['charts'].items():
                # Look for net.* and net_packets.* charts
                if chart_id.startswith('net.') or chart_id.startswith('net_packets.'):
                    network_charts[chart_id] = chart_info
        
        # Get data for each network chart
        network_data = {}
        for chart_id in network_charts.keys():
            data = await query_netdata(server_name, f"data?chart={chart_id}&after=-{time_range}")
            network_data[chart_id] = parse_netdata_metric(data)
        
        result = {
            'server_name': server_name,
            'context': get_server_context(server_name),
            'time_range_seconds': time_range,
            'available_charts': list(network_charts.keys()),
            'network_data': network_data
        }
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "list_containers":
        server_name = arguments["server_name"]
        
        # Get all available charts
        charts = await query_netdata(server_name, "charts")
        
        if 'error' in charts:
            return [TextContent(type="text", text=json.dumps({'error': charts['error']}, indent=2))]
        
        # Find docker-related charts
        containers = []
        if 'charts' in charts:
            for chart_id in charts['charts'].keys():
                if 'cgroup_' in chart_id or 'docker_' in chart_id:
                    # Extract container name
                    parts = chart_id.split('.')
                    if len(parts) > 1:
                        container_name = parts[-1]
                        if container_name not in containers:
                            containers.append(container_name)
        
        result = {
            'server_name': server_name,
            'context': get_server_context(server_name),
            'container_count': len(containers),
            'containers': sorted(containers)
        }
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "get_container_stats":
        server_name = arguments["server_name"]
        
        # Get charts to find containers
        charts = await query_netdata(server_name, "charts")
        
        if 'error' in charts:
            return [TextContent(type="text", text=json.dumps({'error': charts['error']}, indent=2))]
        
        # Look for cgroup CPU and memory charts
        container_stats = {}
        
        if 'charts' in charts:
            for chart_id in charts.get('charts', {}).keys():
                if 'cgroup' in chart_id and ('cpu' in chart_id or 'mem' in chart_id):
                    data = await query_netdata(server_name, f"data?chart={chart_id}&points=1")
                    container_stats[chart_id] = parse_netdata_metric(data)
        
        result = {
            'server_name': server_name,
            'context': get_server_context(server_name),
            'container_stats': container_stats
        }
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    # === MikroTik Tools ===
    
    elif name == "get_mikrotik_interfaces":
        interfaces = await query_mikrotik('/interface')
        
        result = {
            'router': CONFIG['mikrotik']['model'],
            'description': CONFIG['mikrotik']['description'],
            'interfaces': interfaces
        }
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "get_mikrotik_resources":
        resources = await query_mikrotik('/system/resource')
        
        result = {
            'router': CONFIG['mikrotik']['model'],
            'resources': resources
        }
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "get_mikrotik_dhcp_leases":
        leases = await query_mikrotik('/ip/dhcp-server/lease')
        
        result = {
            'router': CONFIG['mikrotik']['model'],
            'dhcp_leases': leases
        }
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    elif name == "get_mikrotik_traffic":
        # Get interface statistics
        interfaces = await query_mikrotik('/interface')
        
        # Also get bonding information
        bonding = await query_mikrotik('/interface/bonding')
        
        result = {
            'router': CONFIG['mikrotik']['model'],
            'interfaces': interfaces,
            'bonding': bonding
        }
        
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    
    else:
        return [TextContent(type="text", text=json.dumps({'error': f'Unknown tool: {name}'}, indent=2))]

# === Main ===

async def main():
    from mcp.server.stdio import stdio_server
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())