#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Device name configuration
# =============================================================================

configure_device() {
    local current
    current=$(cfg_read '.device')

    if [ -n "$current" ] && [ "$current" != "" ]; then
        echo ""
        log_info "Current device name: $current"
    fi

    local DEVICE_NAME="${current:-BeoSound5c}"
    read -p "Device name/location (e.g., Living Room, Kitchen) [$DEVICE_NAME]: " input
    DEVICE_NAME="${input:-$DEVICE_NAME}"

    cfg_set_str '.device' "$DEVICE_NAME"
    log_success "Device name: $DEVICE_NAME"
}
