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

async def query_dozzle_sse():
    """Query Dozzle SSE events stream to get current container state"""
    if not CONFIG.get('dozzle', {}).get('enabled'):
        return {'error': 'Dozzle not enabled in config'}

    dozzle_url = CONFIG['dozzle']['url']
    url = f"{dozzle_url}/api/events/stream"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    # Read the SSE stream line by line to find the containers-changed event
                    buffer = b''
                    async for chunk in resp.content.iter_chunked(4096):
                        buffer += chunk
                        # Look for complete SSE message
                        text = buffer.decode('utf-8', errors='ignore')

                        if 'event: containers-changed' in text and 'data: [' in text:
                            # Extract just the JSON array
                            data_start = text.find('data: [') + 6
                            # Find the end of this data block (double newline marks end of SSE message)
                            data_end = text.find('\n\n', data_start)
                            if data_end == -1:
                                # Not complete yet, keep reading
                                continue

                            json_str = text[data_start:data_end].strip()

                            try:
                                containers_full = json.loads(json_str)

                                # Extract only essential fields to avoid huge response
                                containers_minimal = []
                                for container in containers_full:
                                    containers_minimal.append({
                                        'id': container.get('id'),
                                        'name': container.get('name'),
                                        'image': container.get('image'),
                                        'state': container.get('state'),
                                        'health': container.get('health'),
                                        'host': container.get('host'),
                                        'created': container.get('created'),
                                        'startedAt': container.get('startedAt')
                                        # Deliberately excluding 'stats' and 'labels' which are huge
                                    })

                                return containers_minimal
                            except json.JSONDecodeError as e:
                                return {'error': f'JSON parse error: {str(e)}', 'raw_length': len(json_str)}

                        # If buffer gets too large, something is wrong
                        if len(buffer) > 500000:  # 500KB limit
                            return {'error': 'Response too large'}

                    return {'error': 'No containers-changed event found in stream'}
                else:
                    return {'error': f'HTTP {resp.status}'}
    except asyncio.TimeoutError:
        return {'error': 'Request timed out'}
    except Exception as e:
        return {'error': f'Exception: {str(e)}'}

async def query_dozzle_logs(host_id: str, container_id: str, tail: int = 100,
                           from_time: str = None, to_time: str = None,
                           filter_pattern: str = None, levels: list = None):
    """Query historical logs for a specific container from Dozzle

    Supports advanced filtering:
    - Time range: from_time and to_time in RFC3339 format
    - Pattern matching: filter_pattern (regex)
    - Log levels: levels array for severity filtering
    """
    if not CONFIG.get('dozzle', {}).get('enabled'):
        return {'error': 'Dozzle not enabled in config'}

    # Limit tail to reasonable size
    tail = min(tail, 500)  # Max 500 lines

    dozzle_url = CONFIG['dozzle']['url']

    # Build query parameters
    params = ['stdout=true', 'stderr=true']

    # Time range or everything
    if from_time and to_time:
        # Use specific time range (RFC3339 format)
        params.append(f'from={from_time}')
        params.append(f'to={to_time}')
    else:
        # Get all available logs
        params.append('everything=true')

    # Add filter if provided
    if filter_pattern:
        # URL encode the filter pattern
        import urllib.parse
        encoded_filter = urllib.parse.quote(filter_pattern)
        params.append(f'filter={encoded_filter}')

    # Add log levels if provided
    if levels:
        for level in levels:
            params.append(f'levels={level}')

    url = f"{dozzle_url}/api/hosts/{host_id}/containers/{container_id}/logs?{'&'.join(params)}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    # Response is JSON Lines format (one JSON object per line)
                    content = await resp.read()
                    text = content.decode('utf-8', errors='ignore')

                    # Parse JSON Lines format
                    logs = []
                    for line in text.strip().split('\n'):
                        if not line:
                            continue
                        try:
                            log_entry = json.loads(line)
                            # Extract essential fields
                            # m = message, ts = timestamp (unix milliseconds), s = stream (stdout/stderr)
                            logs.append({
                                'message': log_entry.get('m', ''),
                                'timestamp': log_entry.get('ts', ''),
                                'stream': log_entry.get('s', 'unknown')
                            })
                        except json.JSONDecodeError:
                            continue

                    # Build response with query info
                    response = {
                        'log_count': len(logs),
                        'total_available': len(logs),
                        'query': {}
                    }

                    # Add query details
                    if from_time and to_time:
                        response['query']['time_range'] = f'{from_time} to {to_time}'
                    else:
                        response['query']['scope'] = 'all available logs'

                    if filter_pattern:
                        response['query']['filter'] = filter_pattern

                    if levels:
                        response['query']['levels'] = levels

                    # Return the most recent 'tail' lines
                    if len(logs) == 0:
                        response['logs'] = []
                        response['note'] = 'No logs found matching the query criteria'
                        return response

                    response['logs'] = logs[-tail:]  # Return last N logs
                    response['note'] = f'Showing last {min(tail, len(logs))} of {len(logs)} log lines'

                    return response
                else:
                    return {'error': f'HTTP {resp.status}'}
    except asyncio.TimeoutError:
        return {'error': 'Request timed out'}
    except Exception as e:
        return {'error': f'Exception: {str(e)}'}

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

    # Add Dozzle tools if enabled
    if CONFIG.get('dozzle', {}).get('enabled'):
        tools.extend([
            Tool(
                name="get_dozzle_hosts",
                description="Get all hosts monitored by Dozzle master instance (burger, cheese, tomato, fries)",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_dozzle_containers",
                description="Get all containers visible to Dozzle across all monitored hosts, with their status and basic info",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_dozzle_container_logs",
                description="Get historical logs from a container with advanced filtering. Supports time ranges, regex patterns, and log level filtering. Works for both active and idle containers.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "container_id": {
                            "type": "string",
                            "description": "Container ID or name"
                        },
                        "tail": {
                            "type": "integer",
                            "description": "Number of most recent log lines to return (default: 100, max: 500)",
                            "default": 100
                        },
                        "from_time": {
                            "type": "string",
                            "description": "Start time in RFC3339 format (e.g., '2025-10-28T00:00:00Z'). If provided, to_time is also required."
                        },
                        "to_time": {
                            "type": "string",
                            "description": "End time in RFC3339 format (e.g., '2025-10-29T23:59:59Z'). If provided, from_time is also required."
                        },
                        "filter": {
                            "type": "string",
                            "description": "Regex pattern to filter log messages (e.g., 'error|failed|exception' for errors)"
                        },
                        "levels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Array of log levels to filter by (e.g., ['ERROR', 'WARN'])"
                        }
                    },
                    "required": ["container_id"]
                }
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

        # Get disk usage - dynamically find disk_space charts
        charts = await query_netdata(server_name, "charts")
        disk_parsed = {}

        if 'error' not in charts and 'charts' in charts:
            # Find all disk_space.* charts
            disk_charts = {
                chart_id: chart_info
                for chart_id, chart_info in charts['charts'].items()
                if chart_id.startswith('disk_space.')
            }

            # Query each disk chart
            for chart_id in disk_charts.keys():
                data = await query_netdata(server_name, f"data?chart={chart_id}&after=-600")
                disk_parsed[chart_id] = parse_netdata_metric(data)
        else:
            disk_parsed = {'error': charts.get('error', 'Unable to retrieve disk charts')}

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

    # === Dozzle Tools ===

    elif name == "get_dozzle_hosts":
        # Get containers from SSE stream
        containers_data = await query_dozzle_sse()

        if 'error' in containers_data:
            return [TextContent(type="text", text=json.dumps(containers_data, indent=2))]

        # Extract unique hosts from container data
        hosts = {}
        if isinstance(containers_data, list):
            for container in containers_data:
                host_id = container.get('host')
                if host_id and host_id not in hosts:
                    hosts[host_id] = {
                        'id': host_id,
                        'container_count': 0
                    }
                if host_id:
                    hosts[host_id]['container_count'] += 1

        result = {
            'description': CONFIG['dozzle']['description'],
            'host_count': len(hosts),
            'hosts': list(hosts.values())
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_dozzle_containers":
        # Get containers from SSE stream
        containers_data = await query_dozzle_sse()

        if 'error' in containers_data:
            return [TextContent(type="text", text=json.dumps(containers_data, indent=2))]

        # Simplify container data for easier reading
        simplified_containers = []
        if isinstance(containers_data, list):
            for container in containers_data:
                simplified_containers.append({
                    'id': container.get('id'),
                    'name': container.get('name'),
                    'image': container.get('image'),
                    'state': container.get('state'),
                    'health': container.get('health', 'N/A'),
                    'host': container.get('host'),
                    'created': container.get('created'),
                    'startedAt': container.get('startedAt')
                })

        result = {
            'description': CONFIG['dozzle']['description'],
            'container_count': len(simplified_containers),
            'containers': simplified_containers
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_dozzle_container_logs":
        container_id = arguments["container_id"]
        tail = arguments.get("tail", 100)
        from_time = arguments.get("from_time")
        to_time = arguments.get("to_time")
        filter_pattern = arguments.get("filter")
        levels = arguments.get("levels")

        # First get container list to find host ID
        containers_data = await query_dozzle_sse()

        if 'error' in containers_data:
            return [TextContent(type="text", text=json.dumps(containers_data, indent=2))]

        # Find the container and its host
        host_id = None
        container_name = None
        if isinstance(containers_data, list):
            for container in containers_data:
                if container.get('id') == container_id or container.get('name') == container_id:
                    host_id = container.get('host')
                    container_name = container.get('name')
                    container_id = container.get('id')
                    break

        if not host_id:
            return [TextContent(type="text", text=json.dumps({
                'error': f'Container {container_id} not found',
                'hint': 'Use get_dozzle_containers to list available containers'
            }, indent=2))]

        # Query logs for specific container with advanced filtering
        logs_data = await query_dozzle_logs(
            host_id, container_id, tail,
            from_time=from_time,
            to_time=to_time,
            filter_pattern=filter_pattern,
            levels=levels
        )

        result = {
            'container_id': container_id,
            'container_name': container_name,
            'host_id': host_id,
            'requested_tail': tail,
            'logs': logs_data
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