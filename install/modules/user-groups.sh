#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — User group membership
# =============================================================================

configure_user_groups() {
    log_section "Configuring User Groups"

    log_info "Adding $INSTALL_USER to required groups..."
    usermod -aG video,input,bluetooth,dialout,tty "$INSTALL_USER"

    log_success "User added to groups: video, input, bluetooth, dialout, tty"

    # Passwordless sudo for kiosk commands and config management.
    # Write to a temp file and validate with visudo before installing —
    # a truncated or malformed sudoers.d file breaks sudo SYSTEM-WIDE,
    # and on these headless devices everything self-heals through sudo,
    # so that's a bricked-for-remote-admin state.
    local SUDOERS_FILE="/etc/sudoers.d/beosound5c"
    log_info "Configuring passwordless sudo..."
    local SUDOERS_TMP
    SUDOERS_TMP=$(mktemp "${SUDOERS_FILE}.XXXXXX")
    cat > "$SUDOERS_TMP" << SUDOEOF
# BeoSound 5c — UI kiosk and config management
$INSTALL_USER ALL=(ALL) NOPASSWD: /usr/bin/pkill, /usr/bin/fbi, /usr/bin/plymouth, /sbin/reboot, /usr/sbin/reboot
$INSTALL_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/beosound5c/config.json
$INSTALL_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart beo-*
$INSTALL_USER ALL=(ALL) NOPASSWD: /bin/systemctl stop beo-*
$INSTALL_USER ALL=(ALL) NOPASSWD: /bin/systemctl start beo-*
$INSTALL_USER ALL=(ALL) NOPASSWD: $INSTALL_HOME/beosound5c/install/post-update.sh
$INSTALL_USER ALL=(ALL) NOPASSWD: /bin/bash $INSTALL_HOME/beosound5c/services/system/reconcile-services.sh
SUDOEOF
    if visudo -c -f "$SUDOERS_TMP" >/dev/null 2>&1; then
        chmod 440 "$SUDOERS_TMP"
        mv "$SUDOERS_TMP" "$SUDOERS_FILE"
        log_success "Sudoers configured"
    else
        rm -f "$SUDOERS_TMP"
        log_error "Generated sudoers file failed visudo validation — keeping existing"
        return 1
    fi
}
