#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Boot configuration (HDMI + Plymouth cmdline)
# =============================================================================

configure_boot() {
    log_section "Configuring Boot Settings"

    local BOOT_CONFIG="/boot/firmware/config.txt"
    local BOOT_CMDLINE="/boot/firmware/cmdline.txt"

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
        local CURRENT_CMDLINE
        CURRENT_CMDLINE=$(cat "$BOOT_CMDLINE")
        echo "$CURRENT_CMDLINE quiet splash plymouth.ignore-serial-consoles" > "$BOOT_CMDLINE"
        log_success "Plymouth boot parameters added"
    else
        log_info "Plymouth boot parameters already present in $BOOT_CMDLINE"
    fi
}
