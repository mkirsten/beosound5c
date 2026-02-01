#!/bin/bash
# =============================================================================
# BeoSound 5c Installation Script
# =============================================================================
# Takes a vanilla Raspberry Pi 5 running Raspberry Pi OS to a fully
# operational BeoSound 5c system with interactive configuration.
#
# Usage: sudo ./install.sh [--user USERNAME]
#
# Options:
#   --user USERNAME    Install for specified user (default: $SUDO_USER)
#
# =============================================================================
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_section() { echo -e "\n${CYAN}=== $* ===${NC}\n"; }

# =============================================================================
# Configuration
# =============================================================================

# Parse command line arguments
INSTALL_USER="${SUDO_USER:-$(whoami)}"
while [[ $# -gt 0 ]]; do
    case $1 in
        --user)
            INSTALL_USER="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: sudo $0 [--user USERNAME]"
            echo ""
            echo "Options:"
            echo "  --user USERNAME    Install for specified user (default: \$SUDO_USER)"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

INSTALL_DIR="/home/$INSTALL_USER/beosound5c"
CONFIG_DIR="/etc/beosound5c"
CONFIG_FILE="$CONFIG_DIR/config.env"
PLYMOUTH_THEME_DIR="/usr/share/plymouth/themes/beosound5c"

# =============================================================================
# Pre-flight Checks
# =============================================================================
log_section "Pre-flight Checks"

# Must run as root
if [ "$EUID" -ne 0 ]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi
log_success "Running as root"

# Check if user exists
if ! id "$INSTALL_USER" &>/dev/null; then
    log_error "User '$INSTALL_USER' does not exist"
    exit 1
fi
log_success "User '$INSTALL_USER' exists"

# Check if running on Raspberry Pi 5
if [ -f /proc/device-tree/model ]; then
    MODEL=$(cat /proc/device-tree/model)
    if [[ "$MODEL" == *"Raspberry Pi 5"* ]]; then
        log_success "Raspberry Pi 5 detected: $MODEL"
    else
        log_warn "Not a Raspberry Pi 5: $MODEL"
        log_warn "Some features may not work correctly"
    fi
else
    log_warn "Could not detect hardware model - proceeding anyway"
fi

# Check internet connectivity
if ping -c 1 -W 5 google.com &>/dev/null; then
    log_success "Internet connectivity confirmed"
else
    log_error "No internet connectivity - required for package installation"
    exit 1
fi

# Check if install directory exists
if [ ! -d "$INSTALL_DIR" ]; then
    log_error "Installation directory not found: $INSTALL_DIR"
    log_error "Please clone the repository first:"
    log_error "  git clone <repo-url> $INSTALL_DIR"
    exit 1
fi
log_success "Installation directory found: $INSTALL_DIR"

# =============================================================================
# System Package Installation
# =============================================================================
log_section "Installing System Packages"

log_info "Updating package lists..."
apt-get update -qq

log_info "Installing X11 and display packages..."
apt-get install -y --no-install-recommends \
    xserver-xorg \
    x11-xserver-utils \
    xinit \
    openbox \
    chromium-browser \
    fbi \
    feh \
    unclutter-xfixes

log_info "Installing Python packages..."
apt-get install -y \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    libhidapi-hidraw0 \
    libhidapi-dev \
    python3-hidapi \
    python3-hid \
    python3-websockets \
    python3-websocket

log_info "Installing USB and Bluetooth packages..."
apt-get install -y \
    libudev-dev \
    libusb-1.0-0-dev \
    bluetooth \
    bluez

log_info "Installing Plymouth (boot splash)..."
apt-get install -y \
    plymouth \
    plymouth-themes

log_info "Installing utilities..."
apt-get install -y \
    curl \
    git \
    jq

log_success "System packages installed"

# =============================================================================
# Python Package Installation
# =============================================================================
log_section "Installing Python Packages"

log_info "Installing Python packages via pip..."
pip3 install --break-system-packages \
    'soco>=0.30.0' \
    'pillow>=10.0.0' \
    'requests>=2.31.0' \
    'websockets>=12.0' \
    'websocket-client>=1.6.0' \
    'aiohttp>=3.9.0' \
    'pyusb>=1.2.1'

log_success "Python packages installed"

# =============================================================================
# udev Rules
# =============================================================================
log_section "Configuring udev Rules"

UDEV_RULES_FILE="/etc/udev/rules.d/99-bs5.rules"

log_info "Creating udev rules for BeoSound 5 hardware..."
cat > "$UDEV_RULES_FILE" << 'EOF'
# BeoSound 5 USB HID device (rotary encoder + buttons)
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", \
  ATTRS{idVendor}=="0cd4", ATTRS{idProduct}=="1112", \
  MODE="0666"

# BeoSound 5 PC2/MasterLink interface
SUBSYSTEM=="usb", ATTR{idVendor}=="0cd4", ATTR{idProduct}=="0101", \
  MODE="0666"

# TTY devices - allow access for X11 kiosk mode
KERNEL=="tty[0-9]*", MODE="0666"
EOF

log_info "Reloading udev rules..."
udevadm control --reload-rules
udevadm trigger

log_success "udev rules configured"

# =============================================================================
# User Groups
# =============================================================================
log_section "Configuring User Groups"

log_info "Adding $INSTALL_USER to required groups..."
usermod -aG video,input,bluetooth,dialout,tty "$INSTALL_USER"

log_success "User added to groups: video, input, bluetooth, dialout, tty"

# =============================================================================
# Boot Configuration
# =============================================================================
log_section "Configuring Boot Settings"

BOOT_CONFIG="/boot/firmware/config.txt"
BOOT_CMDLINE="/boot/firmware/cmdline.txt"

# Check for alternative boot paths (older Pi OS versions)
if [ ! -f "$BOOT_CONFIG" ]; then
    BOOT_CONFIG="/boot/config.txt"
fi
if [ ! -f "$BOOT_CMDLINE" ]; then
    BOOT_CMDLINE="/boot/cmdline.txt"
fi

# Add HDMI settings if not already present
if ! grep -q "# BeoSound 5c Panel Settings" "$BOOT_CONFIG" 2>/dev/null; then
    log_info "Adding HDMI configuration to $BOOT_CONFIG..."
    cat >> "$BOOT_CONFIG" << 'EOF'

# BeoSound 5c Panel Settings
hdmi_force_hotplug=1
disable_overscan=1
hdmi_group=2
hdmi_mode=16
hdmi_drive=2
framebuffer_width=1024
framebuffer_height=768
EOF
    log_success "HDMI configuration added"
else
    log_info "HDMI configuration already present in $BOOT_CONFIG"
fi

# Add Plymouth boot parameters if not already present
if ! grep -q "quiet splash" "$BOOT_CMDLINE" 2>/dev/null; then
    log_info "Adding Plymouth boot parameters to $BOOT_CMDLINE..."
    # Read current cmdline and append parameters
    CURRENT_CMDLINE=$(cat "$BOOT_CMDLINE")
    echo "$CURRENT_CMDLINE quiet splash plymouth.ignore-serial-consoles" > "$BOOT_CMDLINE"
    log_success "Plymouth boot parameters added"
else
    log_info "Plymouth boot parameters already present in $BOOT_CMDLINE"
fi

# =============================================================================
# X11 Configuration
# =============================================================================
log_section "Configuring X11"

# Allow any user to start X server (required for systemd service)
log_info "Configuring X11 wrapper permissions..."
cat > /etc/X11/Xwrapper.config << 'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
log_success "X11 wrapper configured"

# Remove any conflicting .xinitrc files that might interfere with beo-ui
XINITRC_FILE="/home/$INSTALL_USER/.xinitrc"
if [ -f "$XINITRC_FILE" ]; then
    log_info "Found existing .xinitrc - backing up to .xinitrc.bak"
    mv "$XINITRC_FILE" "${XINITRC_FILE}.bak"
fi

# =============================================================================
# Plymouth Theme Installation
# =============================================================================
log_section "Installing Plymouth Boot Theme"

SPLASH_SOURCE="$INSTALL_DIR/assets/splashscreen-red.png"
PLYMOUTH_SOURCE="$INSTALL_DIR/plymouth"

if [ -d "$PLYMOUTH_SOURCE" ] && [ -f "$SPLASH_SOURCE" ]; then
    log_info "Creating Plymouth theme directory..."
    mkdir -p "$PLYMOUTH_THEME_DIR"

    log_info "Copying Plymouth theme files..."
    cp "$PLYMOUTH_SOURCE/beosound5c.plymouth" "$PLYMOUTH_THEME_DIR/"
    cp "$PLYMOUTH_SOURCE/beosound5c.script" "$PLYMOUTH_THEME_DIR/"
    cp "$SPLASH_SOURCE" "$PLYMOUTH_THEME_DIR/"

    log_info "Setting Plymouth theme as default..."
    plymouth-set-default-theme beosound5c

    log_info "Updating initramfs (this may take a moment)..."
    update-initramfs -u

    log_success "Plymouth theme installed"
else
    log_warn "Plymouth theme files not found - skipping"
    log_warn "Expected: $PLYMOUTH_SOURCE and $SPLASH_SOURCE"
fi

# =============================================================================
# Interactive Configuration
# =============================================================================
log_section "Configuration"

# Create config directory
mkdir -p "$CONFIG_DIR"

# Check if config already exists
if [ -f "$CONFIG_FILE" ]; then
    log_info "Configuration file already exists at $CONFIG_FILE"
    read -p "Do you want to reconfigure? (y/N): " RECONFIGURE
    if [[ ! "$RECONFIGURE" =~ ^[Yy]$ ]]; then
        log_info "Keeping existing configuration"
    else
        rm -f "$CONFIG_FILE"
    fi
fi

# Interactive configuration if file doesn't exist
if [ ! -f "$CONFIG_FILE" ]; then
    log_info "Let's configure your BeoSound 5c installation..."
    echo ""

    # Device name
    read -p "Device name/location (e.g., Living Room, Kitchen): " DEVICE_NAME
    DEVICE_NAME="${DEVICE_NAME:-BeoSound5c}"

    # Sonos IP
    read -p "Sonos speaker IP address (e.g., 192.168.1.100): " SONOS_IP
    SONOS_IP="${SONOS_IP:-192.168.1.100}"

    # Home Assistant URL
    read -p "Home Assistant URL (e.g., http://homeassistant.local:8123): " HA_URL
    HA_URL="${HA_URL:-http://homeassistant.local:8123}"

    # Home Assistant webhook URL
    DEFAULT_WEBHOOK="${HA_URL}/api/webhook/beosound5c"
    read -p "Home Assistant webhook URL [$DEFAULT_WEBHOOK]: " HA_WEBHOOK_URL
    HA_WEBHOOK_URL="${HA_WEBHOOK_URL:-$DEFAULT_WEBHOOK}"

    # Home Assistant token (optional)
    echo ""
    log_info "Home Assistant Long-Lived Access Token is optional but recommended"
    log_info "Create one at: HA -> Profile -> Long-Lived Access Tokens"
    read -p "Home Assistant token (press Enter to skip): " HA_TOKEN

    # BeoRemote MAC (optional)
    echo ""
    log_info "BeoRemote One Bluetooth MAC is optional"
    log_info "Find with: bluetoothctl -> scan on -> look for 'BeoRemote One'"
    read -p "BeoRemote One MAC address (press Enter to skip): " BEOREMOTE_MAC
    BEOREMOTE_MAC="${BEOREMOTE_MAC:-00:00:00:00:00:00}"

    # Spotify user ID (optional)
    echo ""
    read -p "Spotify user ID for playlists (press Enter to skip): " SPOTIFY_USER_ID

    # Write configuration file
    log_info "Writing configuration to $CONFIG_FILE..."
    cat > "$CONFIG_FILE" << EOF
# BeoSound 5c Configuration
# Generated by install.sh on $(date)

# =============================================================================
# Device Configuration
# =============================================================================

# Location identifier (sent to Home Assistant webhooks)
DEVICE_NAME="$DEVICE_NAME"

# Base path for BeoSound 5c installation
BS5C_BASE_PATH="$INSTALL_DIR"

# =============================================================================
# Sonos Configuration
# =============================================================================

# Sonos speaker IP address
SONOS_IP="$SONOS_IP"

# =============================================================================
# Home Assistant Configuration
# =============================================================================

# Home Assistant base URL
HA_URL="$HA_URL"

# Home Assistant webhook URL for BeoSound 5c events
HA_WEBHOOK_URL="$HA_WEBHOOK_URL"

# Home Assistant Long-Lived Access Token (for API access)
HA_TOKEN="$HA_TOKEN"

# =============================================================================
# Optional Hardware Configuration
# =============================================================================

# BeoRemote One Bluetooth MAC address
BEOREMOTE_MAC="$BEOREMOTE_MAC"

# Spotify user ID for playlist fetching
SPOTIFY_USER_ID="$SPOTIFY_USER_ID"
EOF

    chmod 644 "$CONFIG_FILE"
    log_success "Configuration saved to $CONFIG_FILE"
fi

# Update config.env.example with dynamic username in BS5C_BASE_PATH
EXAMPLE_CONFIG="$INSTALL_DIR/services/config.env.example"
if [ -f "$EXAMPLE_CONFIG" ]; then
    sed -i "s|BS5C_BASE_PATH=\"/home/[^\"]*\"|BS5C_BASE_PATH=\"$INSTALL_DIR\"|g" "$EXAMPLE_CONFIG"
fi

# =============================================================================
# Service Installation
# =============================================================================
log_section "Installing System Services"

SERVICE_SCRIPT="$INSTALL_DIR/services/system/install-services.sh"

if [ -f "$SERVICE_SCRIPT" ]; then
    log_info "Running service installation script..."

    # Update service files with correct user if not 'kirsten'
    if [ "$INSTALL_USER" != "kirsten" ]; then
        log_info "Updating service files for user: $INSTALL_USER"
        for service_file in "$INSTALL_DIR/services/system/"*.service; do
            if [ -f "$service_file" ]; then
                sed -i "s|User=kirsten|User=$INSTALL_USER|g" "$service_file"
                sed -i "s|Group=kirsten|Group=$INSTALL_USER|g" "$service_file"
                sed -i "s|/home/kirsten|/home/$INSTALL_USER|g" "$service_file"
            fi
        done
    fi

    bash "$SERVICE_SCRIPT"
    log_success "Services installed"
else
    log_warn "Service installation script not found: $SERVICE_SCRIPT"
    log_warn "You may need to install services manually"
fi

# =============================================================================
# Verification
# =============================================================================
log_section "Verification"

FAILED_CHECKS=0

# Check apt packages
log_info "Checking installed packages..."
REQUIRED_PACKAGES="chromium-browser python3 python3-hidapi bluetooth plymouth"
for pkg in $REQUIRED_PACKAGES; do
    if dpkg -l "$pkg" &>/dev/null; then
        log_success "Package installed: $pkg"
    else
        log_error "Package missing: $pkg"
        ((FAILED_CHECKS++))
    fi
done

# Check udev rules
if [ -f "$UDEV_RULES_FILE" ]; then
    log_success "udev rules installed: $UDEV_RULES_FILE"
else
    log_error "udev rules missing"
    ((FAILED_CHECKS++))
fi

# Check user groups
USER_GROUPS=$(groups "$INSTALL_USER")
for grp in video input bluetooth tty; do
    if echo "$USER_GROUPS" | grep -q "\b$grp\b"; then
        log_success "User in group: $grp"
    else
        log_error "User not in group: $grp"
        ((FAILED_CHECKS++))
    fi
done

# Check X11 wrapper config
if [ -f "/etc/X11/Xwrapper.config" ]; then
    log_success "X11 wrapper config exists"
else
    log_warn "X11 wrapper config missing"
fi

# Check Plymouth theme
if plymouth-set-default-theme | grep -q "beosound5c"; then
    log_success "Plymouth theme set: beosound5c"
else
    log_warn "Plymouth theme not set as default"
fi

# Check config file
if [ -f "$CONFIG_FILE" ]; then
    log_success "Configuration file exists: $CONFIG_FILE"
else
    log_error "Configuration file missing"
    ((FAILED_CHECKS++))
fi

# Check services (if they're supposed to be running)
log_info "Checking service status..."
SERVICES="beo-http beo-media beo-input beo-ui"
for svc in $SERVICES; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        log_success "Service running: $svc"
    else
        status=$(systemctl is-active "$svc" 2>/dev/null || echo "not-found")
        log_warn "Service not running: $svc ($status)"
    fi
done

# Test HTTP server
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ 2>/dev/null | grep -q "200"; then
    log_success "HTTP server responding on port 8000"
else
    log_warn "HTTP server not responding (may need reboot)"
fi

# =============================================================================
# Summary
# =============================================================================
log_section "Installation Complete"

if [ $FAILED_CHECKS -eq 0 ]; then
    log_success "All checks passed!"
else
    log_warn "$FAILED_CHECKS check(s) failed - review above"
fi

echo ""
echo "Configuration file: $CONFIG_FILE"
echo "Installation directory: $INSTALL_DIR"
echo "Plymouth theme: $PLYMOUTH_THEME_DIR"
echo ""
echo "Next steps:"
echo "  1. Review configuration: sudo nano $CONFIG_FILE"
echo "  2. Reboot to apply all changes: sudo reboot"
echo "  3. After reboot, check services: systemctl status beo-*"
echo ""
echo "Useful commands:"
echo "  View logs:          journalctl -u beo-ui -f"
echo "  Restart services:   sudo systemctl restart beo-*"
echo "  Service status:     $INSTALL_DIR/services/system/status-services.sh"
echo ""

if [ $FAILED_CHECKS -gt 0 ]; then
    exit 1
fi
