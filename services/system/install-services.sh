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
)

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="/etc/systemd/system"

echo "📁 Script directory: $SCRIPT_DIR"
echo "📁 Target directory: $SERVICE_DIR"
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
