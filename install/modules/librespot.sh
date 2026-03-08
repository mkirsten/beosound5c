#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — go-librespot (Spotify Connect local playback)
# =============================================================================

LIBRESPOT_VERSION="0.7.1"
LIBRESPOT_BINARY="/usr/local/bin/go-librespot"
LIBRESPOT_CONFIG_DIR="/etc/beosound5c/librespot"

install_librespot() {
    log_section "Installing go-librespot (Spotify Connect)"

    # Detect architecture
    local ARCH
    ARCH=$(uname -m)
    case "$ARCH" in
        aarch64) ARCH="arm64" ;;
        armv7l)  ARCH="armv6" ;;  # go-librespot uses armv6 for 32-bit ARM
        x86_64)  ARCH="amd64" ;;
        *)
            log_warn "Unsupported architecture: $ARCH — skipping go-librespot"
            return
            ;;
    esac

    # Download if not installed or wrong version
    local NEEDS_INSTALL=true
    if [ -x "$LIBRESPOT_BINARY" ]; then
        local CURRENT_VERSION
        CURRENT_VERSION=$("$LIBRESPOT_BINARY" 2>&1 | grep -oP 'go-librespot \K[0-9.]+' || echo "")
        if [ "$CURRENT_VERSION" = "$LIBRESPOT_VERSION" ]; then
            log_info "go-librespot $LIBRESPOT_VERSION already installed"
            NEEDS_INSTALL=false
        else
            log_info "Upgrading go-librespot from $CURRENT_VERSION to $LIBRESPOT_VERSION"
        fi
    fi

    if [ "$NEEDS_INSTALL" = true ]; then
        local URL="https://github.com/devgianlu/go-librespot/releases/download/v${LIBRESPOT_VERSION}/go-librespot_linux_${ARCH}.tar.gz"
        log_info "Downloading go-librespot $LIBRESPOT_VERSION ($ARCH)..."
        local TMP_DIR
        TMP_DIR=$(mktemp -d)
        if curl -fsSL "$URL" -o "$TMP_DIR/go-librespot.tar.gz"; then
            tar -xzf "$TMP_DIR/go-librespot.tar.gz" -C "$TMP_DIR"
            install -m 755 "$TMP_DIR/go-librespot" "$LIBRESPOT_BINARY"
            rm -rf "$TMP_DIR"
            log_success "go-librespot $LIBRESPOT_VERSION installed"
        else
            log_warn "Failed to download go-librespot — skipping"
            rm -rf "$TMP_DIR"
            return
        fi
    fi

    # Create config directory and default config
    mkdir -p "$LIBRESPOT_CONFIG_DIR"

    if [ ! -f "$LIBRESPOT_CONFIG_DIR/config.yml" ]; then
        local DEVICE_NAME="BeoSound 5c"
        if [ -f "$CONFIG_FILE" ]; then
            local CFG_NAME
            CFG_NAME=$(python3 -c "import json;print(json.load(open('$CONFIG_FILE')).get('device',''))" 2>/dev/null)
            [ -n "$CFG_NAME" ] && DEVICE_NAME="BeoSound 5c $CFG_NAME"
        fi

        log_info "Creating go-librespot config (device: $DEVICE_NAME)..."
        cat > "$LIBRESPOT_CONFIG_DIR/config.yml" << LIBCFG_EOF
device_name: "$DEVICE_NAME"
device_type: speaker
audio_backend: pulseaudio
bitrate: 320
external_volume: true
initial_volume: 100
volume_steps: 100
zeroconf_enabled: true
credentials:
  type: zeroconf
  zeroconf:
    persist_credentials: true
server:
  enabled: true
  address: localhost
  port: 3678
LIBCFG_EOF
        log_success "go-librespot config created"
    else
        log_info "go-librespot config already exists"
    fi

    # Set ownership to install user
    chown -R "$INSTALL_USER:$INSTALL_USER" "$LIBRESPOT_CONFIG_DIR"
}
