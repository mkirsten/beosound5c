#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Generate config files (full wizard mode only)
# =============================================================================
# In full-wizard mode, the individual steps write directly to config.json via
# cfg_set(). This function handles the secrets file and any final fixups.

generate_config() {
    log_info "Configuration saved to $CONFIG_FILE"

    # Ensure secrets file exists and has correct permissions
    secrets_ensure
    chmod 600 "$SECRETS_FILE"
    log_success "Secrets saved to $SECRETS_FILE"
    # Note: web/json symlink is created by run_wizard() after all steps
}
