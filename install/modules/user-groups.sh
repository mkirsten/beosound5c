#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — User group membership
# =============================================================================

configure_user_groups() {
    log_section "Configuring User Groups"

    log_info "Adding $INSTALL_USER to required groups..."
    usermod -aG video,input,bluetooth,dialout,tty "$INSTALL_USER"

    log_success "User added to groups: video, input, bluetooth, dialout, tty"

    # Ensure passwordless sudo for kiosk commands (ui.sh needs pkill, fbi, plymouth, reboot)
    local SUDOERS_FILE="/etc/sudoers.d/beosound5c"
    if [ ! -f "$SUDOERS_FILE" ]; then
        log_info "Configuring passwordless sudo for kiosk commands..."
        cat > "$SUDOERS_FILE" << SUDOEOF
# BeoSound 5c — UI kiosk needs these without a password prompt
$INSTALL_USER ALL=(ALL) NOPASSWD: /usr/bin/pkill, /usr/bin/fbi, /usr/bin/plymouth, /sbin/reboot, /usr/sbin/reboot
SUDOEOF
        chmod 440 "$SUDOERS_FILE"
        log_success "Sudoers configured for kiosk commands"
    else
        log_info "Sudoers file already exists: $SUDOERS_FILE"
    fi
}
