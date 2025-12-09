# Cronicle Plugins

Plugin scripts for Cronicle job scheduler.

## Deployment

Copy scripts to Cronicle plugin directory on each server that needs to run them:

```bash
scp restic-backup.sh server:/opt/stacks/cronicle/data/plugins/
chmod +x /opt/stacks/cronicle/data/plugins/restic-backup.sh
```

## Scripts

### restic-backup.sh

Restic backup plugin using REST server backend.

**Prerequisites:**
- restic installed on the Cronicle server
- Password file at `/host/root/restic.creds` (or set `RESTIC_PASSWORD_FILE` env var)
- REST server running (default: `http://nas:8000`, override with `RESTIC_REST_URL` env var)

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
