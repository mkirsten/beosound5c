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
    local XINITRC_FILE="$INSTALL_HOME/.xinitrc"
    if [ -f "$XINITRC_FILE" ]; then
        log_info "Found existing .xinitrc - backing up to .xinitrc.bak"
        mv "$XINITRC_FILE" "${XINITRC_FILE}.bak"
    fi
}

# =============================================================================
# Disable the desktop session so beo-ui owns the display.
#
# The installer is written for Raspberry Pi OS Lite, but plenty of people flash
# the full desktop image. There, a display manager (lightdm) autologs into a
# Wayland/labwc session that permanently owns the console, DRM master and the
# X11 :0 socket. beo-ui's kiosk Xorg then loops forever on "Cannot establish
# any listening sockets — Make sure an X server isn't already running", and the
# device sits on the boot splash. Boot to the console instead and let beo-ui
# start its own Xorg — matching how a Lite install already behaves.
# =============================================================================
disable_desktop_session() {
    log_section "Disabling Desktop Session (kiosk owns the display)"

    # Boot to the console, not the graphical target.
    if [ "$(systemctl get-default 2>/dev/null)" != "multi-user.target" ]; then
        systemctl set-default multi-user.target >/dev/null 2>&1 \
            && log_success "Default boot target set to multi-user (console)" \
            || log_warn "Could not set default target to multi-user.target"
    else
        log_info "Already booting to console (multi-user.target)"
    fi

    # Disable any display manager that would grab the console before beo-ui.
    local dm found=0
    for dm in lightdm gdm gdm3 sddm lxdm wdm nodm; do
        if systemctl list-unit-files "${dm}.service" >/dev/null 2>&1 \
           && systemctl is-enabled "${dm}.service" >/dev/null 2>&1; then
            systemctl disable "${dm}.service" >/dev/null 2>&1 \
                && log_success "Disabled display manager: ${dm}" \
                || log_warn "Could not disable ${dm}"
            found=1
        fi
    done
    # NOTE: must be if/fi, not `[ ... ] && log`. With a display manager found
    # (found=1) the bare && list would make this function return 1, and under
    # the installer's set -e that aborts the entire install right after
    # disabling the desktop — on precisely the image this function exists for.
    if [ "$found" -eq 0 ]; then
        log_info "No enabled display manager found (Lite image or already disabled)"
    fi
}
