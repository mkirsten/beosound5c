#!/bin/bash

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
    "beo-media.service"
    "beo-input.service"
    "beo-masterlink.service"
    "beo-bluetooth.service"
    "beo-ui.service"
    "beo-spotify-fetch.service"
    "beo-spotify-fetch.timer"
)

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="/etc/systemd/system"

echo "📁 Script directory: $SCRIPT_DIR"
echo "📁 Target directory: $SERVICE_DIR"
echo ""

# Create configuration directory and copy example if needed
CONFIG_DIR="/etc/beosound5c"
CONFIG_FILE="$CONFIG_DIR/config.env"
CONFIG_EXAMPLE="$SCRIPT_DIR/../config.env.example"

echo "📋 Setting up configuration..."
if [ ! -d "$CONFIG_DIR" ]; then
    echo "  ✅ Creating $CONFIG_DIR"
    mkdir -p "$CONFIG_DIR"
fi

if [ ! -f "$CONFIG_FILE" ]; then
    if [ -f "$CONFIG_EXAMPLE" ]; then
        echo "  ✅ Copying config.env.example to $CONFIG_FILE"
        cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
        chmod 644 "$CONFIG_FILE"
        echo ""
        echo "  ⚠️  IMPORTANT: Edit $CONFIG_FILE to customize for this device!"
        echo "     - DEVICE_NAME: Location identifier (e.g., Kitchen, Loft)"
        echo "     - HA_URL: Home Assistant URL"
        echo "     - BEOREMOTE_MAC: BeoRemote One Bluetooth MAC address"
        echo "     - SONOS_IP: Sonos speaker IP address"
        echo "     - SPOTIFY_USER_ID: Spotify username for playlists"
        echo ""
    else
        echo "  ⚠️  Warning: config.env.example not found at $CONFIG_EXAMPLE"
    fi
else
    echo "  ℹ️  Config file already exists at $CONFIG_FILE"
fi

echo ""

# Ensure we are updated
sudo systemctl daemon-reload
sudo systemctl reset-failed

# Copy service files to systemd directory
echo "📋 Copying service files..."
for service in "${SERVICES[@]}"; do
    if [ -f "$SCRIPT_DIR/$service" ]; then
        echo "  ✅ Copying $service"
        cp "$SCRIPT_DIR/$service" "$SERVICE_DIR/"
        chmod 644 "$SERVICE_DIR/$service"
    else
        echo "  ❌ Warning: $service not found in $SCRIPT_DIR"
    fi
done

echo ""

# Reload systemd daemon
echo "🔄 Reloading systemd daemon..."
systemctl daemon-reload

echo ""

# Enable and start services in dependency order
echo "🚀 Enabling and starting services..."

# Start base services first
echo "  🌐 Starting HTTP server..."
systemctl enable beo-http.service
systemctl start beo-http.service

echo "  📡 Starting media server..."
systemctl enable beo-media.service
systemctl start beo-media.service

echo "  🎮 Starting input server..."
systemctl enable beo-input.service
systemctl start beo-input.service

echo "  🔗 Starting MasterLink sniffer..."
systemctl enable beo-masterlink.service
systemctl start beo-masterlink.service

echo "  📱 Starting Bluetooth service..."
systemctl enable beo-bluetooth.service
systemctl start beo-bluetooth.service

# Start UI service last (depends on HTTP)
echo "  🖥️  Starting UI service..."
systemctl enable beo-ui.service
systemctl start beo-ui.service

# Enable Spotify playlist fetch timer (runs nightly at 3am)
echo "  🎵 Enabling Spotify playlist fetch timer..."
systemctl enable beo-spotify-fetch.timer
systemctl start beo-spotify-fetch.timer

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
