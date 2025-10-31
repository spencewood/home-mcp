#!/bin/bash
set -e

# Netdata Blockchain Plugins Installer for Docker
# This script installs custom netdata plugins for monitoring blockchain nodes

echo "=== Netdata Blockchain Plugins Installer ==="
echo ""

# Configuration
CONTAINER_NAME="netdata"
PLUGINS_DIR="/mnt/docker/volumes/netdataconfig/_data/custom-plugins"
REPO_PLUGINS_DIR="./netdata-plugins"

# Check if user is in docker group
if ! groups | grep -q docker; then
    echo "ERROR: Your user is not in the docker group"
    echo "Add yourself with: sudo usermod -aG docker $USER"
    echo "Then log out and back in"
    exit 1
fi

# Check if netdata container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "ERROR: Netdata container '${CONTAINER_NAME}' is not running"
    exit 1
fi

# Check if plugin directory exists in repo
if [ ! -d "$REPO_PLUGINS_DIR" ]; then
    echo "ERROR: Plugin directory '$REPO_PLUGINS_DIR' not found"
    echo "Make sure you're running this from your repo root"
    exit 1
fi

# Check if plugins directory exists
if [ ! -d "$PLUGINS_DIR" ]; then
    echo "ERROR: Plugins directory '$PLUGINS_DIR' does not exist"
    echo ""
    echo "Create it first with:"
    echo "  sudo mkdir -p $PLUGINS_DIR"
    echo "  sudo chown -R \$USER:\$USER $PLUGINS_DIR"
    exit 1
fi

# Check if we can write to plugins directory
if [ ! -w "$PLUGINS_DIR" ]; then
    echo "ERROR: Cannot write to '$PLUGINS_DIR'"
    echo ""
    echo "Fix permissions with:"
    echo "  sudo chown -R \$USER:\$USER $PLUGINS_DIR"
    exit 1
fi

# Copy plugins
echo "Copying blockchain monitoring plugins..."
cp -v "$REPO_PLUGINS_DIR"/*.py "$PLUGINS_DIR/"

# Set permissions (executable for all)
echo "Setting permissions..."
chmod +x "$PLUGINS_DIR"/*.py

# Create/update netdata config to enable custom plugins
CONFIG_FILE="/mnt/docker/volumes/netdataconfig/_data/netdata.conf"

echo "Checking netdata configuration..."
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Creating netdata.conf..."
    docker exec $CONTAINER_NAME netdata -W config > "$CONFIG_FILE"
fi

# Add custom plugins directory to config if not already present
if ! grep -q "custom-plugins" "$CONFIG_FILE"; then
    echo "Adding custom plugins directory to netdata.conf..."
    cat >> "$CONFIG_FILE" << 'EOF'

[plugins]
    # Enable custom plugins directory
    PATH = /usr/libexec/netdata/plugins.d:/etc/netdata/custom-plugins

EOF
fi

# List installed plugins
echo ""
echo "Installed plugins:"
ls -lh "$PLUGINS_DIR"/*.py

# Restart netdata container
echo ""
echo "Restarting netdata container..."
docker restart $CONTAINER_NAME

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Your blockchain monitoring plugins are now installed!"
echo ""
echo "Next steps:"
echo "1. Wait ~30 seconds for netdata to restart"
echo "2. Visit http://cheese:19999 to see your dashboards"
echo "3. Look for these new charts:"
echo "   - Reth Monitor (Ethereum Execution)"
echo "   - Lighthouse Monitor (Ethereum Consensus)"
echo "   - Octez Monitor (Tezos Node)"
echo "   - Helium Monitor (Helium Hotspot)"
echo "   - Earnings Monitor (Combined Earnings)"
echo ""
echo "To check plugin logs:"
echo "  docker logs -f netdata 2>&1 | grep -E '(reth|lighthouse|octez|helium|earnings)'"
echo ""