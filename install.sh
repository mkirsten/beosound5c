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
CONFIG_FILE="$CONFIG_DIR/config.json"
SECRETS_FILE="$CONFIG_DIR/secrets.env"
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

log_info "Installing audio/TTS packages..."
apt-get install -y \
    espeak-ng

log_info "Installing utilities..."
apt-get install -y \
    curl \
    git \
    jq \
    mosquitto-clients

log_success "System packages installed"

# =============================================================================
# Python Package Installation
# =============================================================================
log_section "Installing Python Packages"

log_info "Installing Python packages via pip..."
pip3 install --break-system-packages -r "$SCRIPT_DIR/requirements.txt"

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
# SD Card Longevity Hardening
# =============================================================================
log_section "SD Card Longevity Hardening"

# --- Disable swap (major source of SD card wear) ---
log_info "Disabling swap..."
if command -v dphys-swapfile &>/dev/null; then
    dphys-swapfile swapoff 2>/dev/null
    dphys-swapfile uninstall 2>/dev/null
    systemctl disable dphys-swapfile 2>/dev/null
    echo "CONF_SWAPSIZE=0" > /etc/dphys-swapfile
    rm -f /var/swap
    log_success "Swap disabled and swap file removed"
else
    log_info "dphys-swapfile not installed — no swap to disable"
fi

# --- Add commit=120 to root filesystem (reduce journal flush from 5s to 120s) ---
log_info "Configuring filesystem mount options..."
FSTAB="/etc/fstab"
if grep -q "commit=" "$FSTAB"; then
    log_info "commit= already set in fstab"
else
    sed -i '/[[:space:]]\/[[:space:]].*ext4/ s/defaults,noatime/defaults,noatime,commit=120/' "$FSTAB"
    # If the above didn't match (no noatime yet), try plain defaults
    if ! grep -q "commit=" "$FSTAB"; then
        sed -i '/[[:space:]]\/[[:space:]].*ext4/ s/defaults/defaults,noatime,commit=120/' "$FSTAB"
    fi
    if grep -q "commit=120" "$FSTAB"; then
        log_success "Added commit=120 to root filesystem (reduces write frequency)"
    else
        log_warn "Could not add commit=120 — check /etc/fstab manually"
    fi
fi

# --- Mount /tmp as tmpfs ---
if grep -q "tmpfs.*/tmp " "$FSTAB"; then
    log_info "/tmp tmpfs already configured in fstab"
else
    echo "tmpfs /tmp tmpfs defaults,noatime,nosuid,nodev,size=200M,mode=1777 0 0" >> "$FSTAB"
    log_success "Added /tmp as 200MB tmpfs"
fi

# --- Mount /var/log as tmpfs ---
if grep -q "tmpfs.*/var/log" "$FSTAB"; then
    log_info "/var/log tmpfs already configured in fstab"
else
    echo "tmpfs /var/log tmpfs defaults,noatime,nosuid,nodev,size=50M,mode=0755 0 0" >> "$FSTAB"
    log_success "Added /var/log as 50MB tmpfs"
fi

# --- Configure journald to use volatile (RAM) storage ---
log_info "Configuring journald for volatile storage..."
JOURNALD_CONF="/etc/systemd/journald.conf"
if grep -q "^Storage=volatile" "$JOURNALD_CONF" 2>/dev/null; then
    log_info "journald already set to volatile"
else
    # Set Storage=volatile, uncommenting if needed
    sed -i 's/^#\?Storage=.*/Storage=volatile/' "$JOURNALD_CONF"
    if ! grep -q "^Storage=volatile" "$JOURNALD_CONF"; then
        echo "Storage=volatile" >> "$JOURNALD_CONF"
    fi
    log_success "journald set to volatile (logs stay in RAM)"
fi

# --- Disable journald rate limiting (services log useful info) ---
sed -i 's/^#\?RateLimitIntervalSec=.*/RateLimitIntervalSec=0/' "$JOURNALD_CONF"
sed -i 's/^#\?RateLimitBurst=.*/RateLimitBurst=0/' "$JOURNALD_CONF"

# --- Create tmpfiles.d config for /var/log subdirectories ---
log_info "Creating tmpfiles.d config for /var/log..."
cat > /etc/tmpfiles.d/var-log.conf << 'EOF'
# Create /var/log subdirectories on tmpfs
d /var/log 0755 root root -
d /var/log/apt 0755 root root -
d /var/log/cups 0755 root root -
d /var/log/journal 2755 root systemd-journal -
d /var/log/lightdm 0710 root root -
d /var/log/nginx 0755 root adm -
d /var/log/private 0700 root root -
EOF
log_success "tmpfiles.d config created"

# --- Redirect Xorg logs to tmpfs ---
XORG_LOG_DIR="/home/$INSTALL_USER/.local/share/xorg"
if [ -L "$XORG_LOG_DIR" ]; then
    log_info "Xorg log directory already symlinked"
else
    rm -rf "$XORG_LOG_DIR"
    sudo -u "$INSTALL_USER" mkdir -p "/home/$INSTALL_USER/.local/share"
    sudo -u "$INSTALL_USER" ln -s /tmp "$XORG_LOG_DIR"
    log_success "Xorg logs redirected to tmpfs (/tmp)"
fi

# --- Mount ~/.cache as tmpfs (Chromium cache, Mesa shaders, dconf, etc.) ---
USER_CACHE="/home/$INSTALL_USER/.cache"
USER_UID=$(id -u "$INSTALL_USER")
USER_GID=$(id -g "$INSTALL_USER")
if grep -q "$USER_CACHE" "$FSTAB"; then
    log_info "~/.cache tmpfs already configured in fstab"
else
    echo "tmpfs $USER_CACHE tmpfs defaults,noatime,nosuid,nodev,size=300M,uid=$USER_UID,gid=$USER_GID,mode=0700 0 0" >> "$FSTAB"
    log_success "Added ~/.cache as 300MB tmpfs"
fi

# --- Redirect ~/.fehbg to tmpfs ---
FEHBG="/home/$INSTALL_USER/.fehbg"
if [ -L "$FEHBG" ]; then
    log_info "~/.fehbg already symlinked"
else
    rm -f "$FEHBG"
    sudo -u "$INSTALL_USER" ln -s /tmp/.fehbg "$FEHBG"
    log_success "~/.fehbg redirected to tmpfs"
fi

# --- Redirect WirePlumber state to tmpfs ---
WIREPLUMBER_DIR="/home/$INSTALL_USER/.local/state/wireplumber"
if [ -L "$WIREPLUMBER_DIR" ]; then
    log_info "WirePlumber state already symlinked"
else
    rm -rf "$WIREPLUMBER_DIR"
    sudo -u "$INSTALL_USER" mkdir -p "/home/$INSTALL_USER/.local/state"
    sudo -u "$INSTALL_USER" ln -s /tmp "$WIREPLUMBER_DIR"
    log_success "WirePlumber state redirected to tmpfs"
fi

# --- Disable WirePlumber Bluetooth audio monitor ---
# The BS5c only uses BLE HID (handled by kernel HOGP), not Bluetooth audio.
# WirePlumber's bluez monitor continuously registers/unregisters A2DP endpoints
# with BlueZ, causing bluetoothd to burn ~40% CPU and adding latency to HID events.
WP_BT_OVERRIDE="/home/$INSTALL_USER/.config/wireplumber/bluetooth.lua.d"
WP_BT_SYSTEM="/usr/share/wireplumber/bluetooth.lua.d"
if [ -d "$WP_BT_SYSTEM" ]; then
    if [ -f "$WP_BT_OVERRIDE/90-enable-all.lua" ] && grep -q "disabled" "$WP_BT_OVERRIDE/90-enable-all.lua" 2>/dev/null; then
        log_info "WirePlumber Bluetooth monitor already disabled"
    else
        log_info "Disabling WirePlumber Bluetooth audio monitor..."
        sudo -u "$INSTALL_USER" mkdir -p "$WP_BT_OVERRIDE"
        cp "$WP_BT_SYSTEM/00-functions.lua" "$WP_BT_OVERRIDE/"
        chown "$INSTALL_USER:$INSTALL_USER" "$WP_BT_OVERRIDE/00-functions.lua"
        echo '-- Bluetooth audio monitor disabled (BS5c only uses BLE HID, not A2DP)' | sudo -u "$INSTALL_USER" tee "$WP_BT_OVERRIDE/90-enable-all.lua" > /dev/null
        log_success "WirePlumber Bluetooth monitor disabled (saves ~40% CPU on bluetoothd)"
    fi
else
    log_info "WirePlumber bluetooth.lua.d not found — skipping"
fi

# --- Symlink ~/.config/chromium to tmpfs (catches crashpad handler writes) ---
CHROMIUM_CFG="/home/$INSTALL_USER/.config/chromium"
if [ -L "$CHROMIUM_CFG" ]; then
    log_info "~/.config/chromium already symlinked"
else
    rm -rf "$CHROMIUM_CFG"
    sudo -u "$INSTALL_USER" mkdir -p "/home/$INSTALL_USER/.config"
    sudo -u "$INSTALL_USER" ln -s /tmp/chromium-profile "$CHROMIUM_CFG"
    log_success "~/.config/chromium redirected to tmpfs"
fi

# --- Disable unnecessary services (CUPS, apt-daily, logrotate, man-db) ---
log_info "Disabling unnecessary services..."
for svc in cups cups-browsed cups.socket cups.path; do
    systemctl disable --now "$svc" 2>/dev/null
    systemctl mask "$svc" 2>/dev/null
done
for timer in apt-daily.timer apt-daily-upgrade.timer man-db.timer logrotate.timer dpkg-db-backup.timer; do
    systemctl disable --now "$timer" 2>/dev/null
done
log_success "Disabled CUPS, apt-daily, logrotate, man-db, dpkg-db-backup"

log_success "SD card hardening complete"

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

# Scan for Bluesound devices on the network
scan_bluesound_devices() {
    log_info "Scanning for Bluesound devices on the network..."
    local bluesound_devices=()
    local timeout=2

    # Method 1: Try avahi/mDNS discovery (_musc._tcp is BluOS service type)
    if command -v avahi-browse &>/dev/null; then
        while IFS= read -r line; do
            if [[ "$line" =~ ([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+) ]]; then
                local ip="${BASH_REMATCH[1]}"
                # Verify it's a BluOS device by checking port 11000
                if timeout $timeout bash -c "echo >/dev/tcp/$ip/11000" 2>/dev/null; then
                    # Get device name from BluOS SyncStatus API
                    local name=$(curl -s --connect-timeout $timeout "http://$ip:11000/SyncStatus" 2>/dev/null | grep -oP '(?<=<name>)[^<]+' | head -1)
                    if [ -z "$name" ]; then
                        # Fallback: try Status endpoint
                        name=$(curl -s --connect-timeout $timeout "http://$ip:11000/Status" 2>/dev/null | grep -oP '(?<=<name>)[^<]+' | head -1)
                    fi
                    if [ -n "$name" ]; then
                        bluesound_devices+=("$ip|$name")
                    else
                        bluesound_devices+=("$ip|Bluesound Device")
                    fi
                fi
            fi
        done < <(avahi-browse -rtp _musc._tcp 2>/dev/null | grep "=" | head -10)
    fi

    # Method 2: Fallback - scan for port 11000 on local network
    if [ ${#bluesound_devices[@]} -eq 0 ]; then
        local network=$(ip route | grep -oP 'src \K[0-9.]+' | head -1 | sed 's/\.[0-9]*$/./')
        if [ -n "$network" ]; then
            log_info "Scanning network ${network}0/24 for Bluesound devices (port 11000)..."
            for i in $(seq 1 254); do
                local ip="${network}${i}"
                if timeout $timeout bash -c "echo >/dev/tcp/$ip/11000" 2>/dev/null; then
                    local name=$(curl -s --connect-timeout $timeout "http://$ip:11000/SyncStatus" 2>/dev/null | grep -oP '(?<=<name>)[^<]+' | head -1)
                    if [ -n "$name" ]; then
                        bluesound_devices+=("$ip|$name")
                    fi
                fi
            done &
            local scan_pid=$!
            sleep 10
            kill $scan_pid 2>/dev/null
            wait $scan_pid 2>/dev/null
        fi
    fi

    # Return results
    if [ ${#bluesound_devices[@]} -gt 0 ]; then
        printf '%s\n' "${bluesound_devices[@]}"
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
    # Player Configuration
    # -------------------------------------------------------------------------
    echo ""
    log_section "Player Configuration"

    echo "Select the network player type:"
    echo ""
    echo "  1) Sonos      - Sonos speaker (most common)"
    echo "  2) BlueSound  - BlueSound player"
    echo "  3) Local      - Local from the BeoSound 5c"
    echo ""

    PLAYER_TYPE="sonos"
    PLAYER_IP=""

    while true; do
        read -p "Select player type [1-3, default 1]: " PLAYER_CHOICE
        PLAYER_CHOICE="${PLAYER_CHOICE:-1}"
        case "$PLAYER_CHOICE" in
            1) PLAYER_TYPE="sonos"; break ;;
            2) PLAYER_TYPE="bluesound"; break ;;
            3) PLAYER_TYPE="none"; break ;;
            *) echo "Invalid selection. Please enter 1, 2, or 3." ;;
        esac
    done

    if [[ "$PLAYER_TYPE" == "sonos" ]]; then
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
                PLAYER_IP=$(echo "$selection" | grep -oP '\(([0-9.]+)\)' | tr -d '()')
            else
                read -p "Enter Sonos speaker IP address: " PLAYER_IP
            fi
        else
            log_warn "No Sonos devices found on the network"
            log_info "Make sure your Sonos speaker is powered on and connected to the same network"
            read -p "Enter Sonos speaker IP address: " PLAYER_IP
        fi
        PLAYER_IP="${PLAYER_IP:-192.168.1.100}"
    elif [[ "$PLAYER_TYPE" == "bluesound" ]]; then
        # Scan for Bluesound devices
        mapfile -t bluesound_results < <(scan_bluesound_devices)

        if [ ${#bluesound_results[@]} -gt 0 ]; then
            # Format results for display
            bluesound_display=()
            bluesound_ips=()
            for result in "${bluesound_results[@]}"; do
                ip=$(echo "$result" | cut -d'|' -f1)
                name=$(echo "$result" | cut -d'|' -f2)
                bluesound_display+=("$name ($ip)")
                bluesound_ips+=("$ip")
            done

            log_success "Found ${#bluesound_results[@]} Bluesound device(s)!"

            if selection=$(select_from_list "Select Bluesound player to control:" "${bluesound_display[@]}"); then
                # Extract IP from selection
                PLAYER_IP=$(echo "$selection" | grep -oP '\(([0-9.]+)\)' | tr -d '()')
            else
                read -p "Enter Bluesound player IP address: " PLAYER_IP
            fi
        else
            log_warn "No Bluesound devices found on the network"
            log_info "Make sure your Bluesound player is powered on and connected to the same network"
            read -p "Enter Bluesound player IP address: " PLAYER_IP
        fi
        PLAYER_IP="${PLAYER_IP:-192.168.1.100}"
    fi

    log_success "Player: $PLAYER_TYPE${PLAYER_IP:+ @ $PLAYER_IP}"

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
        log_info "You can add a token later by editing: $SECRETS_FILE"
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
    # Spotify Setup (optional)
    # -------------------------------------------------------------------------
    echo ""
    log_section "Spotify Integration (Optional)"
    echo ""
    echo "Spotify integration allows you to browse and play your Spotify playlists"
    echo "through the BeoSound 5c interface. This requires a Spotify account and"
    echo "creating a free Spotify Developer app."
    echo ""
    read -p "Do you want to set up Spotify playlist access? (y/N): " SETUP_SPOTIFY

    SPOTIFY_CLIENT_ID=""
    SPOTIFY_CLIENT_SECRET=""
    SPOTIFY_REFRESH_TOKEN=""
    SPOTIFY_SETUP_SUCCESS=false

    if [[ "$SETUP_SPOTIFY" =~ ^[Yy]$ ]]; then
        echo ""
        log_info "Starting Spotify OAuth Setup Wizard..."
        echo ""
        echo -e "${YELLOW}This will start a temporary web server on this device.${NC}"
        echo ""
        echo "You'll need:"
        echo "  1. A phone/tablet/computer on the same network"
        echo "  2. A Spotify account"
        echo "  3. To create a free app at developer.spotify.com"
        echo ""
        echo -e "${YELLOW}IMPORTANT: The SSL certificate is self-signed.${NC}"
        echo "When you open the URL, your browser will show a security warning."
        echo "You must tap 'Advanced' → 'Proceed anyway' to continue."
        echo ""
        read -p "Press Enter to start the setup wizard (or 's' to skip): " START_SPOTIFY_SETUP

        if [[ ! "$START_SPOTIFY_SETUP" =~ ^[Ss]$ ]]; then
            echo ""
            log_info "Spotify setup is done via the web interface."
            log_info "After installation, the beo-spotify service will start and serve"
            log_info "a setup page at http://<device-ip>:8771/setup"
            echo ""
            # Check if tokens already exist from a previous setup
            SPOTIFY_TOKENS="/etc/beosound5c/spotify_tokens.json"
            if [ -f "$SPOTIFY_TOKENS" ]; then
                SPOTIFY_CLIENT_ID=$(jq -r '.client_id // empty' "$SPOTIFY_TOKENS" 2>/dev/null)
                SPOTIFY_REFRESH_TOKEN=$(jq -r '.refresh_token // empty' "$SPOTIFY_TOKENS" 2>/dev/null)
                if [ -n "$SPOTIFY_REFRESH_TOKEN" ]; then
                    log_success "Spotify tokens found from previous setup!"
                    SPOTIFY_SETUP_SUCCESS=true
                fi
            fi
            if [ "$SPOTIFY_SETUP_SUCCESS" = false ]; then
                log_info "Open http://<device-ip>:8771/setup after starting services"
            fi
        else
            log_info "Skipping Spotify setup"
        fi
    else
        log_info "Skipping Spotify integration"
        log_info "You can set it up later at http://<device-ip>:8771/setup"
    fi

    # -------------------------------------------------------------------------
    # Transport Configuration (webhook / MQTT / both)
    # -------------------------------------------------------------------------
    echo ""
    log_section "Transport Configuration"
    echo ""
    echo "BeoSound 5c can communicate with Home Assistant via:"
    echo ""
    echo "  1) Webhook  - HTTP POST requests (default, works out of the box)"
    echo "  2) MQTT     - Persistent connection via MQTT broker (lower latency, bidirectional)"
    echo "  3) Both     - Send events via both webhook AND MQTT"
    echo ""
    echo "MQTT requires the Mosquitto add-on or another MQTT broker running on your network."
    echo ""

    TRANSPORT_MODE="webhook"
    MQTT_BROKER=""
    MQTT_PORT="1883"
    MQTT_USER=""
    MQTT_PASSWORD=""

    while true; do
        read -p "Select transport mode [1-3, default 1]: " TRANSPORT_CHOICE
        TRANSPORT_CHOICE="${TRANSPORT_CHOICE:-1}"
        case "$TRANSPORT_CHOICE" in
            1) TRANSPORT_MODE="webhook"; break ;;
            2) TRANSPORT_MODE="mqtt"; break ;;
            3) TRANSPORT_MODE="both"; break ;;
            *) echo "Invalid selection. Please enter 1, 2, or 3." ;;
        esac
    done
    log_success "Transport mode: $TRANSPORT_MODE"

    if [[ "$TRANSPORT_MODE" == "mqtt" || "$TRANSPORT_MODE" == "both" ]]; then
        echo ""
        log_info "MQTT Broker Configuration"
        echo ""

        DEFAULT_MQTT_BROKER="homeassistant.local"
        read -p "MQTT broker hostname [$DEFAULT_MQTT_BROKER]: " MQTT_BROKER
        MQTT_BROKER="${MQTT_BROKER:-$DEFAULT_MQTT_BROKER}"

        read -p "MQTT broker port [1883]: " MQTT_PORT
        MQTT_PORT="${MQTT_PORT:-1883}"

        read -p "MQTT username (press Enter if none): " MQTT_USER
        if [[ -n "$MQTT_USER" ]]; then
            read -s -p "MQTT password: " MQTT_PASSWORD
            echo ""
        fi

        log_success "MQTT broker: $MQTT_BROKER:$MQTT_PORT"

        # Test MQTT connectivity
        if command -v mosquitto_pub &>/dev/null; then
            echo -n "  Testing MQTT connection "
            mqtt_auth=()
            if [[ -n "$MQTT_USER" ]]; then
                mqtt_auth=(-u "$MQTT_USER")
                if [[ -n "$MQTT_PASSWORD" ]]; then
                    mqtt_auth+=(-P "$MQTT_PASSWORD")
                fi
            fi
            if mosquitto_pub -h "$MQTT_BROKER" -p "$MQTT_PORT" "${mqtt_auth[@]}" \
                -t "beosound5c/test" -m "install_test" 2>/dev/null; then
                echo ""
                log_success "MQTT connection successful!"
            else
                echo ""
                log_warn "Could not connect to MQTT broker - check settings later"
            fi
        fi
    fi

    # -------------------------------------------------------------------------
    # Audio Output Configuration
    # -------------------------------------------------------------------------
    echo ""
    log_section "Audio Output Configuration"
    echo ""
    echo "Configure how BeoSound 5c controls volume on your speakers."
    echo ""

    DEFAULT_OUTPUT_NAME="BeoLab 5"
    read -p "Audio output name (shown in UI) [$DEFAULT_OUTPUT_NAME]: " OUTPUT_NAME
    OUTPUT_NAME="${OUTPUT_NAME:-$DEFAULT_OUTPUT_NAME}"

    echo ""
    echo "Volume control method:"
    echo ""

    VOLUME_TYPE="beolab5"
    VOLUME_HOST=""
    VOLUME_MAX="70"
    VOLUME_ZONE=""
    VOLUME_INPUT=""
    VOLUME_MIXER_PORT=""

    if [[ "$PLAYER_TYPE" == "sonos" ]]; then
        echo "  1) Sonos       - Control volume directly on the Sonos speaker (Recommended)"
        echo "  2) BeoLab 5    - BeoLab 5 via controller REST API"
        echo "  3) PowerLink   - B&O speakers via MasterLink mixer"
        echo "  4) C4 Amp      - Control4 amplifier via UDP"
        echo "  5) HDMI        - HDMI audio output (ALSA software volume)"
        echo "  6) S/PDIF      - S/PDIF HAT output (ALSA software volume)"
        echo "  7) RCA         - RCA analog output (no volume control)"
        echo ""
        while true; do
            read -p "Select volume control [1-7, default 1]: " VOLUME_CHOICE
            VOLUME_CHOICE="${VOLUME_CHOICE:-1}"
            case "$VOLUME_CHOICE" in
                1) VOLUME_TYPE="sonos"; break ;;
                2) VOLUME_TYPE="beolab5"; break ;;
                3) VOLUME_TYPE="powerlink"; break ;;
                4) VOLUME_TYPE="c4amp"; break ;;
                5) VOLUME_TYPE="hdmi"; break ;;
                6) VOLUME_TYPE="spdif"; break ;;
                7) VOLUME_TYPE="rca"; break ;;
                *) echo "Invalid selection. Please enter 1-7." ;;
            esac
        done
    elif [[ "$PLAYER_TYPE" == "bluesound" ]]; then
        echo "  1) BlueSound   - Control volume directly on the BlueSound player (Recommended)"
        echo "  2) BeoLab 5    - BeoLab 5 via controller REST API"
        echo "  3) PowerLink   - B&O speakers via MasterLink mixer"
        echo "  4) C4 Amp      - Control4 amplifier via UDP"
        echo "  5) HDMI        - HDMI audio output (ALSA software volume)"
        echo "  6) S/PDIF      - S/PDIF HAT output (ALSA software volume)"
        echo "  7) RCA         - RCA analog output (no volume control)"
        echo ""
        while true; do
            read -p "Select volume control [1-7, default 1]: " VOLUME_CHOICE
            VOLUME_CHOICE="${VOLUME_CHOICE:-1}"
            case "$VOLUME_CHOICE" in
                1) VOLUME_TYPE="bluesound"; break ;;
                2) VOLUME_TYPE="beolab5"; break ;;
                3) VOLUME_TYPE="powerlink"; break ;;
                4) VOLUME_TYPE="c4amp"; break ;;
                5) VOLUME_TYPE="hdmi"; break ;;
                6) VOLUME_TYPE="spdif"; break ;;
                7) VOLUME_TYPE="rca"; break ;;
                *) echo "Invalid selection. Please enter 1-7." ;;
            esac
        done
    else
        echo "  1) PowerLink   - B&O speakers via MasterLink mixer (Recommended)"
        echo "  2) BeoLab 5    - BeoLab 5 via controller REST API"
        echo "  3) C4 Amp      - Control4 amplifier via UDP"
        echo "  4) HDMI        - HDMI audio output (ALSA software volume)"
        echo "  5) S/PDIF      - S/PDIF HAT output (ALSA software volume)"
        echo "  6) RCA         - RCA analog output (no volume control)"
        echo ""
        while true; do
            read -p "Select volume control [1-6, default 1]: " VOLUME_CHOICE
            VOLUME_CHOICE="${VOLUME_CHOICE:-1}"
            case "$VOLUME_CHOICE" in
                1) VOLUME_TYPE="powerlink"; break ;;
                2) VOLUME_TYPE="beolab5"; break ;;
                3) VOLUME_TYPE="c4amp"; break ;;
                4) VOLUME_TYPE="hdmi"; break ;;
                5) VOLUME_TYPE="spdif"; break ;;
                6) VOLUME_TYPE="rca"; break ;;
                *) echo "Invalid selection. Please enter 1-6." ;;
            esac
        done
    fi

    # Per-type configuration
    case "$VOLUME_TYPE" in
        beolab5)
            DEFAULT_VOLUME_HOST="beolab5-controller.local"
            read -p "BeoLab 5 controller hostname [$DEFAULT_VOLUME_HOST]: " VOLUME_HOST
            VOLUME_HOST="${VOLUME_HOST:-$DEFAULT_VOLUME_HOST}"
            ;;
        sonos|bluesound)
            VOLUME_HOST="$PLAYER_IP"
            log_info "Using player IP ($PLAYER_IP) for volume control"
            ;;
        powerlink)
            DEFAULT_VOLUME_HOST="localhost"
            read -p "MasterLink mixer host [$DEFAULT_VOLUME_HOST]: " VOLUME_HOST
            VOLUME_HOST="${VOLUME_HOST:-$DEFAULT_VOLUME_HOST}"
            read -p "Mixer HTTP port [8768]: " VOLUME_MIXER_PORT
            VOLUME_MIXER_PORT="${VOLUME_MIXER_PORT:-8768}"
            ;;
        c4amp)
            read -p "C4 amplifier IP address: " VOLUME_HOST
            VOLUME_HOST="${VOLUME_HOST:-192.168.1.100}"
            read -p "Output zone [01]: " VOLUME_ZONE
            VOLUME_ZONE="${VOLUME_ZONE:-01}"
            read -p "Source input [01]: " VOLUME_INPUT
            VOLUME_INPUT="${VOLUME_INPUT:-01}"
            ;;
        hdmi|spdif|rca)
            VOLUME_HOST=""
            ;;
    esac

    read -p "Maximum volume percentage [70]: " VOLUME_MAX
    VOLUME_MAX="${VOLUME_MAX:-70}"

    # Build extra volume config fields
    VOLUME_EXTRA=""
    [[ -n "$VOLUME_ZONE" ]] && VOLUME_EXTRA="$VOLUME_EXTRA, \"zone\": \"$VOLUME_ZONE\""
    [[ -n "$VOLUME_INPUT" ]] && VOLUME_EXTRA="$VOLUME_EXTRA, \"input\": \"$VOLUME_INPUT\""
    [[ -n "$VOLUME_MIXER_PORT" ]] && VOLUME_EXTRA="$VOLUME_EXTRA, \"mixer_port\": $VOLUME_MIXER_PORT"

    log_success "Output: $OUTPUT_NAME, volume: $VOLUME_TYPE @ ${VOLUME_HOST:-local} (max $VOLUME_MAX%%)"

    # -------------------------------------------------------------------------
    # Write config.json
    # -------------------------------------------------------------------------
    echo ""
    log_info "Writing configuration to $CONFIG_FILE..."

    # Build menu — include SECURITY only if a dashboard was configured
    MENU_JSON='"PLAYING": "playing", "CD": { "id": "cd", "hidden": true }, "USB": { "id": "usb", "paths": ["/mnt/usb-music"] }, "SPOTIFY": "spotify", "SCENES": "scenes"'
    if [ -n "$HA_SECURITY_DASHBOARD" ]; then
        MENU_JSON="$MENU_JSON, \"SECURITY\": { \"id\": \"security\", \"dashboard\": \"$HA_SECURITY_DASHBOARD\" }"
    fi
    MENU_JSON="$MENU_JSON, \"SYSTEM\": \"system\", \"SHOWING\": \"showing\""

    # Build transport section
    TRANSPORT_JSON="\"mode\": \"$TRANSPORT_MODE\""
    if [[ "$TRANSPORT_MODE" == "mqtt" || "$TRANSPORT_MODE" == "both" ]]; then
        TRANSPORT_JSON="$TRANSPORT_JSON, \"mqtt_broker\": \"$MQTT_BROKER\", \"mqtt_port\": $MQTT_PORT"
    fi

    cat > "$CONFIG_FILE" << EOF
{
  "device": "$DEVICE_NAME",

  "menu": { $MENU_JSON },

  "scenes": [],

  "player": { "type": "$PLAYER_TYPE", "ip": "$PLAYER_IP" },
  "bluetooth": { "remote_mac": "$BEOREMOTE_MAC" },
  "home_assistant": {
    "url": "$HA_URL",
    "webhook_url": "$HA_WEBHOOK_URL"
  },
  "transport": { $TRANSPORT_JSON },
  "volume": {
    "type": "$VOLUME_TYPE",
    "host": "$VOLUME_HOST",
    "max": $VOLUME_MAX,
    "step": 3,
    "output_name": "$OUTPUT_NAME"$VOLUME_EXTRA
  },
  "cd": { "device": "/dev/sr0" },
  "spotify": { "client_id": "$SPOTIFY_CLIENT_ID" }
}
EOF

    chmod 644 "$CONFIG_FILE"
    log_success "Configuration saved to $CONFIG_FILE"

    # -------------------------------------------------------------------------
    # Write secrets.env
    # -------------------------------------------------------------------------
    log_info "Writing secrets to $SECRETS_FILE..."
    cat > "$SECRETS_FILE" << EOF
# BeoSound 5c Secrets
# Generated by install.sh on $(date)

# Home Assistant Long-Lived Access Token
HA_TOKEN="$HA_TOKEN"

# MQTT credentials (only needed if transport.mode includes "mqtt")
MQTT_USER="$MQTT_USER"
MQTT_PASSWORD="$MQTT_PASSWORD"
EOF

    chmod 600 "$SECRETS_FILE"
    log_success "Secrets saved to $SECRETS_FILE"

    # Symlink web/json/config.json → /etc/beosound5c/config.json so the UI can load it via HTTP
    mkdir -p "$INSTALL_DIR/web/json"
    ln -sf "$CONFIG_FILE" "$INSTALL_DIR/web/json/config.json"
    log_success "Config symlinked to web/json/config.json for UI"
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
SERVICES="beo-http beo-player-sonos beo-input beo-ui"
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
# Variables are already set from the interactive prompts above.
# For the reconfigure=no path, read them back from the JSON file.
if [ -f "$CONFIG_FILE" ] && [ -z "$DEVICE_NAME" ]; then
    DEVICE_NAME=$(jq -r '.device // empty' "$CONFIG_FILE" 2>/dev/null)
    PLAYER_TYPE=$(jq -r '.player.type // empty' "$CONFIG_FILE" 2>/dev/null)
    PLAYER_IP=$(jq -r '.player.ip // empty' "$CONFIG_FILE" 2>/dev/null)
    HA_URL=$(jq -r '.home_assistant.url // empty' "$CONFIG_FILE" 2>/dev/null)
    TRANSPORT_MODE=$(jq -r '.transport.mode // empty' "$CONFIG_FILE" 2>/dev/null)
    MQTT_BROKER=$(jq -r '.transport.mqtt_broker // empty' "$CONFIG_FILE" 2>/dev/null)
    MQTT_PORT=$(jq -r '.transport.mqtt_port // empty' "$CONFIG_FILE" 2>/dev/null)
    OUTPUT_NAME=$(jq -r '.volume.output_name // empty' "$CONFIG_FILE" 2>/dev/null)
    VOLUME_TYPE=$(jq -r '.volume.type // empty' "$CONFIG_FILE" 2>/dev/null)
    VOLUME_HOST=$(jq -r '.volume.host // empty' "$CONFIG_FILE" 2>/dev/null)
    VOLUME_MAX=$(jq -r '.volume.max // empty' "$CONFIG_FILE" 2>/dev/null)
    BEOREMOTE_MAC=$(jq -r '.bluetooth.remote_mac // empty' "$CONFIG_FILE" 2>/dev/null)
fi
if [ -f "$SECRETS_FILE" ] && [ -z "$HA_TOKEN" ]; then
    HA_TOKEN=$(grep '^HA_TOKEN=' "$SECRETS_FILE" 2>/dev/null | cut -d'"' -f2)
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
echo -e "${CYAN}║${NC}  Player:           ${GREEN}${PLAYER_TYPE:-Not set}${PLAYER_IP:+ @ $PLAYER_IP}${NC}"
echo -e "${CYAN}║${NC}  Home Assistant:   ${GREEN}${HA_URL:-Not set}${NC}"
if [ -n "$HA_TOKEN" ] && [ "$HA_TOKEN" != "" ]; then
echo -e "${CYAN}║${NC}  HA Token:         ${GREEN}Configured${NC}"
else
echo -e "${CYAN}║${NC}  HA Token:         ${YELLOW}Not configured${NC}"
fi
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Transport${NC}                                                  ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Mode:             ${GREEN}${TRANSPORT_MODE:-webhook}${NC}"
if [ -n "$MQTT_BROKER" ] && [[ "$TRANSPORT_MODE" == "mqtt" || "$TRANSPORT_MODE" == "both" ]]; then
echo -e "${CYAN}║${NC}  MQTT Broker:      ${GREEN}${MQTT_BROKER}:${MQTT_PORT}${NC}"
fi
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Audio Output${NC}                                                ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Output:           ${GREEN}${OUTPUT_NAME:-BeoLab 5}${NC}"
echo -e "${CYAN}║${NC}  Volume Control:   ${GREEN}${VOLUME_TYPE:-beolab5} @ ${VOLUME_HOST:-beolab5-controller.local}${NC}"
echo -e "${CYAN}║${NC}  Max Volume:       ${GREEN}${VOLUME_MAX:-70}%%${NC}"
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
echo -e "${CYAN}║${NC}  ${YELLOW}Spotify${NC}                                                    ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
if [ -n "$SPOTIFY_REFRESH_TOKEN" ]; then
echo -e "${CYAN}║${NC}  Status:           ${GREEN}Connected${NC}"
else
echo -e "${CYAN}║${NC}  Status:           ${BLUE}Not configured${NC}"
fi
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}File Locations${NC}                                            ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Config:  ${GREEN}${CONFIG_FILE}${NC}"
echo -e "${CYAN}║${NC}  Secrets: ${GREEN}${SECRETS_FILE}${NC}"
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
echo -e "${CYAN}║${NC}     ${GREEN}sudo nano ${CONFIG_FILE}${NC}  (settings)"
echo -e "${CYAN}║${NC}     ${GREEN}sudo nano ${SECRETS_FILE}${NC}  (credentials)"
echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Useful commands:${NC}                                          ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${GREEN}sudo systemctl restart beo-*${NC}      Restart services     ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}     ${GREEN}./services/system/status-services.sh${NC}  Check status     ${CYAN}║${NC}"
if [ "$BEOREMOTE_MAC" = "00:00:00:00:00:00" ] || [ -z "$BEOREMOTE_MAC" ]; then
echo -e "${CYAN}║${NC}     ${GREEN}sudo ./tools/bt/pair-remote.sh${NC}    Pair BT remote       ${CYAN}║${NC}"
fi
if [ -z "$SPOTIFY_REFRESH_TOKEN" ]; then
echo -e "${CYAN}║${NC}     ${GREEN}http://<device-ip>:8771/setup${NC}            Spotify${CYAN}║${NC}"
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
