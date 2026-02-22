#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — User group membership
# =============================================================================

configure_user_groups() {
    log_section "Configuring User Groups"

    log_info "Adding $INSTALL_USER to required groups..."
    usermod -aG video,input,bluetooth,dialout,tty "$INSTALL_USER"

    log_success "User added to groups: video, input, bluetooth, dialout, tty"
}
