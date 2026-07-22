#!/bin/bash

# BeoSound 5C Service Uninstallation Script
# This script stops, disables, and removes all BeoSound 5C services

echo "🛑 BeoSound 5C Service Uninstallation Script"
echo "============================================"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ This script must be run as root (use sudo)"
    exit 1
fi

# Service list comes from the registry (single source of truth), removed in
# reverse install order so beo-ui and sources stop before the backends.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/service-registry.sh"

SERVICES=()
for ((i=${#ALL_SERVICES[@]}-1; i>=0; i--)); do
    SERVICES+=("${ALL_SERVICES[$i]}")
done

SERVICE_DIR="/etc/systemd/system"

echo "📁 Target directory: $SERVICE_DIR"
echo ""

# Stop and disable services in reverse dependency order
echo "🛑 Stopping and disabling services..."

for service in "${SERVICES[@]}"; do
    echo "  ⏹️  Stopping $service..."
    systemctl stop "$service" 2>/dev/null
    
    echo "  ❌ Disabling $service..."
    systemctl disable "$service" 2>/dev/null
    
    echo "  🗑️  Removing $service..."
    rm -f "$SERVICE_DIR/$service"
done

echo ""

# Reload systemd daemon
echo "🔄 Reloading systemd daemon..."
systemctl daemon-reload

echo ""

# Reset failed services
echo "🧹 Resetting failed service states..."
systemctl reset-failed

echo ""

# Check if any services are still running
echo "📊 Final Status Check:"
echo "====================="
remaining_services=$(systemctl list-units --type=service --state=active | grep "beo-" | wc -l)

if [ "$remaining_services" -eq 0 ]; then
    echo "  ✅ All BeoSound 5C services have been removed"
else
    echo "  ⚠️  Some services may still be running:"
    systemctl list-units --type=service --state=active | grep "beo-"
fi

echo ""
echo "🎉 Uninstallation complete!"
echo ""
echo "💡 To reinstall services, run: sudo ./install-services.sh" 