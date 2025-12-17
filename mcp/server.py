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

async def query_adguard(endpoint: str):
    """Query AdGuard Home API"""
    if not CONFIG.get('adguard', {}).get('enabled'):
        return {'error': 'AdGuard not enabled in config'}

    ag_config = CONFIG['adguard']
    url = f"{ag_config['url']}/control/{endpoint}"

    try:
        auth = aiohttp.BasicAuth(ag_config['username'], ag_config['password'])
        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=auth, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return {'error': f'HTTP {resp.status}'}
    except asyncio.TimeoutError:
        return {'error': 'Request timed out'}
    except Exception as e:
        return {'error': str(e)}

async def query_tezos_node(endpoint: str):
    """Query Tezos node RPC"""
    if not CONFIG.get('tezos', {}).get('enabled'):
        return {'error': 'Tezos not enabled in config'}

    tz_config = CONFIG['tezos']
    url = f"{tz_config['node_url']}/{endpoint}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return {'error': f'HTTP {resp.status}'}
    except asyncio.TimeoutError:
        return {'error': 'Request timed out'}
    except Exception as e:
        return {'error': str(e)}

async def query_tzkt(endpoint: str):
    """Query TzKT API for baker/delegation info"""
    if not CONFIG.get('tezos', {}).get('enabled'):
        return {'error': 'Tezos not enabled in config'}

    tz_config = CONFIG['tezos']
    # Use public TzKT API or self-hosted
    base_url = tz_config.get('tzkt_url', 'https://api.tzkt.io')
    url = f"{base_url}/v1/{endpoint}"

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

async def query_eth_execution(method: str, params: list = None):
    """Query Ethereum execution client JSON-RPC"""
    if not CONFIG.get('ethereum', {}).get('enabled'):
        return {'error': 'Ethereum not enabled in config'}

    eth_config = CONFIG['ethereum']
    url = eth_config['execution_url']

    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or [],
        "id": 1
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if 'error' in result:
                        return {'error': result['error'].get('message', 'RPC error')}
                    return result.get('result')
                else:
                    return {'error': f'HTTP {resp.status}'}
    except asyncio.TimeoutError:
        return {'error': 'Request timed out'}
    except Exception as e:
        return {'error': str(e)}

async def query_eth_beacon(endpoint: str):
    """Query Ethereum beacon chain API"""
    if not CONFIG.get('ethereum', {}).get('enabled'):
        return {'error': 'Ethereum not enabled in config'}

    eth_config = CONFIG['ethereum']
    url = f"{eth_config['beacon_url']}/eth/v1/{endpoint}"

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

async def query_restic(command: str = 'snapshots', host: str = None):
    """Query restic repository

    Uses restic CLI to query the REST server. Commands supported:
    - snapshots: list all snapshots (optionally filtered by host)
    - stats: repository statistics
    - check: verify repository integrity (slow)
    """
    if not CONFIG.get('restic', {}).get('enabled'):
        return {'error': 'Restic not enabled in config'}

    restic_config = CONFIG['restic']
    repo_url = restic_config['repo_url']
    password = restic_config['password']

    # Build restic command
    cmd = ['restic', '-r', repo_url, '--json']

    if command == 'snapshots':
        cmd.append('snapshots')
        if host:
            cmd.extend(['--host', host])
    elif command == 'stats':
        cmd.extend(['stats', '--mode', 'raw-data'])
    elif command == 'check':
        cmd.append('check')
    else:
        return {'error': f'Unknown command: {command}'}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**dict(__import__('os').environ), 'RESTIC_PASSWORD': password}
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            # Check for common errors
            if 'repository does not exist' in error_msg.lower():
                return {'error': 'Repository not initialized', 'exists': False}
            return {'error': error_msg}

        # Parse JSON output
        output = stdout.decode().strip()
        if output:
            return json.loads(output)
        return {'success': True, 'message': 'Command completed'}

    except asyncio.TimeoutError:
        return {'error': 'Restic command timed out'}
    except json.JSONDecodeError as e:
        return {'error': f'Invalid JSON response: {str(e)}', 'raw': stdout.decode()[:500]}
    except Exception as e:
        return {'error': str(e)}

async def get_restic_overview():
    """Get overview of all backups, grouped by hostname"""
    if not CONFIG.get('restic', {}).get('enabled'):
        return {'error': 'Restic not enabled in config'}

    snapshots = await query_restic('snapshots')

    if 'error' in snapshots:
        return snapshots

    snapshot_list = snapshots if isinstance(snapshots, list) else []

    # Group snapshots by hostname
    hosts = {}
    for snap in snapshot_list:
        hostname = snap.get('hostname', 'unknown')
        if hostname not in hosts:
            hosts[hostname] = []
        hosts[hostname].append(snap)

    # Build summary per host
    results = {}
    for hostname, snaps in hosts.items():
        sorted_snaps = sorted(snaps, key=lambda x: x.get('time', ''), reverse=True)
        latest = sorted_snaps[0] if sorted_snaps else None
        results[hostname] = {
            'snapshot_count': len(snaps),
            'latest_backup': latest.get('time') if latest else None,
            'latest_tags': latest.get('tags', []) if latest else [],
            'paths': latest.get('paths', []) if latest else []
        }

    return {
        'status': 'ok',
        'total_snapshots': len(snapshot_list),
        'hosts': results
    }

# UniFi session cache
_unifi_session = None
_unifi_cookies = None

async def unifi_login():
    """Login to UniFi controller and cache the session"""
    global _unifi_session, _unifi_cookies

    if not CONFIG.get('unifi', {}).get('enabled'):
        return None, {'error': 'UniFi not enabled in config'}

    unifi_config = CONFIG['unifi']
    url = f"{unifi_config['url']}/api/login"

    verify_ssl = unifi_config.get('verify_ssl', False)
    connector = aiohttp.TCPConnector(ssl=False if not verify_ssl else None)

    try:
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        session = aiohttp.ClientSession(connector=connector, headers=headers)
        payload = {
            'username': unifi_config['username'],
            'password': unifi_config['password']
        }

        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                _unifi_session = session
                _unifi_cookies = resp.cookies
                return session, None
            else:
                # Try to get error details from response
                try:
                    error_body = await resp.text()
                except:
                    error_body = 'Could not read response body'
                await session.close()
                return None, {'error': f'Login failed: HTTP {resp.status}', 'details': error_body[:500]}
    except Exception as e:
        if session:
            await session.close()
        return None, {'error': f'Login failed: {str(e)}'}

async def query_unifi(endpoint: str, method: str = 'GET', data: dict = None):
    """Query UniFi controller API"""
    global _unifi_session, _unifi_cookies

    if not CONFIG.get('unifi', {}).get('enabled'):
        return {'error': 'UniFi not enabled in config'}

    unifi_config = CONFIG['unifi']
    site = unifi_config.get('site', 'default')

    # Build URL - endpoints starting with 'api/' are controller-level, others are site-level
    if endpoint.startswith('api/'):
        url = f"{unifi_config['url']}/{endpoint}"
    else:
        url = f"{unifi_config['url']}/api/s/{site}/{endpoint}"

    verify_ssl = unifi_config.get('verify_ssl', False)

    async def make_request(session):
        try:
            if method == 'GET':
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get('data', result)
                    elif resp.status == 401:
                        return {'error': 'Unauthorized', 'needs_reauth': True}
                    else:
                        return {'error': f'HTTP {resp.status}'}
            else:  # POST
                async with session.post(url, json=data or {}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get('data', result)
                    elif resp.status == 401:
                        return {'error': 'Unauthorized', 'needs_reauth': True}
                    else:
                        return {'error': f'HTTP {resp.status}'}
        except asyncio.TimeoutError:
            return {'error': 'Request timed out'}
        except Exception as e:
            return {'error': str(e)}

    # Try with existing session first
    if _unifi_session and not _unifi_session.closed:
        result = await make_request(_unifi_session)
        if not (isinstance(result, dict) and result.get('needs_reauth')):
            return result

    # Need to login (first time or re-auth)
    session, error = await unifi_login()
    if error:
        return error

    return await make_request(session)

async def query_unifi_cmd(manager: str, command: str, params: dict = None):
    """Execute a UniFi command via cmd endpoint"""
    if not CONFIG.get('unifi', {}).get('enabled'):
        return {'error': 'UniFi not enabled in config'}

    payload = {'cmd': command}
    if params:
        payload.update(params)

    return await query_unifi(f'cmd/{manager}', method='POST', data=payload)

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
            description="Get network interface statistics (bandwidth, packets, errors) for a server. Includes bonded interface stats where applicable.",
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
            Tool(
                name="get_mikrotik_connections",
                description="Get active firewall connections (connection tracking table)",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_mikrotik_firewall_rules",
                description="Get firewall filter and NAT rules",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_mikrotik_arp",
                description="Get ARP table - shows all devices seen on the network",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_mikrotik_wireguard",
                description="Get WireGuard interfaces and peer status",
                inputSchema={"type": "object", "properties": {}}
            ),
        ])

    # Add Dozzle tools if enabled
    if CONFIG.get('dozzle', {}).get('enabled'):
        tools.extend([
            Tool(
                name="get_dozzle_hosts",
                description="Get all hosts monitored by Dozzle master instance",
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

    # Add AdGuard tools if enabled
    if CONFIG.get('adguard', {}).get('enabled'):
        tools.extend([
            Tool(
                name="get_adguard_status",
                description="Get AdGuard Home status including protection state, filters, and version",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_adguard_stats",
                description="Get AdGuard DNS query statistics (queries, blocked, top clients, top domains)",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_adguard_query_log",
                description="Get recent DNS query log entries",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of entries to return (default: 100, max: 500)",
                            "default": 100
                        },
                        "search": {
                            "type": "string",
                            "description": "Filter by domain name"
                        },
                        "blocked_only": {
                            "type": "boolean",
                            "description": "Only show blocked queries",
                            "default": False
                        }
                    }
                }
            ),
        ])

    # Add Tezos tools if enabled
    if CONFIG.get('tezos', {}).get('enabled'):
        tools.extend([
            Tool(
                name="get_tezos_node_status",
                description="Get Tezos node sync status, head block, and network info",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_tezos_baker_info",
                description="Get baker delegation info, balance, and staking status",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_tezos_baking_rights",
                description="Get upcoming baking and endorsing rights for the baker",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "cycles": {
                            "type": "integer",
                            "description": "Number of cycles to look ahead (default: 1)",
                            "default": 1
                        }
                    }
                }
            ),
            Tool(
                name="get_tezos_rewards",
                description="Get recent baking rewards and payment history",
                inputSchema={"type": "object", "properties": {}}
            ),
        ])

    # Add Ethereum tools if enabled
    if CONFIG.get('ethereum', {}).get('enabled'):
        tools.extend([
            Tool(
                name="get_eth_node_status",
                description="Get Ethereum execution client sync status and peer info",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_eth_beacon_status",
                description="Get beacon chain (consensus) node sync status and head slot",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_eth_validator_status",
                description="Get validator status, balance, and attestation performance",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_eth_validator_duties",
                description="Get upcoming validator duties (attestations, proposals)",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_eth_rewards",
                description="Get validator rewards summary",
                inputSchema={"type": "object", "properties": {}}
            ),
        ])

    # Add Restic tools if enabled
    if CONFIG.get('restic', {}).get('enabled'):
        tools.extend([
            Tool(
                name="get_restic_overview",
                description="Get overview of all backups - shows last backup time, snapshot counts per host. Use this to quickly check backup health across all machines.",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_restic_snapshots",
                description="Get detailed snapshot list. Shows all backups with timestamps, tags, paths, and hostnames. Can filter by hostname.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "hostname": {
                            "type": "string",
                            "description": "Filter by hostname (e.g., 'server1', 'nas')"
                        },
                        "tag": {
                            "type": "string",
                            "description": "Filter by tag (e.g., 'daily', 'stacks', 'keys')"
                        },
                        "last": {
                            "type": "integer",
                            "description": "Only show the N most recent snapshots",
                            "default": 10
                        }
                    }
                }
            ),
            Tool(
                name="get_restic_stats",
                description="Get repository statistics including total size and file counts",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="search_restic_snapshots",
                description="Search for snapshots containing specific paths or tags. Use this to answer questions like 'are my keys backed up?' or 'which backups include /etc?'",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path_contains": {
                            "type": "string",
                            "description": "Search for snapshots containing this path substring (e.g., 'keys', '.ssh', 'docker')"
                        },
                        "tag": {
                            "type": "string",
                            "description": "Filter by tag"
                        },
                        "hostname": {
                            "type": "string",
                            "description": "Filter by hostname"
                        }
                    }
                }
            ),
        ])

    # Add UniFi tools if enabled
    if CONFIG.get('unifi', {}).get('enabled'):
        tools.extend([
            Tool(
                name="get_unifi_health",
                description="Get overall UniFi network health status including subsystems (WLAN, WAN, LAN, VPN)",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_devices",
                description="Get all UniFi devices (APs, switches, gateways) with status, firmware, uptime, and connection info",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_clients",
                description="Get all connected clients with IP, MAC, hostname, connection type, signal strength, and bandwidth usage",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "active_only": {
                            "type": "boolean",
                            "description": "Only show currently connected clients (default: true)",
                            "default": True
                        }
                    }
                }
            ),
            Tool(
                name="get_unifi_client_details",
                description="Get detailed info for a specific client by MAC address including history and stats",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "mac": {
                            "type": "string",
                            "description": "Client MAC address (e.g., 'aa:bb:cc:dd:ee:ff')"
                        }
                    },
                    "required": ["mac"]
                }
            ),
            Tool(
                name="get_unifi_wlans",
                description="Get all configured wireless networks (SSIDs) with security settings and status",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_networks",
                description="Get all configured networks (VLANs, subnets) with DHCP settings",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_port_forwards",
                description="Get all port forwarding rules",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_firewall_rules",
                description="Get user-defined firewall rules",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_firewall_groups",
                description="Get firewall groups (IP groups, port groups)",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_routing",
                description="Get static routes configuration",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_dpi_stats",
                description="Get Deep Packet Inspection statistics - application and category traffic breakdown",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_events",
                description="Get recent network events (connections, disconnections, alerts)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of events to retrieve (default: 50, max: 500)",
                            "default": 50
                        }
                    }
                }
            ),
            Tool(
                name="get_unifi_alarms",
                description="Get active and recent alarms/alerts",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_rogues",
                description="Get detected rogue/neighboring access points",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="get_unifi_site_stats",
                description="Get site-wide traffic statistics for a time period",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "period": {
                            "type": "string",
                            "enum": ["hourly", "daily", "monthly"],
                            "description": "Statistics period (default: daily)",
                            "default": "daily"
                        }
                    }
                }
            ),
            Tool(
                name="get_unifi_sysinfo",
                description="Get UniFi controller system information and version",
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

    elif name == "get_mikrotik_connections":
        # Get active connections from connection tracking
        connections = await query_mikrotik('/ip/firewall/connection')

        result = {
            'router': CONFIG['mikrotik']['model'],
            'connection_count': len(connections.get('data', [])) if 'data' in connections else 0,
            'connections': connections
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_mikrotik_firewall_rules":
        # Get filter rules
        filter_rules = await query_mikrotik('/ip/firewall/filter')

        # Get NAT rules
        nat_rules = await query_mikrotik('/ip/firewall/nat')

        result = {
            'router': CONFIG['mikrotik']['model'],
            'filter_rules': filter_rules,
            'nat_rules': nat_rules
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_mikrotik_arp":
        # Get ARP table
        arp = await query_mikrotik('/ip/arp')

        result = {
            'router': CONFIG['mikrotik']['model'],
            'arp_entries': arp
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_mikrotik_wireguard":
        # Get WireGuard interfaces
        wg_interfaces = await query_mikrotik('/interface/wireguard')

        # Get WireGuard peers
        wg_peers = await query_mikrotik('/interface/wireguard/peers')

        result = {
            'router': CONFIG['mikrotik']['model'],
            'wireguard_interfaces': wg_interfaces,
            'wireguard_peers': wg_peers
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

    # === AdGuard Tools ===

    elif name == "get_adguard_status":
        status = await query_adguard('status')

        result = {
            'description': CONFIG.get('adguard', {}).get('description', 'AdGuard Home'),
            'status': status
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_adguard_stats":
        stats = await query_adguard('stats')

        result = {
            'description': CONFIG.get('adguard', {}).get('description', 'AdGuard Home'),
            'stats': stats
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_adguard_query_log":
        limit = min(arguments.get("limit", 100), 500)
        search = arguments.get("search", "")
        blocked_only = arguments.get("blocked_only", False)

        # Build query params
        params = f"limit={limit}"
        if search:
            params += f"&search={search}"
        if blocked_only:
            params += "&response_status=blocked"

        query_log = await query_adguard(f'querylog?{params}')

        result = {
            'description': CONFIG.get('adguard', {}).get('description', 'AdGuard Home'),
            'query_log': query_log
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # === Tezos Tools ===

    elif name == "get_tezos_node_status":
        # Get node bootstrapped status
        bootstrapped = await query_tezos_node('chains/main/is_bootstrapped')

        # Get head block
        head = await query_tezos_node('chains/main/blocks/head/header')

        # Get network connections
        connections = await query_tezos_node('network/connections')

        result = {
            'description': CONFIG.get('tezos', {}).get('description', 'Tezos Node'),
            'bootstrapped': bootstrapped,
            'head': head,
            'peer_count': len(connections) if isinstance(connections, list) else 0
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_tezos_baker_info":
        tz_config = CONFIG.get('tezos', {})
        baker_address = tz_config.get('baker_address')

        if not baker_address:
            return [TextContent(type="text", text=json.dumps({'error': 'baker_address not configured'}, indent=2))]

        # Get baker info from TzKT
        baker_info = await query_tzkt(f'accounts/{baker_address}')

        # Get delegators
        delegators = await query_tzkt(f'accounts/{baker_address}/delegators')

        result = {
            'description': tz_config.get('description', 'Tezos Baker'),
            'baker_address': baker_address,
            'baker_info': baker_info,
            'delegator_count': len(delegators) if isinstance(delegators, list) else 0,
            'delegators': delegators[:20] if isinstance(delegators, list) else delegators  # Limit to 20
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_tezos_baking_rights":
        tz_config = CONFIG.get('tezos', {})
        baker_address = tz_config.get('baker_address')
        cycles_ahead = arguments.get("cycles", 1)

        if not baker_address:
            return [TextContent(type="text", text=json.dumps({'error': 'baker_address not configured'}, indent=2))]

        # Get current cycle
        head = await query_tezos_node('chains/main/blocks/head/metadata')
        current_cycle = head.get('level_info', {}).get('cycle') if isinstance(head, dict) else None

        # Get baking rights (limit based on cycles requested - ~8192 blocks per cycle)
        rights_limit = min(cycles_ahead * 50, 200)
        baking_rights = await query_tzkt(f'rights/baking?baker={baker_address}&limit={rights_limit}')

        # Get endorsing rights
        endorsing_rights = await query_tzkt(f'rights/endorsing?baker={baker_address}&limit=50')

        result = {
            'description': tz_config.get('description', 'Tezos Baker'),
            'baker_address': baker_address,
            'current_cycle': current_cycle,
            'baking_rights': baking_rights,
            'endorsing_rights': endorsing_rights
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_tezos_rewards":
        tz_config = CONFIG.get('tezos', {})
        baker_address = tz_config.get('baker_address')

        if not baker_address:
            return [TextContent(type="text", text=json.dumps({'error': 'baker_address not configured'}, indent=2))]

        # Get recent rewards from TzKT
        rewards = await query_tzkt(f'rewards/bakers/{baker_address}?limit=10')

        result = {
            'description': tz_config.get('description', 'Tezos Baker'),
            'baker_address': baker_address,
            'recent_rewards': rewards
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # === Ethereum Tools ===

    elif name == "get_eth_node_status":
        # Get sync status
        sync_status = await query_eth_execution('eth_syncing')

        # Get peer count
        peer_count = await query_eth_execution('net_peerCount')

        # Get latest block
        latest_block = await query_eth_execution('eth_blockNumber')

        # Get client version
        client_version = await query_eth_execution('web3_clientVersion')

        result = {
            'description': CONFIG.get('ethereum', {}).get('description', 'Ethereum Node'),
            'client_version': client_version,
            'syncing': sync_status if sync_status else False,
            'peer_count': int(peer_count, 16) if isinstance(peer_count, str) else peer_count,
            'latest_block': int(latest_block, 16) if isinstance(latest_block, str) else latest_block
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_eth_beacon_status":
        # Get node sync status
        sync_status = await query_eth_beacon('node/syncing')

        # Get node identity
        identity = await query_eth_beacon('node/identity')

        # Get node peers
        peers = await query_eth_beacon('node/peers')

        result = {
            'description': CONFIG.get('ethereum', {}).get('description', 'Ethereum Beacon'),
            'sync_status': sync_status,
            'identity': identity,
            'peer_count': len(peers.get('data', [])) if isinstance(peers, dict) else 0
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_eth_validator_status":
        eth_config = CONFIG.get('ethereum', {})
        validator_indices = eth_config.get('validator_indices', [])

        if not validator_indices:
            return [TextContent(type="text", text=json.dumps({'error': 'validator_indices not configured'}, indent=2))]

        validators = []
        for idx in validator_indices[:10]:  # Limit to 10 validators
            validator = await query_eth_beacon(f'beacon/states/head/validators/{idx}')
            if isinstance(validator, dict) and 'data' in validator:
                validators.append(validator['data'])

        result = {
            'description': eth_config.get('description', 'Ethereum Validators'),
            'validator_count': len(validator_indices),
            'validators': validators
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_eth_validator_duties":
        eth_config = CONFIG.get('ethereum', {})
        validator_indices = eth_config.get('validator_indices', [])

        if not validator_indices:
            return [TextContent(type="text", text=json.dumps({'error': 'validator_indices not configured'}, indent=2))]

        # Get current epoch
        head = await query_eth_beacon('beacon/headers/head')
        current_slot = int(head.get('data', {}).get('header', {}).get('message', {}).get('slot', 0)) if isinstance(head, dict) else 0
        current_epoch = current_slot // 32

        # Get proposer duties for current epoch
        proposer_duties = await query_eth_beacon(f'validator/duties/proposer/{current_epoch}')

        result = {
            'description': eth_config.get('description', 'Ethereum Validators'),
            'current_epoch': current_epoch,
            'current_slot': current_slot,
            'proposer_duties': proposer_duties
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_eth_rewards":
        eth_config = CONFIG.get('ethereum', {})
        validator_indices = eth_config.get('validator_indices', [])

        if not validator_indices:
            return [TextContent(type="text", text=json.dumps({'error': 'validator_indices not configured'}, indent=2))]

        # Get validator balances
        validators = []
        for idx in validator_indices[:10]:
            validator = await query_eth_beacon(f'beacon/states/head/validators/{idx}')
            if isinstance(validator, dict) and 'data' in validator:
                validators.append({
                    'index': idx,
                    'balance': validator['data'].get('balance'),
                    'effective_balance': validator['data'].get('validator', {}).get('effective_balance'),
                    'status': validator['data'].get('status')
                })

        result = {
            'description': eth_config.get('description', 'Ethereum Validators'),
            'validators': validators
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # === Restic Tools ===

    elif name == "get_restic_overview":
        overview = await get_restic_overview()

        result = {
            'description': CONFIG.get('restic', {}).get('description', 'Restic Backup Server'),
            'repo_url': CONFIG.get('restic', {}).get('repo_url'),
            **overview
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_restic_snapshots":
        hostname_filter = arguments.get("hostname")
        tag_filter = arguments.get("tag")
        last_n = arguments.get("last", 10)

        snapshots = await query_restic('snapshots', host=hostname_filter)

        if 'error' in snapshots:
            return [TextContent(type="text", text=json.dumps(snapshots, indent=2))]

        snapshot_list = snapshots if isinstance(snapshots, list) else []

        # Filter by tag if specified
        if tag_filter and snapshot_list:
            snapshot_list = [s for s in snapshot_list if tag_filter in s.get('tags', [])]

        # Sort by time descending
        snapshot_list = sorted(snapshot_list, key=lambda x: x.get('time', ''), reverse=True)

        # Limit to last N
        snapshot_list = snapshot_list[:last_n]

        # Simplify output
        simplified = []
        for snap in snapshot_list:
            simplified.append({
                'id': snap.get('short_id', snap.get('id', '')[:8]),
                'time': snap.get('time'),
                'hostname': snap.get('hostname'),
                'tags': snap.get('tags', []),
                'paths': snap.get('paths', [])
            })

        result = {
            'snapshot_count': len(simplified),
            'filter': {'hostname': hostname_filter, 'tag': tag_filter} if (hostname_filter or tag_filter) else None,
            'snapshots': simplified
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_restic_stats":
        stats = await query_restic('stats')

        if 'error' in stats:
            return [TextContent(type="text", text=json.dumps(stats, indent=2))]

        result = {
            'description': CONFIG.get('restic', {}).get('description', 'Restic Backup Server'),
            'stats': stats
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "search_restic_snapshots":
        path_contains = arguments.get("path_contains")
        tag_filter = arguments.get("tag")
        hostname_filter = arguments.get("hostname")

        snapshots = await query_restic('snapshots')

        if 'error' in snapshots:
            return [TextContent(type="text", text=json.dumps(snapshots, indent=2))]

        snapshot_list = snapshots if isinstance(snapshots, list) else []
        all_matches = []

        for snap in snapshot_list:
            match = True

            # Filter by path
            if path_contains:
                paths = snap.get('paths', [])
                if not any(path_contains.lower() in p.lower() for p in paths):
                    match = False

            # Filter by tag
            if tag_filter:
                if tag_filter not in snap.get('tags', []):
                    match = False

            # Filter by hostname
            if hostname_filter:
                if hostname_filter.lower() != snap.get('hostname', '').lower():
                    match = False

            if match:
                all_matches.append({
                    'id': snap.get('short_id', snap.get('id', '')[:8]),
                    'time': snap.get('time'),
                    'hostname': snap.get('hostname'),
                    'tags': snap.get('tags', []),
                    'paths': snap.get('paths', [])
                })

        # Sort by time descending
        all_matches = sorted(all_matches, key=lambda x: x.get('time', ''), reverse=True)

        result = {
            'search_criteria': {
                'path_contains': path_contains,
                'tag': tag_filter,
                'hostname': hostname_filter
            },
            'match_count': len(all_matches),
            'matches': all_matches[:50]  # Limit to 50 results
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # === UniFi Tools ===

    elif name == "get_unifi_health":
        health = await query_unifi('stat/health')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'health': health
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_devices":
        devices = await query_unifi('stat/device')

        if isinstance(devices, list):
            # Simplify device info
            simplified = []
            for dev in devices:
                simplified.append({
                    'name': dev.get('name', dev.get('hostname', 'Unknown')),
                    'mac': dev.get('mac'),
                    'model': dev.get('model'),
                    'type': dev.get('type'),
                    'ip': dev.get('ip'),
                    'state': dev.get('state'),
                    'adopted': dev.get('adopted'),
                    'uptime': dev.get('uptime'),
                    'version': dev.get('version'),
                    'upgradable': dev.get('upgradable'),
                    'num_sta': dev.get('num_sta'),  # connected clients
                    'tx_bytes': dev.get('tx_bytes'),
                    'rx_bytes': dev.get('rx_bytes'),
                })
            result = {
                'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
                'device_count': len(simplified),
                'devices': simplified
            }
        else:
            result = devices

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_clients":
        active_only = arguments.get("active_only", True)

        if active_only:
            clients = await query_unifi('stat/sta')
        else:
            clients = await query_unifi('rest/user')

        if isinstance(clients, list):
            simplified = []
            for client in clients:
                simplified.append({
                    'hostname': client.get('hostname', client.get('name', 'Unknown')),
                    'mac': client.get('mac'),
                    'ip': client.get('ip'),
                    'oui': client.get('oui'),  # manufacturer
                    'is_wired': client.get('is_wired'),
                    'network': client.get('network'),
                    'essid': client.get('essid'),  # WiFi network name
                    'signal': client.get('signal'),  # WiFi signal strength
                    'rssi': client.get('rssi'),
                    'tx_rate': client.get('tx_rate'),
                    'rx_rate': client.get('rx_rate'),
                    'tx_bytes': client.get('tx_bytes'),
                    'rx_bytes': client.get('rx_bytes'),
                    'uptime': client.get('uptime'),
                    'first_seen': client.get('first_seen'),
                    'last_seen': client.get('last_seen'),
                })
            result = {
                'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
                'client_count': len(simplified),
                'clients': simplified
            }
        else:
            result = clients

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_client_details":
        mac = arguments["mac"].lower()
        client = await query_unifi(f'stat/user/{mac}')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'mac': mac,
            'client': client
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_wlans":
        wlans = await query_unifi('rest/wlanconf')

        if isinstance(wlans, list):
            simplified = []
            for wlan in wlans:
                simplified.append({
                    'name': wlan.get('name'),
                    'enabled': wlan.get('enabled'),
                    'security': wlan.get('security'),
                    'wpa_mode': wlan.get('wpa_mode'),
                    'is_guest': wlan.get('is_guest'),
                    'vlan': wlan.get('vlan'),
                    'hide_ssid': wlan.get('hide_ssid'),
                    'band': wlan.get('wlan_band'),
                })
            result = {
                'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
                'wlan_count': len(simplified),
                'wlans': simplified
            }
        else:
            result = wlans

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_networks":
        networks = await query_unifi('rest/networkconf')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'networks': networks
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_port_forwards":
        forwards = await query_unifi('rest/portforward')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'port_forwards': forwards
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_firewall_rules":
        rules = await query_unifi('rest/firewallrule')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'firewall_rules': rules
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_firewall_groups":
        groups = await query_unifi('rest/firewallgroup')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'firewall_groups': groups
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_routing":
        routes = await query_unifi('rest/routing')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'routes': routes
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_dpi_stats":
        dpi = await query_unifi('stat/dpi')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'dpi_stats': dpi
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_events":
        limit = min(arguments.get("limit", 50), 500)
        events = await query_unifi(f'stat/event?_limit={limit}')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'event_count': len(events) if isinstance(events, list) else 0,
            'events': events
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_alarms":
        alarms = await query_unifi('stat/alarm')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'alarm_count': len(alarms) if isinstance(alarms, list) else 0,
            'alarms': alarms
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_rogues":
        rogues = await query_unifi('stat/rogueap')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'rogue_count': len(rogues) if isinstance(rogues, list) else 0,
            'rogues': rogues
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_site_stats":
        period = arguments.get("period", "daily")

        if period == "hourly":
            stats = await query_unifi('stat/report/hourly.site')
        elif period == "monthly":
            stats = await query_unifi('stat/report/monthly.site')
        else:
            stats = await query_unifi('stat/report/daily.site')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'period': period,
            'stats': stats
        }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_unifi_sysinfo":
        sysinfo = await query_unifi('stat/sysinfo')

        result = {
            'description': CONFIG.get('unifi', {}).get('description', 'UniFi Network'),
            'sysinfo': sysinfo
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