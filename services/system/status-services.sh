#!/bin/bash

# BeoSound 5C Service Status Script
# Shows the current status of all BeoSound 5C services

echo "🎵 BeoSound 5C Service Status"
echo "============================="

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
    "beo-source-usb.service"
    "beo-source-news.service"
    "beo-ui.service"
)

# Service descriptions
declare -A SERVICE_DESC
SERVICE_DESC["beo-http.service"]="HTTP Web Server (Port 8000)"
SERVICE_DESC["beo-player-sonos.service"]="Sonos Player (Port 8766)"
SERVICE_DESC["beo-player-bluesound.service"]="BlueSound Player (Port 8766)"
SERVICE_DESC["beo-input.service"]="Hardware Input Server (Port 8765)"
SERVICE_DESC["beo-router.service"]="Event Router (Port 8770)"
SERVICE_DESC["beo-masterlink.service"]="MasterLink Sniffer"
SERVICE_DESC["beo-bluetooth.service"]="Bluetooth Remote Service"
SERVICE_DESC["beo-source-cd.service"]="CD Source (Port 8769)"
SERVICE_DESC["beo-source-usb.service"]="USB File Source (Port 8773)"
SERVICE_DESC["beo-source-news.service"]="News Source (Port 8776)"
SERVICE_DESC["beo-ui.service"]="Chromium UI Kiosk"

echo ""
echo "📊 Service Overview:"
echo "==================="

# Track overall status
all_running=true
total_services=${#SERVICES[@]}
running_count=0

for service in "${SERVICES[@]}"; do
    # Get service status
    if systemctl is-active "$service" >/dev/null 2>&1; then
        status="active"
        status_icon="✅"
        ((running_count++))
    else
        status="inactive"
        status_icon="❌"
        all_running=false
    fi
    
    # Get enabled status
    if systemctl is-enabled "$service" >/dev/null 2>&1; then
        enabled="enabled"
        enabled_icon="🔄"
    else
        enabled="disabled"
        enabled_icon="⏸️"
    fi
    
    # Format service name for display
    service_name="${service%.service}"
    description="${SERVICE_DESC[$service]}"
    
    printf "  %s %s %-20s %-35s [%s/%s]\n" "$status_icon" "$enabled_icon" "$service_name" "$description" "$status" "$enabled"
done

echo ""
echo "📈 Summary:"
echo "==========="
if [ "$all_running" = true ]; then
    echo "  🎉 All services are running! ($running_count/$total_services)"
else
    echo "  ⚠️  Some services need attention ($running_count/$total_services running)"
fi

echo ""
echo "🔍 Detailed Status:"
echo "=================="

for service in "${SERVICES[@]}"; do
    echo ""
    echo "--- ${service} ---"
    
    # Show brief status
    systemctl status "$service" --no-pager -l | head -3
    
    # Show recent logs if service is failed
    if ! systemctl is-active "$service" >/dev/null 2>&1; then
        echo "Recent logs:"
        journalctl -u "$service" --no-pager -l -n 3 | tail -3
    fi
done

echo ""
echo "💡 Quick Commands:"
echo "=================="
echo "  View logs:           journalctl -u <service-name> -f"
echo "  Restart service:     sudo systemctl restart <service-name>"
echo "  Stop all services:   sudo systemctl stop beo-*"
echo "  Start all services:  sudo systemctl start beo-*"
echo "  Reload services:     sudo ./install-services.sh"
echo ""
echo "🔄 Auto-refresh: watch -n 2 ./status-services.sh" 