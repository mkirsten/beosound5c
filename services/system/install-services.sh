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
    "beo-spotify.service"
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

echo "📁 Script directory: $SCRIPT_DIR"
echo "📁 Target directory: $SERVICE_DIR"
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
        echo "     For Spotify: open the /setup page on port 8771 after starting beo-spotify"
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
        -addext "subjectAltName=IP:$LOCAL_IP,DNS:$HOSTNAME.local,DNS:$HOSTNAME.kirstenhome" \
        2>/dev/null
    # Service runs as kirsten, needs to read the key
    chown kirsten:kirsten "$SSL_DIR/key.pem" "$SSL_DIR/cert.pem"
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

# Ensure health/notification scripts are executable
echo "📋 Setting up health check and failure notification scripts..."
chmod +x "$SCRIPT_DIR/notify-failure.sh"
chmod +x "$SCRIPT_DIR/beo-health.sh"
echo "  ✅ Scripts made executable"

echo ""

# Enable Debian backports (for latest PipeWire)
BACKPORTS_LIST="/etc/apt/sources.list.d/backports.list"
if [ ! -f "$BACKPORTS_LIST" ]; then
    echo "📋 Enabling Debian backports..."
    echo "deb http://deb.debian.org/debian bookworm-backports main" > "$BACKPORTS_LIST"
    apt update -qq 2>/dev/null
    echo "  ✅ Backports enabled"
fi

# Upgrade PipeWire from backports
echo "📋 Upgrading PipeWire from backports..."
apt install -y -qq -t bookworm-backports pipewire pipewire-pulse pipewire-alsa libspa-0.2-bluetooth 2>/dev/null
echo "  ✅ PipeWire upgraded"

# Install PipeWire RAOP config (AirPlay speaker discovery)
PIPEWIRE_CONF_DIR="/etc/pipewire/pipewire.conf.d"
RAOP_CONF="$PIPEWIRE_CONF_DIR/raop-discover.conf"
if [ ! -f "$RAOP_CONF" ]; then
    echo "📋 Installing PipeWire RAOP discovery config..."
    mkdir -p "$PIPEWIRE_CONF_DIR"
    cat > "$RAOP_CONF" << 'RAOP_EOF'
# Enable AirPlay (RAOP) speaker discovery
# Discovered speakers appear as PipeWire sinks
context.modules = [
    { name = libpipewire-module-raop-discover }
]
RAOP_EOF
    chmod 644 "$RAOP_CONF"
    echo "  ✅ Installed $RAOP_CONF"
else
    echo "  ℹ️  RAOP config already exists at $RAOP_CONF"
fi

# Install CD service dependencies
echo "📋 Installing CD service dependencies..."
apt install -y -qq mpv cdparanoia libdiscid-dev 2>/dev/null
pip3 install --break-system-packages -q discid musicbrainzngs 2>/dev/null
echo "  ✅ CD dependencies installed"

echo ""

# Install USB music auto-mount (NTFS drives exposed via Samba for Sonos)
echo "📋 Setting up USB music auto-mount..."
if [ -f "$SCRIPT_DIR/usb-music-mount.sh" ] && [ -f "$SCRIPT_DIR/99-usb-music.rules" ]; then
    mkdir -p /mnt/usb-music
    cp "$SCRIPT_DIR/usb-music-mount.sh" /usr/local/bin/usb-music-mount.sh
    chmod +x /usr/local/bin/usb-music-mount.sh
    cp "$SCRIPT_DIR/99-usb-music.rules" /etc/udev/rules.d/99-usb-music.rules
    chmod 644 /etc/udev/rules.d/99-usb-music.rules
    udevadm control --reload-rules
    echo "  ✅ Udev rule and mount script installed"
else
    echo "  ⚠️  USB music files not found in $SCRIPT_DIR, skipping"
fi

# Install Samba config for USB music shares
if ! grep -q "USB-Music" /etc/samba/smb.conf 2>/dev/null; then
    echo "📋 Adding USB-Music Samba share..."
    apt install -y -qq samba 2>/dev/null
    cat >> /etc/samba/smb.conf << 'SAMBA_EOF'

[USB-Music]
    comment = Auto-mounted USB music drives
    path = /mnt/usb-music
    read only = yes
    guest ok = yes
    browseable = yes
    follow symlinks = yes
    wide links = yes
SAMBA_EOF
    systemctl restart smbd 2>/dev/null
    echo "  ✅ Samba share configured"
else
    echo "  ℹ️  USB-Music Samba share already configured"
fi

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

# Enable and start services in dependency order
echo "🚀 Enabling and starting services..."

# Start base services first
echo "  🌐 Starting HTTP server..."
systemctl enable beo-http.service
systemctl start beo-http.service

# Determine configured player type from config.json
PLAYER_TYPE=$(python3 -c "import json; print(json.load(open('$CONFIG_DIR/config.json')).get('player',{}).get('type','sonos'))" 2>/dev/null || echo "sonos")
echo "  ℹ️  Configured player type: $PLAYER_TYPE"

if [ "$PLAYER_TYPE" = "sonos" ]; then
    echo "  📡 Starting Sonos player..."
    systemctl enable beo-player-sonos.service
    systemctl start beo-player-sonos.service
    echo "  📡 Disabling BlueSound player (not configured)..."
    systemctl disable beo-player-bluesound.service 2>/dev/null || true
    systemctl stop beo-player-bluesound.service 2>/dev/null || true
elif [ "$PLAYER_TYPE" = "bluesound" ]; then
    echo "  📡 Starting BlueSound player..."
    systemctl enable beo-player-bluesound.service
    systemctl start beo-player-bluesound.service
    echo "  📡 Disabling Sonos player (not configured)..."
    systemctl disable beo-player-sonos.service 2>/dev/null || true
    systemctl stop beo-player-sonos.service 2>/dev/null || true
else
    echo "  ⚠️  Unknown player type '$PLAYER_TYPE', starting both..."
    systemctl enable beo-player-sonos.service
    systemctl start beo-player-sonos.service || true
    systemctl enable beo-player-bluesound.service
    systemctl start beo-player-bluesound.service || true
fi

echo "  🎮 Starting input server..."
systemctl enable beo-input.service
systemctl start beo-input.service

echo "  🔀 Starting Event Router..."
systemctl enable beo-router.service
systemctl start beo-router.service

echo "  🔗 Starting MasterLink sniffer..."
systemctl enable beo-masterlink.service
systemctl start beo-masterlink.service

echo "  📱 Starting Bluetooth service..."
systemctl enable beo-bluetooth.service
systemctl start beo-bluetooth.service

echo "  💿 Starting CD source..."
systemctl enable beo-source-cd.service
systemctl start beo-source-cd.service

echo "  🎵 Starting Spotify source..."
systemctl enable beo-spotify.service
systemctl start beo-spotify.service

echo "  💾 Starting USB source..."
systemctl enable beo-source-usb.service
systemctl start beo-source-usb.service

echo "  📰 Starting News source..."
systemctl enable beo-source-news.service
systemctl start beo-source-news.service

# Start UI service last (depends on HTTP)
echo "  🖥️  Starting UI service..."
systemctl enable beo-ui.service
systemctl start beo-ui.service

# Enable health check timer (auto-recovers failed services every 5 min)
echo "  🩺 Enabling health check timer..."
systemctl enable beo-health.timer
systemctl start beo-health.timer

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
