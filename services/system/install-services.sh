#!/bin/bash
set -e

# BeoSound 5C Service Installation Script
# This script installs, enables, and starts all BeoSound 5C services

echo "🎵 BeoSound 5C Service Installation Script"
echo "=========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ This script must be run as root (use sudo)"
    exit 1
fi

# Define service files
SERVICES=(
    "beo-http.service"
    "beo-player-sonos.service"
    "beo-player-bluesound.service"
    "beo-input.service"
    "beo-router.service"
    "beo-masterlink.service"
    "beo-bluetooth.service"
    "beo-source-cd.service"
    "beo-source-spotify.service"
    "beo-source-usb.service"
    "beo-source-news.service"
    "beo-ui.service"
    "beo-notify-failure@.service"
    "beo-health.service"
    "beo-health.timer"
)

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="/etc/systemd/system"

# Determine the install user (from env, SUDO_USER, or logname)
INSTALL_USER="${INSTALL_USER:-${SUDO_USER:-$(logname 2>/dev/null || whoami)}}"
INSTALL_HOME=$(eval echo "~$INSTALL_USER")

echo "📁 Script directory: $SCRIPT_DIR"
echo "📁 Target directory: $SERVICE_DIR"
echo "👤 Install user: $INSTALL_USER ($INSTALL_HOME)"
echo ""

# Create configuration directory and copy example if needed
CONFIG_DIR="/etc/beosound5c"
SECRETS_FILE="$CONFIG_DIR/secrets.env"
SECRETS_EXAMPLE="$SCRIPT_DIR/../../config/secrets.env.example"

echo "📋 Setting up configuration..."
if [ ! -d "$CONFIG_DIR" ]; then
    echo "  ✅ Creating $CONFIG_DIR"
    mkdir -p "$CONFIG_DIR"
fi

if [ ! -f "$SECRETS_FILE" ]; then
    if [ -f "$SECRETS_EXAMPLE" ]; then
        echo "  ✅ Copying secrets.env.example to $SECRETS_FILE"
        cp "$SECRETS_EXAMPLE" "$SECRETS_FILE"
        chmod 600 "$SECRETS_FILE"
        echo ""
        echo "  ⚠️  IMPORTANT: Edit $SECRETS_FILE with credentials for this device!"
        echo "     - HA_TOKEN: Home Assistant long-lived access token"
        echo "     For Spotify: open the /setup page on port 8771 after starting beo-source-spotify"
        echo ""
    else
        echo "  ⚠️  Warning: secrets.env.example not found at $SECRETS_EXAMPLE"
    fi
else
    echo "  ℹ️  Secrets file already exists at $SECRETS_FILE"
fi

if [ ! -f "$CONFIG_DIR/config.json" ]; then
    echo "  ⚠️  No config.json found — run deploy.sh to install device config"
fi

# Generate self-signed SSL cert for Spotify OAuth (HTTPS required for non-localhost)
SSL_DIR="$CONFIG_DIR/ssl"
if [ ! -f "$SSL_DIR/cert.pem" ]; then
    echo "  🔐 Generating SSL certificate for Spotify OAuth..."
    mkdir -p "$SSL_DIR"
    HOSTNAME=$(hostname)
    LOCAL_IP=$(hostname -I | awk '{print $1}')
    openssl req -x509 -newkey rsa:2048 \
        -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
        -days 3650 -nodes \
        -subj "/CN=$HOSTNAME" \
        -addext "subjectAltName=IP:$LOCAL_IP,DNS:$HOSTNAME.local" \
        2>/dev/null
    # Service user needs to read the key
    CERT_OWNER="$INSTALL_USER"
    chown "$CERT_OWNER:$CERT_OWNER" "$SSL_DIR/key.pem" "$SSL_DIR/cert.pem"
    chmod 600 "$SSL_DIR/key.pem"
    chmod 644 "$SSL_DIR/cert.pem"
    echo "  ✅ SSL cert created (CN=$HOSTNAME, IP=$LOCAL_IP)"
else
    echo "  ℹ️  SSL certificate already exists"
fi

echo ""

# Ensure we are updated
sudo systemctl daemon-reload
sudo systemctl reset-failed

# Remove stale/renamed service files
STALE_SERVICES=(
    "beo-cd-source.service"      # renamed to beo-source-cd
    "beo-usb-source.service"     # renamed to beo-source-usb
    "beo-media.service"          # removed
    "beo-sonos.service"          # renamed to beo-player-sonos
    "beo-spotify.service"        # renamed to beo-source-spotify
    "beo-spotify-fetch.service"  # removed
    "beo-spotify-fetch.timer"    # removed
)
echo "🧹 Cleaning up stale services..."
for svc in "${STALE_SERVICES[@]}"; do
    if [ -f "$SERVICE_DIR/$svc" ]; then
        echo "  🗑️  Removing $svc"
        systemctl stop "$svc" 2>/dev/null || true
        systemctl disable "$svc" 2>/dev/null || true
        rm -f "$SERVICE_DIR/$svc"
    fi
done

# Copy service files to systemd directory, replacing user/home placeholders
echo "📋 Copying service files..."
for service in "${SERVICES[@]}"; do
    if [ -f "$SCRIPT_DIR/$service" ]; then
        echo "  ✅ Copying $service"
        sed -e "s|__USER__|$INSTALL_USER|g" -e "s|__HOME__|$INSTALL_HOME|g" \
            "$SCRIPT_DIR/$service" > "$SERVICE_DIR/$service"
        chmod 644 "$SERVICE_DIR/$service"
    else
        echo "  ❌ Warning: $service not found in $SCRIPT_DIR"
    fi
done

# Ensure health/notification scripts are executable
echo "📋 Setting up health check and failure notification scripts..."
chmod +x "$SCRIPT_DIR/notify-failure.sh"
chmod +x "$SCRIPT_DIR/beo-health.sh"
echo "  ✅ Scripts made executable"

echo ""

# Install Xorg config to prevent BeoRemote from generating mouse events
XORG_CONF="/etc/X11/xorg.conf.d/20-beorc-no-pointer.conf"
if [ -f "$SCRIPT_DIR/20-beorc-no-pointer.conf" ]; then
    echo "📋 Installing Xorg config (BeoRemote pointer fix)..."
    mkdir -p /etc/X11/xorg.conf.d
    cp "$SCRIPT_DIR/20-beorc-no-pointer.conf" "$XORG_CONF"
    chmod 644 "$XORG_CONF"
    echo "  ✅ Installed $XORG_CONF"
fi

echo ""

# Reload systemd daemon
echo "🔄 Reloading systemd daemon..."
systemctl daemon-reload

echo ""

# Helper: enable and start a service (skips if unit file wasn't installed)
start_service() {
    local svc="$1"
    if [ ! -f "$SERVICE_DIR/$svc" ]; then
        echo "  ⏭️  Skipping $svc (not installed)"
        return 0
    fi
    systemctl enable "$svc"
    systemctl start "$svc"
}

# Helper: disable and stop a service
disable_service() {
    local svc="$1"
    systemctl disable "$svc" 2>/dev/null || true
    systemctl stop "$svc" 2>/dev/null || true
}

# Helper: check if a menu item is enabled in config.json
menu_has() {
    python3 -c "
import json, sys
try:
    cfg = json.load(open('$CONFIG_DIR/config.json'))
    menu = cfg.get('menu', {})
    sys.exit(0 if '$1' in menu else 1)
except:
    sys.exit(1)
" 2>/dev/null
}

# Enable and start services in dependency order
echo "🚀 Enabling and starting services..."

# Start base services first
echo "  🌐 Starting HTTP server..."
start_service beo-http.service

# Determine configured player type from config.json
PLAYER_TYPE=$(python3 -c "import json; print(json.load(open('$CONFIG_DIR/config.json')).get('player',{}).get('type','sonos'))" 2>/dev/null || echo "sonos")
echo "  ℹ️  Configured player type: $PLAYER_TYPE"

if [ "$PLAYER_TYPE" = "sonos" ]; then
    echo "  📡 Starting Sonos player..."
    start_service beo-player-sonos.service
    echo "  📡 Disabling BlueSound player (not configured)..."
    systemctl disable beo-player-bluesound.service 2>/dev/null || true
    systemctl stop beo-player-bluesound.service 2>/dev/null || true
elif [ "$PLAYER_TYPE" = "bluesound" ]; then
    echo "  📡 Starting BlueSound player..."
    start_service beo-player-bluesound.service
    echo "  📡 Disabling Sonos player (not configured)..."
    systemctl disable beo-player-sonos.service 2>/dev/null || true
    systemctl stop beo-player-sonos.service 2>/dev/null || true
elif [ "$PLAYER_TYPE" = "none" ]; then
    echo "  ℹ️  No network player configured — skipping player services"
    disable_service beo-player-sonos.service
    disable_service beo-player-bluesound.service
else
    echo "  ⚠️  Unknown player type '$PLAYER_TYPE', starting both..."
    start_service beo-player-sonos.service || true
    start_service beo-player-bluesound.service || true
fi

echo "  🎮 Starting input server..."
start_service beo-input.service

echo "  🔀 Starting Event Router..."
start_service beo-router.service

echo "  🔗 Starting MasterLink sniffer..."
start_service beo-masterlink.service

echo "  📱 Starting Bluetooth service..."
start_service beo-bluetooth.service

# Start source services based on menu configuration
echo ""
echo "  📋 Checking menu config for optional sources..."

if menu_has "CD"; then
    echo "  💿 Starting CD source..."
    start_service beo-source-cd.service
else
    echo "  ⏭️  CD not in menu — skipping beo-source-cd"
    disable_service beo-source-cd.service
fi

if menu_has "SPOTIFY"; then
    echo "  🎵 Starting Spotify source..."
    start_service beo-source-spotify.service
else
    echo "  ⏭️  SPOTIFY not in menu — skipping beo-source-spotify"
    disable_service beo-source-spotify.service
fi

if menu_has "USB"; then
    echo "  💾 Starting USB source..."
    start_service beo-source-usb.service
else
    echo "  ⏭️  USB not in menu — skipping beo-source-usb"
    disable_service beo-source-usb.service
fi

if menu_has "NEWS"; then
    echo "  📰 Starting News source..."
    start_service beo-source-news.service
else
    echo "  ⏭️  NEWS not in menu — skipping beo-source-news"
    disable_service beo-source-news.service
fi

# Start UI service last (depends on HTTP)
echo "  🖥️  Starting UI service..."
start_service beo-ui.service

# Enable health check timer (auto-recovers failed services every 5 min)
echo "  🩺 Enabling health check timer..."
start_service beo-health.timer

echo "Reloading daemon services"
sudo systemctl daemon-reload
sudo systemctl reset-failed

# Check status of all services
echo "📊 Service Status Check:"
echo "======================="
for service in "${SERVICES[@]}"; do
    status=$(systemctl is-active "$service" 2>/dev/null)
    enabled=$(systemctl is-enabled "$service" 2>/dev/null)
    
    if [ "$status" = "active" ]; then
        status_icon="✅"
    else
        status_icon="❌"
    fi
    
    if [ "$enabled" = "enabled" ]; then
        enabled_icon="🔄"
    else
        enabled_icon="⏸️"
    fi
    
    printf "  %s %s %-25s [%s] [%s]\n" "$status_icon" "$enabled_icon" "$service" "$status" "$enabled"
done

echo ""
echo "🎉 Installation complete!"
echo ""
echo "💡 Useful commands:"
echo "   View all service status: systemctl status beo-*"
echo "   Stop all services:       sudo systemctl stop beo-*"
echo "   Restart all services:    sudo systemctl restart beo-*"
echo "   View logs:               journalctl -u <service-name> -f"
echo ""
echo "📝 Example log commands:"
for service in "${SERVICES[@]}"; do
    echo "   journalctl -u $service -f -l"
done 
