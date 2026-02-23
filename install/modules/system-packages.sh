#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — System package installation
# =============================================================================

install_system_packages() {
    log_section "Installing System Packages"

    log_info "Updating package lists..."
    apt-get update -qq

    log_info "Installing X11 and display packages..."
    # Chromium package name varies: 'chromium-browser' (Bullseye) vs 'chromium' (Bookworm+)
    local CHROMIUM_PKG="chromium-browser"
    if ! apt-cache show chromium-browser &>/dev/null; then
        CHROMIUM_PKG="chromium"
    fi
    apt-get install -y --no-install-recommends \
        xserver-xorg \
        x11-xserver-utils \
        x11-utils \
        xdotool \
        xinit \
        openbox \
        "$CHROMIUM_PKG" \
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
        avahi-utils \
        curl \
        git \
        jq \
        mosquitto-clients \
        openssl \
        samba

    log_info "Installing CD/media packages..."
    apt-get install -y \
        mpv \
        cdparanoia \
        libdiscid-dev

    # --- PipeWire from backports (better Bluetooth + AirPlay support) ---
    local CODENAME
    CODENAME=$(grep VERSION_CODENAME /etc/os-release 2>/dev/null | cut -d= -f2)
    if [ -n "$CODENAME" ]; then
        local BACKPORTS_LIST="/etc/apt/sources.list.d/backports.list"
        local BACKPORTS_LINE="deb http://deb.debian.org/debian ${CODENAME}-backports main"
        if [ ! -f "$BACKPORTS_LIST" ] || ! grep -q "${CODENAME}-backports" "$BACKPORTS_LIST" 2>/dev/null; then
            log_info "Enabling Debian ${CODENAME}-backports..."
            echo "$BACKPORTS_LINE" > "$BACKPORTS_LIST"
            apt-get update -qq
        fi
        log_info "Upgrading PipeWire from backports..."
        apt-get install -y -qq -t "${CODENAME}-backports" \
            pipewire pipewire-pulse pipewire-alsa libspa-0.2-bluetooth 2>/dev/null || \
            log_warn "PipeWire backports upgrade failed — using stock version"
    else
        log_warn "Could not detect Debian codename — skipping PipeWire backports"
    fi

    # PipeWire RAOP config (AirPlay speaker discovery)
    local PIPEWIRE_CONF_DIR="/etc/pipewire/pipewire.conf.d"
    local RAOP_CONF="$PIPEWIRE_CONF_DIR/raop-discover.conf"
    if [ ! -f "$RAOP_CONF" ]; then
        log_info "Installing PipeWire RAOP discovery config..."
        mkdir -p "$PIPEWIRE_CONF_DIR"
        cat > "$RAOP_CONF" << 'RAOP_EOF'
# Enable AirPlay (RAOP) speaker discovery
# Discovered speakers appear as PipeWire sinks
context.modules = [
    { name = libpipewire-module-raop-discover }
]
RAOP_EOF
        chmod 644 "$RAOP_CONF"
    fi

    log_success "System packages installed"
}

# =============================================================================
# USB music auto-mount + Samba share (NTFS drives exposed for Sonos)
# =============================================================================
install_usb_music_support() {
    log_section "USB Music Support"

    local SERVICES_SYSTEM_DIR="$INSTALL_DIR/services/system"

    if [ -f "$SERVICES_SYSTEM_DIR/usb-music-mount.sh" ] && [ -f "$SERVICES_SYSTEM_DIR/99-usb-music.rules" ]; then
        mkdir -p /mnt/usb-music
        cp "$SERVICES_SYSTEM_DIR/usb-music-mount.sh" /usr/local/bin/usb-music-mount.sh
        chmod +x /usr/local/bin/usb-music-mount.sh
        cp "$SERVICES_SYSTEM_DIR/99-usb-music.rules" /etc/udev/rules.d/99-usb-music.rules
        chmod 644 /etc/udev/rules.d/99-usb-music.rules
        udevadm control --reload-rules
        log_success "USB mount udev rule and script installed"
    else
        log_info "USB music mount files not found — skipping"
    fi

    # Samba share for USB music
    if ! grep -q "USB-Music" /etc/samba/smb.conf 2>/dev/null; then
        log_info "Adding USB-Music Samba share..."
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
        systemctl restart smbd 2>/dev/null || true
        log_success "Samba share configured"
    else
        log_info "USB-Music Samba share already configured"
    fi
}
