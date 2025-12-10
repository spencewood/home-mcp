# Cronicle Docker Deployment

Centralized Cronicle configuration with gitignored local overrides for per-host settings.

## Structure

```
home-mcp/
├── cronicle/
│   ├── docker-compose.yml              # Shared config (checked in)
│   ├── docker-compose.local.yml        # Local overrides (gitignored)
│   ├── docker-compose.local.yml.example
│   ├── Dockerfile
│   └── data/                           # Runtime data (gitignored)
└── cronicle-plugins/                   # Mounted directly into container
    ├── restic-backup.sh
    └── ...
```

## How It Works

Docker Compose merges YAML files in order. The local file overrides/extends the base:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

Plugins are mounted directly from `../cronicle-plugins/` - update them in the repo and they're live.

## Initial Setup (per server)

```bash
git clone <repo> /opt/stacks/home-mcp
cd /opt/stacks/home-mcp/cronicle

cp docker-compose.local.yml.example docker-compose.local.yml
vim docker-compose.local.yml  # Set hostname, volumes, worker/master config

docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

## Updating

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

## Local Config Examples

**Worker:**
```yaml
services:
  cronicle:
    hostname: lettuce
    volumes:
      - /var/spool/asterisk/backup/:/backup:ro
      - /root:/host/root:ro
    environment:
      - HOSTNAME=lettuce
      - IS_WORKER=true
```

**Master:**
```yaml
services:
  cronicle:
    hostname: burger
    volumes:
      - /opt:/host/opt:ro
      - /root:/host/root:ro
    environment:
      - HOSTNAME=burger
      - RESTIC_REST_URL=http://fries:8000
      - CRONICLE_email_from=cronicle@burger
      - CRONICLE_smtp_hostname=localhost
      - CRONICLE_smtp_port=8025
      - CRONICLE_base_app_url=https://cronicle.spencewood.com
```
