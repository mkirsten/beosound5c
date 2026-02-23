#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Plymouth boot theme
# =============================================================================

install_plymouth_theme() {
    log_section "Installing Plymouth Boot Theme"

    local SPLASH_SOURCE="$INSTALL_DIR/assets/splashscreen-red.png"
    local PLYMOUTH_SOURCE="$INSTALL_DIR/plymouth"

    if [ -d "$PLYMOUTH_SOURCE" ] && [ -f "$SPLASH_SOURCE" ]; then
        log_info "Creating Plymouth theme directory..."
        mkdir -p "$PLYMOUTH_THEME_DIR"

        log_info "Copying Plymouth theme files..."
        cp "$PLYMOUTH_SOURCE/beosound5c.plymouth" "$PLYMOUTH_THEME_DIR/"
        cp "$PLYMOUTH_SOURCE/beosound5c.script" "$PLYMOUTH_THEME_DIR/"
        cp "$SPLASH_SOURCE" "$PLYMOUTH_THEME_DIR/"

        log_info "Setting Plymouth theme as default..."
        plymouth-set-default-theme beosound5c

        log_info "Updating initramfs (this may take a moment)..."
        if command -v update-initramfs &>/dev/null; then
            update-initramfs -u
        else
            log_warn "update-initramfs not found (RPi OS Lite uses device tree, not initramfs)"
        fi

        log_success "Plymouth theme installed"
    else
        log_warn "Plymouth theme files not found - skipping"
        log_warn "Expected: $PLYMOUTH_SOURCE and $SPLASH_SOURCE"
    fi
}
