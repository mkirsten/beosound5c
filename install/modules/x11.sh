#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — X11 configuration
# =============================================================================

configure_x11() {
    log_section "Configuring X11"

    # Allow any user to start X server (required for systemd service)
    log_info "Configuring X11 wrapper permissions..."
    cat > /etc/X11/Xwrapper.config << 'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
    log_success "X11 wrapper configured"

    # Remove any conflicting .xinitrc files that might interfere with beo-ui
    local XINITRC_FILE="/home/$INSTALL_USER/.xinitrc"
    if [ -f "$XINITRC_FILE" ]; then
        log_info "Found existing .xinitrc - backing up to .xinitrc.bak"
        mv "$XINITRC_FILE" "${XINITRC_FILE}.bak"
    fi
}
