#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — X11 configuration
# =============================================================================

configure_x11() {
    log_section "Configuring X11"

    local XORG_CONF_DIR="/etc/X11/xorg.conf.d"
    mkdir -p "$XORG_CONF_DIR"

    # Allow any user to start X server (required for systemd service)
    log_info "Configuring X11 wrapper permissions..."
    cat > /etc/X11/Xwrapper.config << 'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
    log_success "X11 wrapper configured"

    # KMS modesetting driver — required for Pi 5 (two DRM cards: v3d + display)
    log_info "Installing KMS modesetting config..."
    cat > "$XORG_CONF_DIR/99-v3d.conf" << 'EOF'
Section "OutputClass"
  Identifier "vc4"
  MatchDriver "vc4"
  Driver "modesetting"
  Option "PrimaryGPU" "true"
EndSection
EOF
    log_success "KMS modesetting config installed"

    # Disable DPMS screen blanking — kiosk must stay on permanently
    log_info "Installing DPMS disable config..."
    cat > "$XORG_CONF_DIR/10-no-dpms.conf" << 'EOF'
Section "ServerFlags"
    Option "BlankTime"    "0"
    Option "StandbyTime"  "0"
    Option "SuspendTime"  "0"
    Option "OffTime"      "0"
EndSection
EOF
    log_success "DPMS disable config installed"

    # Remove any conflicting .xinitrc files that might interfere with beo-ui
    local XINITRC_FILE="/home/$INSTALL_USER/.xinitrc"
    if [ -f "$XINITRC_FILE" ]; then
        log_info "Found existing .xinitrc - backing up to .xinitrc.bak"
        mv "$XINITRC_FILE" "${XINITRC_FILE}.bak"
    fi
}
