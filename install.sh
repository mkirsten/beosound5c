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

# Display banner
show_banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}____             ____                      _${NC}       ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}| __ )  ___  ___|  __| ___  _   _ _ __   __| |${NC}      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}|  _ \\ / _ \\/ _ \\|__  |/ _ \\| | | | '_ \\ / _\` |${NC}      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}| |_) |  __/ (_) |__) | (_) | |_| | | | | (_| |${NC}      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}       ${GREEN}|____/ \\___|\\___/|____/\\___/ \\__,_|_| |_|\\__,_|${NC}      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                            ${YELLOW}5 c${NC}                            ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                  ${BLUE}Installation & Setup${NC}                     ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

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

# Show welcome banner
show_banner

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
    x11-utils \
    xdotool \
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
# Network Discovery Functions
# =============================================================================

# Scan for Sonos devices on the network
scan_sonos_devices() {
    log_info "Scanning for Sonos devices on the network..."
    local sonos_devices=()
    local timeout=2

    # Method 1: Try avahi/mDNS discovery (most reliable)
    if command -v avahi-browse &>/dev/null; then
        while IFS= read -r line; do
            # Parse avahi-browse output for Sonos devices
            if [[ "$line" =~ ([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+) ]]; then
                local ip="${BASH_REMATCH[1]}"
                # Verify it's actually a Sonos device by checking port 1400
                if timeout $timeout bash -c "echo >/dev/tcp/$ip/1400" 2>/dev/null; then
                    # Try to get device name from Sonos API
                    local name=$(curl -s --connect-timeout $timeout "http://$ip:1400/xml/device_description.xml" 2>/dev/null | grep -oP '(?<=<roomName>)[^<]+' | head -1)
                    if [ -n "$name" ]; then
                        sonos_devices+=("$ip|$name")
                    else
                        sonos_devices+=("$ip|Sonos Device")
                    fi
                fi
            fi
        done < <(avahi-browse -rtp _sonos._tcp 2>/dev/null | grep "=" | head -10)
    fi

    # Method 2: Fallback - scan common ports if avahi didn't find anything
    if [ ${#sonos_devices[@]} -eq 0 ]; then
        # Get local network range
        local network=$(ip route | grep -oP 'src \K[0-9.]+' | head -1 | sed 's/\.[0-9]*$/./')
        if [ -n "$network" ]; then
            log_info "Scanning network ${network}0/24 for Sonos devices (port 1400)..."
            for i in $(seq 1 254); do
                local ip="${network}${i}"
                if timeout $timeout bash -c "echo >/dev/tcp/$ip/1400" 2>/dev/null; then
                    local name=$(curl -s --connect-timeout $timeout "http://$ip:1400/xml/device_description.xml" 2>/dev/null | grep -oP '(?<=<roomName>)[^<]+' | head -1)
                    if [ -n "$name" ]; then
                        sonos_devices+=("$ip|$name")
                    fi
                fi
            done &
            # Run scan in background with timeout
            local scan_pid=$!
            sleep 10
            kill $scan_pid 2>/dev/null
            wait $scan_pid 2>/dev/null
        fi
    fi

    # Return results
    if [ ${#sonos_devices[@]} -gt 0 ]; then
        printf '%s\n' "${sonos_devices[@]}"
    fi
}

# Detect Home Assistant on the network
detect_home_assistant() {
    log_info "Looking for Home Assistant..."
    local ha_urls=()
    local timeout=3

    # Method 1: Try homeassistant.local first (most common)
    if curl -s --connect-timeout $timeout -o /dev/null -w "%{http_code}" "http://homeassistant.local:8123/api/" 2>/dev/null | grep -qE "^(200|401|403)$"; then
        ha_urls+=("http://homeassistant.local:8123")
        log_success "Found Home Assistant at homeassistant.local:8123"
    fi

    # Method 2: Try common hostnames
    for hostname in "home-assistant.local" "hass.local" "ha.local"; do
        if [ ${#ha_urls[@]} -eq 0 ]; then
            if curl -s --connect-timeout $timeout -o /dev/null -w "%{http_code}" "http://${hostname}:8123/api/" 2>/dev/null | grep -qE "^(200|401|403)$"; then
                ha_urls+=("http://${hostname}:8123")
                log_success "Found Home Assistant at ${hostname}:8123"
            fi
        fi
    done

    # Method 3: Scan local network for port 8123
    if [ ${#ha_urls[@]} -eq 0 ]; then
        local network=$(ip route | grep -oP 'src \K[0-9.]+' | head -1 | sed 's/\.[0-9]*$/./')
        if [ -n "$network" ]; then
            log_info "Scanning network ${network}0/24 for Home Assistant (port 8123)..."
            for i in $(seq 1 254); do
                local ip="${network}${i}"
                if timeout $timeout bash -c "echo >/dev/tcp/$ip/8123" 2>/dev/null; then
                    # Verify it's actually Home Assistant
                    if curl -s --connect-timeout $timeout "http://$ip:8123/api/" 2>/dev/null | grep -q "API running"; then
                        ha_urls+=("http://$ip:8123")
                        log_success "Found Home Assistant at $ip:8123"
                        break
                    fi
                fi
            done
        fi
    fi

    # Return results
    if [ ${#ha_urls[@]} -gt 0 ]; then
        printf '%s\n' "${ha_urls[@]}"
    fi
}

# Display menu and get user selection
select_from_list() {
    local prompt="$1"
    shift
    local options=("$@")
    local count=${#options[@]}

    if [ $count -eq 0 ]; then
        return 1
    fi

    echo ""
    echo "$prompt"
    for i in "${!options[@]}"; do
        echo "  $((i+1))) ${options[$i]}"
    done
    echo "  $((count+1))) Enter manually"
    echo ""

    while true; do
        read -p "Select option [1-$((count+1))]: " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le $((count+1)) ]; then
            if [ "$choice" -eq $((count+1)) ]; then
                return 1  # User wants manual entry
            else
                echo "${options[$((choice-1))]}"
                return 0
            fi
        fi
        echo "Invalid selection. Please enter a number between 1 and $((count+1))."
    done
}

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

    # -------------------------------------------------------------------------
    # Device name
    # -------------------------------------------------------------------------
    read -p "Device name/location (e.g., Living Room, Kitchen): " DEVICE_NAME
    DEVICE_NAME="${DEVICE_NAME:-BeoSound5c}"

    # -------------------------------------------------------------------------
    # Sonos IP - with network discovery
    # -------------------------------------------------------------------------
    echo ""
    log_section "Sonos Configuration"

    # Scan for Sonos devices
    mapfile -t sonos_results < <(scan_sonos_devices)

    if [ ${#sonos_results[@]} -gt 0 ]; then
        # Format results for display
        sonos_display=()
        sonos_ips=()
        for result in "${sonos_results[@]}"; do
            ip=$(echo "$result" | cut -d'|' -f1)
            name=$(echo "$result" | cut -d'|' -f2)
            sonos_display+=("$name ($ip)")
            sonos_ips+=("$ip")
        done

        log_success "Found ${#sonos_results[@]} Sonos device(s)!"

        if selection=$(select_from_list "Select Sonos speaker to control:" "${sonos_display[@]}"); then
            # Extract IP from selection
            SONOS_IP=$(echo "$selection" | grep -oP '\(([0-9.]+)\)' | tr -d '()')
        else
            read -p "Enter Sonos speaker IP address: " SONOS_IP
        fi
    else
        log_warn "No Sonos devices found on the network"
        log_info "Make sure your Sonos speaker is powered on and connected to the same network"
        read -p "Enter Sonos speaker IP address: " SONOS_IP
    fi
    SONOS_IP="${SONOS_IP:-192.168.1.100}"
    log_success "Sonos IP: $SONOS_IP"

    # -------------------------------------------------------------------------
    # Home Assistant URL - with network discovery
    # -------------------------------------------------------------------------
    echo ""
    log_section "Home Assistant Configuration"

    # Detect Home Assistant
    mapfile -t ha_results < <(detect_home_assistant)

    if [ ${#ha_results[@]} -gt 0 ]; then
        if [ ${#ha_results[@]} -eq 1 ]; then
            HA_URL="${ha_results[0]}"
            log_success "Using detected Home Assistant: $HA_URL"
        else
            if selection=$(select_from_list "Select Home Assistant instance:" "${ha_results[@]}"); then
                HA_URL="$selection"
            else
                read -p "Enter Home Assistant URL (e.g., http://homeassistant.local:8123): " HA_URL
            fi
        fi
    else
        log_warn "Home Assistant not found automatically"
        read -p "Enter Home Assistant URL (e.g., http://homeassistant.local:8123): " HA_URL
    fi
    HA_URL="${HA_URL:-http://homeassistant.local:8123}"
    log_success "Home Assistant URL: $HA_URL"

    # Home Assistant webhook URL
    DEFAULT_WEBHOOK="${HA_URL}/api/webhook/beosound5c"
    read -p "Home Assistant webhook URL [$DEFAULT_WEBHOOK]: " HA_WEBHOOK_URL
    HA_WEBHOOK_URL="${HA_WEBHOOK_URL:-$DEFAULT_WEBHOOK}"

    # Home Assistant security dashboard (for SECURITY page)
    echo ""
    log_info "Home Assistant Dashboard for SECURITY Page (Optional)"
    echo ""
    echo "The SECURITY menu item can display a Home Assistant dashboard (e.g., camera feeds)."
    echo "Enter the dashboard path without leading slash."
    echo "Examples: lovelace-cameras/0, dashboard-cameras/home"
    echo ""
    read -p "HA dashboard for SECURITY page (press Enter to skip): " HA_SECURITY_DASHBOARD
    if [ -n "$HA_SECURITY_DASHBOARD" ]; then
        log_success "Security dashboard: $HA_SECURITY_DASHBOARD"
    else
        log_info "No security dashboard configured - SECURITY page will be empty"
    fi

    # -------------------------------------------------------------------------
    # Home Assistant Token - with detailed instructions
    # -------------------------------------------------------------------------
    echo ""
    log_info "Home Assistant Long-Lived Access Token"
    echo ""
    echo "A token is recommended for features like Apple TV status and camera feeds."
    echo ""
    echo "To create a token:"
    echo "  1. Open Home Assistant in your browser: ${HA_URL}"
    echo "  2. Click your profile icon (bottom-left corner)"
    echo "  3. Scroll down to 'Long-Lived Access Tokens'"
    echo "  4. Click 'Create Token'"
    echo "  5. Name it 'BeoSound 5c' and click 'OK'"
    echo "  6. Copy the token (you won't be able to see it again!)"
    echo ""
    echo "Direct link: ${HA_URL}/profile/security"
    echo ""
    read -p "Paste your Home Assistant token (or press Enter to skip): " HA_TOKEN

    if [ -z "$HA_TOKEN" ]; then
        log_warn "No token provided - some features will be unavailable"
        log_info "You can add a token later by editing: $CONFIG_FILE"
    else
        log_success "Token configured"
    fi

    # -------------------------------------------------------------------------
    # Bluetooth Remote Setup
    # -------------------------------------------------------------------------
    echo ""
    log_section "Bluetooth Remote (Optional)"
    echo ""
    echo "The BeoRemote One works out of the box in IR mode (point at the device)."
    echo "For Bluetooth mode (works from anywhere), you need to pair the remote."
    echo ""
    read -p "Do you want to set up a BeoRemote One via Bluetooth? (y/N): " SETUP_BLUETOOTH

    BEOREMOTE_MAC="00:00:00:00:00:00"
    BT_DEVICE_NAME=""
    BLUETOOTH_PAIRED=false

    if [[ "$SETUP_BLUETOOTH" =~ ^[Yy]$ ]]; then
        # -------------------------------------------------------------------------
        # Device name shown on BeoRemote One
        # -------------------------------------------------------------------------
        echo ""
        log_info "Device Name on BeoRemote One"
        echo ""
        echo "Choose how this BeoSound 5c will appear on the remote's display."
        echo "Examples: BeoVision Frame, Sonos, Spotify, HomeAssistant, Great Room"
        echo ""

        DEFAULT_BT_NAME="BeoSound 5c"
        if [ -n "$DEVICE_NAME" ] && [ "$DEVICE_NAME" != "BeoSound5c" ]; then
            DEFAULT_BT_NAME="$DEVICE_NAME"
        fi
        read -p "Device name on remote [$DEFAULT_BT_NAME]: " BT_DEVICE_NAME
        BT_DEVICE_NAME="${BT_DEVICE_NAME:-$DEFAULT_BT_NAME}"

        # -------------------------------------------------------------------------
        # Pairing Instructions
        # -------------------------------------------------------------------------
        while true; do
            echo ""
            log_section "BeoRemote One Pairing"
            echo ""
            echo -e "${YELLOW}Please prepare your BeoRemote One for pairing:${NC}"
            echo ""
            echo "  If this is a NEW remote (never paired):"
            echo "    → It will automatically be in pairing mode when powered on"
            echo ""
            echo "  If the remote was previously paired:"
            echo "    1. Press the LIST button"
            echo "    2. Use UP/DOWN to select SETTINGS, press the center button"
            echo "    3. Select PAIRING, press the center button"
            echo "    4. Select PAIR, press the center button"
            echo ""
            echo "  The remote display should show 'Open for pairing'"
            echo ""
            read -p "Press Enter when the remote is ready for pairing (or 's' to skip): " PAIRING_READY

            if [[ "$PAIRING_READY" =~ ^[Ss]$ ]]; then
                log_info "Skipping Bluetooth pairing"
                break
            fi

            # -------------------------------------------------------------------------
            # Bluetooth Scanning and Pairing
            # -------------------------------------------------------------------------
            echo ""
            log_info "Starting Bluetooth scan..."
            echo ""

            # Ensure Bluetooth is powered on
            bluetoothctl power on &>/dev/null
            sleep 1

            # Start scanning in background and look for BEORC
            echo -n "  Scanning for BeoRemote One "

            # Start scan
            bluetoothctl --timeout 2 scan on &>/dev/null &

            SCAN_ATTEMPTS=0
            MAX_SCAN_ATTEMPTS=15
            FOUND_MAC=""

            while [ $SCAN_ATTEMPTS -lt $MAX_SCAN_ATTEMPTS ]; do
                echo -n "."
                sleep 2

                # Look for BEORC device
                FOUND_MAC=$(bluetoothctl devices | grep -i "BEORC" | awk '{print $2}' | head -1)

                if [ -n "$FOUND_MAC" ]; then
                    echo ""
                    log_success "Found BeoRemote One: $FOUND_MAC"
                    break
                fi

                ((SCAN_ATTEMPTS++))
            done

            # Stop scanning
            bluetoothctl scan off &>/dev/null

            if [ -z "$FOUND_MAC" ]; then
                echo ""
                log_warn "BeoRemote One not found"
                echo ""
                echo "  [r] Retry scanning"
                echo "  [s] Skip Bluetooth setup"
                echo ""
                read -p "Choose an option: " RETRY_OPTION

                if [[ "$RETRY_OPTION" =~ ^[Ss]$ ]]; then
                    log_info "Skipping Bluetooth pairing"
                    break
                fi
                continue
            fi

            # -------------------------------------------------------------------------
            # Pair and Trust
            # -------------------------------------------------------------------------
            echo ""
            log_info "Pairing with BeoRemote One..."
            echo -n "  Pairing "

            # Attempt to pair
            PAIR_OUTPUT=$(bluetoothctl pair "$FOUND_MAC" 2>&1) &
            PAIR_PID=$!

            for i in {1..10}; do
                echo -n "."
                sleep 1
            done
            echo ""

            # Check if pairing was successful
            if bluetoothctl info "$FOUND_MAC" 2>/dev/null | grep -q "Paired: yes"; then
                log_success "Paired successfully!"

                # Trust the device
                echo -n "  Trusting device "
                bluetoothctl trust "$FOUND_MAC" &>/dev/null
                for i in {1..3}; do
                    echo -n "."
                    sleep 1
                done
                echo ""
                log_success "Device trusted"

                # Try to connect
                echo -n "  Connecting "
                bluetoothctl connect "$FOUND_MAC" &>/dev/null &
                for i in {1..5}; do
                    echo -n "."
                    sleep 1
                done
                echo ""

                if bluetoothctl info "$FOUND_MAC" 2>/dev/null | grep -q "Connected: yes"; then
                    log_success "Connected!"
                else
                    log_info "Connection will be established when remote is used"
                fi

                BEOREMOTE_MAC="$FOUND_MAC"

                # -------------------------------------------------------------------------
                # Verify with user
                # -------------------------------------------------------------------------
                echo ""
                echo -e "${GREEN}Pairing appears successful!${NC}"
                echo ""
                echo "Please check your BeoRemote One display."
                echo "It should show '$BT_DEVICE_NAME' as a paired device."
                echo ""
                read -p "Does the remote show the pairing was successful? (Y/n): " PAIRING_CONFIRMED

                if [[ ! "$PAIRING_CONFIRMED" =~ ^[Nn]$ ]]; then
                    log_success "BeoRemote One paired successfully!"
                    BLUETOOTH_PAIRED=true
                    break
                else
                    echo ""
                    echo "  [r] Retry pairing"
                    echo "  [s] Skip Bluetooth setup"
                    echo ""
                    read -p "Choose an option: " RETRY_OPTION

                    if [[ "$RETRY_OPTION" =~ ^[Ss]$ ]]; then
                        log_info "Skipping Bluetooth pairing"
                        BEOREMOTE_MAC="00:00:00:00:00:00"
                        break
                    fi
                    # Remove failed pairing
                    bluetoothctl remove "$FOUND_MAC" &>/dev/null
                    continue
                fi
            else
                log_warn "Pairing failed"
                echo ""
                echo "  [r] Retry pairing"
                echo "  [s] Skip Bluetooth setup"
                echo ""
                read -p "Choose an option: " RETRY_OPTION

                if [[ "$RETRY_OPTION" =~ ^[Ss]$ ]]; then
                    log_info "Skipping Bluetooth pairing"
                    break
                fi
                continue
            fi
        done
    else
        log_info "Skipping Bluetooth remote setup"
        log_info "You can pair a remote later using: sudo $INSTALL_DIR/tools/bt/pair-remote.sh"
        BT_DEVICE_NAME="BeoSound 5c"
    fi

    # -------------------------------------------------------------------------
    # Spotify (optional)
    # -------------------------------------------------------------------------
    echo ""
    read -p "Spotify user ID for playlists (press Enter to skip): " SPOTIFY_USER_ID

    # -------------------------------------------------------------------------
    # Write configuration file
    # -------------------------------------------------------------------------
    echo ""
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

# Home Assistant dashboard for SECURITY page (without leading slash)
HA_SECURITY_DASHBOARD="$HA_SECURITY_DASHBOARD"

# Home Assistant Long-Lived Access Token (for API access)
HA_TOKEN="$HA_TOKEN"

# =============================================================================
# Bluetooth Configuration
# =============================================================================

# Bluetooth device name (how this device appears to others)
BT_DEVICE_NAME="$BT_DEVICE_NAME"

# BeoRemote One Bluetooth MAC address
BEOREMOTE_MAC="$BEOREMOTE_MAC"

# =============================================================================
# Spotify Configuration
# =============================================================================

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
# Configuration Summary
# =============================================================================

# Load configuration to display summary
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
fi

echo ""
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}          ${GREEN}BeoSound 5c - Installation Complete${NC}            ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Configuration Summary${NC}                                    ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Device Name:      ${GREEN}${DEVICE_NAME:-Not set}${NC}"
echo -e "${CYAN}║${NC}  Sonos IP:         ${GREEN}${SONOS_IP:-Not set}${NC}"
echo -e "${CYAN}║${NC}  Home Assistant:   ${GREEN}${HA_URL:-Not set}${NC}"
if [ -n "$HA_TOKEN" ] && [ "$HA_TOKEN" != "" ]; then
echo -e "${CYAN}║${NC}  HA Token:         ${GREEN}Configured${NC}"
else
echo -e "${CYAN}║${NC}  HA Token:         ${YELLOW}Not configured${NC}"
fi
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Bluetooth Remote${NC}                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
if [ "$BEOREMOTE_MAC" != "00:00:00:00:00:00" ] && [ -n "$BEOREMOTE_MAC" ]; then
echo -e "${CYAN}║${NC}  Status:           ${GREEN}Paired${NC}"
echo -e "${CYAN}║${NC}  Remote MAC:       ${GREEN}${BEOREMOTE_MAC}${NC}"
echo -e "${CYAN}║${NC}  Device Name:      ${GREEN}${BT_DEVICE_NAME:-BeoSound 5c}${NC}"
else
echo -e "${CYAN}║${NC}  Status:           ${BLUE}Not configured (IR mode works)${NC}"
fi
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
if [ -n "$SPOTIFY_USER_ID" ]; then
echo -e "${CYAN}║${NC}  ${YELLOW}Spotify${NC}                                                  ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  User ID:          ${GREEN}${SPOTIFY_USER_ID}${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
fi
echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}File Locations${NC}                                            ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Config:  ${GREEN}${CONFIG_FILE}${NC}"
echo -e "${CYAN}║${NC}  Install: ${GREEN}${INSTALL_DIR}${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
if [ $FAILED_CHECKS -eq 0 ]; then
echo -e "${CYAN}║${NC}  ${GREEN}✓ All verification checks passed${NC}                         ${CYAN}║${NC}"
else
echo -e "${CYAN}║${NC}  ${YELLOW}⚠ ${FAILED_CHECKS} verification check(s) need attention${NC}              ${CYAN}║${NC}"
fi
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Next Steps${NC}                                                ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  1. Reboot to apply all changes:                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${GREEN}sudo reboot${NC}                                             ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  2. After reboot, verify services are running:            ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${GREEN}systemctl status beo-*${NC}                                  ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  3. View live logs:                                       ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${GREEN}journalctl -u beo-ui -f${NC}                                 ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}To modify settings later:${NC}                                ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${GREEN}sudo nano ${CONFIG_FILE}${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Useful commands:${NC}                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${GREEN}sudo systemctl restart beo-*${NC}      Restart services     ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${GREEN}./services/system/status-services.sh${NC}  Check status     ${CYAN}║${NC}"
if [ "$BEOREMOTE_MAC" = "00:00:00:00:00:00" ] || [ -z "$BEOREMOTE_MAC" ]; then
echo -e "${CYAN}║${NC}     ${GREEN}sudo ./tools/bt/pair-remote.sh${NC}    Pair BT remote       ${CYAN}║${NC}"
fi
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

if [ $FAILED_CHECKS -gt 0 ]; then
    exit 1
fi

# Prompt for reboot
echo ""
read -p "Would you like to reboot now to apply all changes? (Y/n): " REBOOT_NOW
if [[ ! "$REBOOT_NOW" =~ ^[Nn]$ ]]; then
    log_info "Rebooting..."
    sudo reboot
fi
