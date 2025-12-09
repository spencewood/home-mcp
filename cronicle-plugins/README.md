# Cronicle Plugins

Plugin scripts for Cronicle job scheduler.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Backup Architecture                               │
└─────────────────────────────────────────────────────────────────────────────┘

   Cronicle Workers                    NAS                      MCP Server
   ┌─────────────┐                ┌───────────────┐          ┌─────────────┐
   │  worker-1   │──┐             │ Restic REST   │          │  home-mcp   │
   ├─────────────┤  │   HTTP      │   Server      │   HTTP   │             │
   │  worker-2   │──┼────────────▶│  :8000        │◀─────────│  Query      │
   ├─────────────┤  │             │               │          │  backups    │
   │  worker-3   │──┤             │  /worker-1/   │          │             │
   ├─────────────┤  │             │  /worker-2/   │          │  "When was  │
   │  worker-4   │──┘             │  /worker-3/   │          │   last      │
   └─────────────┘                │  /worker-4/   │          │   backup?"  │
         │                        └───────────────┘          └─────────────┘
         │
    Each worker only needs:
    • restic binary
    • One password (file or env var)
    • REST server URL
```

## Why REST Server vs SFTP/SSH?

| Aspect | SSH/SFTP | REST Server |
|--------|----------|-------------|
| Auth per machine | SSH key + password file | Password only |
| Network | Requires SSH access | HTTP (simpler firewall) |
| Queryable | Must SSH to check | HTTP API / MCP tools |
| Setup complexity | High | Low |

## Deployment

Copy scripts to Cronicle plugin directory on each server that needs to run them:

```bash
scp restic-backup.sh server:/opt/stacks/cronicle/data/plugins/
chmod +x /opt/stacks/cronicle/data/plugins/restic-backup.sh
```

### Worker Setup (One-time per machine)

1. Install restic:
   ```bash
   apt install restic  # or your package manager
   ```

2. Create password file (same password on all machines):
   ```bash
   echo "your-restic-password" > /root/restic.creds
   chmod 600 /root/restic.creds
   ```

3. (Optional) Set env vars in Cronicle or system-wide:
   ```bash
   RESTIC_REST_URL=http://your-nas:8000
   RESTIC_PASSWORD_FILE=/root/restic.creds
   ```

That's it. No SSH keys, no complex auth.

## Scripts

### restic-backup.sh

Restic backup plugin using REST server backend.

**Prerequisites:**
- restic installed on the Cronicle server
- Password file at `/host/root/restic.creds` (or set `RESTIC_PASSWORD_FILE` env var)
- REST server running (override default with `RESTIC_REST_URL` env var)

**Security:** No secrets in Cronicle job parameters. Password is read from file on disk.

**Cronicle Job Parameters:**
| Parameter | Required | Description |
|-----------|----------|-------------|
| BACKUP_PATHS | Yes | Space-separated paths to back up |
| REPO_NAME | No | Repository name (defaults to hostname) |
| TAGS | No | Space-separated tags for organizing/filtering snapshots |
| EXCLUDE_PATTERNS | No | Space-separated exclude patterns |
| KEEP_DAILY | No | Days to keep (default: 7) |
| KEEP_WEEKLY | No | Weeks to keep (default: 4) |
| KEEP_MONTHLY | No | Months to keep (default: 6) |

**Example Cronicle job config:**
```
BACKUP_PATHS=/opt/stacks /home/user
TAGS=daily stacks
EXCLUDE_PATTERNS=*.log *.tmp node_modules
```

Tags let you filter snapshots later: `restic snapshots --tag daily` or `restic snapshots --tag stacks`

### restic-forget.sh

Standalone prune/forget script for cleaning up old snapshots. Use when you want to run retention cleanup separately from backups.

**Cronicle Job Parameters:**
| Parameter | Required | Description |
|-----------|----------|-------------|
| REPO_NAME | No | Repository name (defaults to hostname) |
| KEEP_LAST | No | Snapshots to keep (default: 3) |
| KEEP_DAILY | No | Days to keep (default: 7) |
| KEEP_WEEKLY | No | Weeks to keep (default: 4) |
| KEEP_MONTHLY | No | Months to keep (default: 6) |

**Note:** `restic-backup.sh` already runs forget/prune after each backup. This script is for running cleanup independently (e.g., weekly deep prune across all repos).

### graphql-api.sh

Generic GraphQL API caller for triggering mutations/queries from Cronicle jobs.

**Prerequisites:**
- jq installed on the Cronicle server
- curl installed

**Cronicle Job Parameters (JSON):**
| Parameter | Required | Description |
|-----------|----------|-------------|
| api_url | Yes | GraphQL endpoint URL |
| api_key | Yes* | API key for authentication |
| graphql_query | Yes | The GraphQL query/mutation |
| description | No | Description for logging |

*Can also be set via `GRAPHQL_API_KEY` env var to avoid storing in job config.

**Example Cronicle job config (JSON params):**
```json
{
  "api_url": "http://server:9999/graphql",
  "api_key": "your-api-key",
  "graphql_query": "mutation { refreshMetadata { success } }",
  "description": "Refresh media metadata"
}
```

**Note:** This script uses Cronicle's JSON parameter input (stdin) rather than env vars because GraphQL queries can be complex/multiline.

## MCP Integration

The home-mcp server can query backup status directly from the REST server. This enables natural language queries like:

- "When was the last backup on worker-1?"
- "Are my keys backed up?"
- "Show me all snapshots tagged 'stacks'"

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `get_restic_overview` | Overview of all repos with last backup times |
| `get_restic_snapshots` | Detailed snapshot list for a repo |
| `get_restic_stats` | Repository size and file counts |
| `search_restic_snapshots` | Search across all repos by path, tag, or hostname |

### Configuration

Add to your `config.json`:

```json
{
  "restic": {
    "enabled": true,
    "rest_url": "http://your-nas:8000",
    "password": "your-restic-password",
    "description": "Restic REST server for backups",
    "repos": {
      "worker-1": "Workstation backups",
      "worker-2": "Main server backups",
      "worker-3": "Gateway backups",
      "worker-4": "Keys and secrets backup"
    }
  }
}
```

The `repos` map is optional but helps the MCP server know which repositories to check and provides descriptions for context.
