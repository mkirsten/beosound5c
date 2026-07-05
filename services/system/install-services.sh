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

# Load shared service registry
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/service-registry.sh"
SERVICES=("${ALL_SERVICES[@]}")

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="/etc/systemd/system"

# Determine the install user (from env, SUDO_USER, or logname)
INSTALL_USER="${INSTALL_USER:-${SUDO_USER:-$(logname 2>/dev/null || whoami)}}"
INSTALL_HOME=$(eval echo "~$INSTALL_USER")
INSTALL_UID=$(id -u "$INSTALL_USER")

echo "📁 Script directory: $SCRIPT_DIR"
echo "📁 Target directory: $SERVICE_DIR"
echo "👤 Install user: $INSTALL_USER (uid=$INSTALL_UID, home=$INSTALL_HOME)"
echo ""

# Ensure the install user has the required groups:
#   video, render - DRM/KMS display access (/dev/dri/card*)
#   tty           - xinit console access
#   input         - HID input devices
#   audio         - audio device access
echo "👥 Ensuring required group memberships for $INSTALL_USER..."
for group in video render tty input audio; do
    if getent group "$group" &>/dev/null; then
        if id -nG "$INSTALL_USER" | grep -qw "$group"; then
            echo "  ✅ Already in group: $group"
        else
            usermod -aG "$group" "$INSTALL_USER"
            echo "  ✅ Added to group: $group"
        fi
    else
        echo "  ⚠️  Group '$group' does not exist — skipping"
    fi
done
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
# Ensure the install user owns the config dir (services write tokens here)
chown "$INSTALL_USER:$INSTALL_USER" "$CONFIG_DIR"

if [ ! -f "$SECRETS_FILE" ]; then
    if [ -f "$SECRETS_EXAMPLE" ]; then
        echo "  ✅ Copying secrets.env.example to $SECRETS_FILE"
        cp "$SECRETS_EXAMPLE" "$SECRETS_FILE"
        chmod 600 "$SECRETS_FILE"
        echo ""
        echo "  ⚠️  IMPORTANT: Edit $SECRETS_FILE with credentials for this device!"
        echo "     - HA_TOKEN: Home Assistant long-lived access token"
        echo "     For Spotify: open the /setup page on port 8772 (HTTPS) after starting beo-source-spotify"
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
    echo "  🔐 Generating SSL certificate for OAuth (Spotify, Apple Music)..."
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
        sed -e "s|__USER__|$INSTALL_USER|g" -e "s|__HOME__|$INSTALL_HOME|g" -e "s|__UID__|$INSTALL_UID|g" \
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
chmod +x "$SCRIPT_DIR/reconcile-services.sh"
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
    grep -q "\"$1\"" "$CONFIG_DIR/config.json" 2>/dev/null
}

# Enable and start services in dependency order
echo "🚀 Enabling and starting services..."

# Always-on infrastructure services (independent of player.type / menu).
echo "  🌐 Starting HTTP server..."
start_service beo-http.service
echo "  🎮 Starting input server..."
start_service beo-input.service
echo "  🔀 Starting Event Router..."
start_service beo-router.service
echo "  🔗 Starting MasterLink sniffer..."
start_service beo-masterlink.service
echo "  📱 Starting Bluetooth service..."
start_service beo-bluetooth.service

# Player + optional sources reconciled by reconcile-services.sh — single
# source of truth for "which services should be running, given config.json".
echo "  🔄 Reconciling player + source services from config..."
"$SCRIPT_DIR/reconcile-services.sh"

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
    # `|| true`: is-active/is-enabled exit non-zero for inactive/disabled
    # units (normal for optional players/sources), and under `set -e` a
    # failing command substitution in an assignment kills the whole script —
    # which aborted the installer mid status-report, before the verification,
    # summary and reboot prompt ever ran.
    status=$(systemctl is-active "$service" 2>/dev/null || true)
    enabled=$(systemctl is-enabled "$service" 2>/dev/null || true)
    
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
