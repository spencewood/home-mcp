# Home MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for homelab infrastructure monitoring. Provides AI assistants with real-time access to server health metrics, network statistics, container status, and router information.

## Features

### Netdata Integration
- **Multi-server monitoring**: Query multiple homelab servers running Netdata
- **System metrics**: CPU, RAM, disk usage, and historical data
- **Network statistics**: Interface bandwidth, packets, errors, and bonding info
- **Container monitoring**: Docker container discovery and resource tracking

### MikroTik Router Integration
- **System resources**: CPU, memory, uptime, and temperature
- **Network interfaces**: Status, traffic stats, and bonding configuration
- **DHCP leases**: View all active DHCP assignments
- **Traffic statistics**: Interface-level traffic monitoring

### Custom Netdata Plugins
Includes monitoring plugins for specialized services:
- **reth_monitor**: Ethereum execution client monitoring
- **lighthouse_monitor**: Ethereum consensus client monitoring
- **octez_monitor**: Tezos node monitoring
- **helium_monitor**: Helium network monitoring
- **earnings_monitor**: Crypto earnings tracking

## Prerequisites

- Python 3.11+
- Docker and Docker Compose (for containerized deployment)
- Netdata running on monitored servers
- MikroTik router with API access (optional)

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd home-mcp
```

### 2. Configure the Server

Copy the example configuration and customize it:

```bash
cp config.example.json config.json
```

Edit `config.json` with your server details:

```json
{
  "servers": {
    "your-server": {
      "netdata_url": "http://your-server.local:19999",
      "role": "compute",
      "description": "Your server description",
      "hardware_notes": "Hardware specs"
    }
  },
  "mikrotik": {
    "enabled": true,
    "model": "RouterBoard Model",
    "host": "192.168.x.x",
    "port": 8729,
    "username": "mcp-read",
    "password": "YOUR_PASSWORD_HERE"
  }
}
```

### 3. Deploy with Docker

```bash
docker-compose up -d
```

Or build and run manually:

```bash
docker build -t home-mcp .
docker run -d \
  --name home-mcp \
  --network host \
  -v $(pwd)/config.json:/app/config.json:ro \
  home-mcp
```

### 4. Local Development

```bash
pip install -r requirements.txt
python server.py
```

## Available Tools

### Server Monitoring

- **`get_all_servers_overview`**: Health overview of all configured servers
- **`get_server_health`**: Detailed health stats for a specific server (CPU, RAM, disk)
- **`get_network_stats`**: Network interface statistics with configurable time ranges
- **`list_containers`**: List all Docker containers on a server
- **`get_container_stats`**: CPU and memory stats for containers

### Router Monitoring (MikroTik)

- **`get_mikrotik_interfaces`**: All network interfaces with status and traffic
- **`get_mikrotik_resources`**: System resources (CPU, memory, uptime, temp)
- **`get_mikrotik_dhcp_leases`**: All DHCP lease information
- **`get_mikrotik_traffic`**: Interface traffic statistics and bonding info

## MCP Client Configuration

### Claude Desktop

Add to your Claude Desktop configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "home-mcp": {
      "command": "docker",
      "args": [
        "exec",
        "-i",
        "home-mcp",
        "python",
        "server.py"
      ]
    }
  }
}
```

### Other MCP Clients

Connect via stdio transport. The server communicates over standard input/output using the MCP protocol.

## Netdata Plugins Installation

To install the custom Netdata plugins on your monitored servers:

```bash
cd netdata-plugins
sudo ./install.sh
```

This will copy the plugins to `/usr/libexec/netdata/python.d/` and restart Netdata.

## Security Considerations

- **Read-only access**: Recommended to use read-only credentials for MikroTik API
- **Network isolation**: Consider running on a dedicated monitoring network
- **Config file permissions**: Protect `config.json` containing credentials
- **Firewall rules**: Ensure Netdata (port 19999) is only accessible from trusted networks

## Troubleshooting

### Connection Issues

If the MCP server can't reach Netdata instances:
- Verify Netdata is running: `http://server:19999`
- Check firewall rules on monitored servers
- Test connectivity: `curl http://server:19999/api/v1/info`

### MikroTik API Issues

- Ensure API service is enabled on the router
- Verify credentials and network connectivity
- Check port (default 8728 for plain, 8729 for SSL)

### Docker Issues

View logs:
```bash
docker logs home-mcp
```

Restart the container:
```bash
docker-compose restart
```

## Architecture

```
+-------------------+
|   MCP Client      |
|  (Claude, etc.)   |
+--------+----------+
         |
         | stdio
         |
+--------v----------+
|   Home MCP        |
|    Server         |
+----+--------+-----+
     |        |
     |        |
     v        v
+----+----+  +----------+
| Netdata |  | MikroTik |
| Servers |  |  Router  |
+---------+  +----------+
```

## Contributing
