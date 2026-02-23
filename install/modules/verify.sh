#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Verification checks
# =============================================================================

run_verification() {
    log_section "Verification"

    FAILED_CHECKS=0

    # Check apt packages
    log_info "Checking installed packages..."
    # Chromium package name varies: 'chromium-browser' (Bullseye) vs 'chromium' (Bookworm+)
    local CHROMIUM_PKG="chromium-browser"
    dpkg -l chromium-browser &>/dev/null || CHROMIUM_PKG="chromium"
    local REQUIRED_PACKAGES="$CHROMIUM_PKG python3 python3-hidapi bluetooth plymouth"
    for pkg in $REQUIRED_PACKAGES; do
        if dpkg -l "$pkg" &>/dev/null; then
            log_success "Package installed: $pkg"
        else
            log_error "Package missing: $pkg"
            ((FAILED_CHECKS++))
        fi
    done

    # Check udev rules
    if [ -f "/etc/udev/rules.d/99-bs5.rules" ]; then
        log_success "udev rules installed"
    else
        log_error "udev rules missing"
        ((FAILED_CHECKS++))
    fi

    # Check user groups
    local USER_GROUPS
    USER_GROUPS=$(groups "$INSTALL_USER")
    for grp in video input bluetooth tty; do
        if echo "$USER_GROUPS" | grep -q "\b$grp\b"; then
            log_success "User in group: $grp"
        else
            log_error "User not in group: $grp"
            ((FAILED_CHECKS++))
        fi
    done

    # Check X11 wrapper config
    if [ -f "/etc/X11/Xwrapper.config" ]; then
        log_success "X11 wrapper config exists"
    else
        log_warn "X11 wrapper config missing"
    fi

    # Check Plymouth theme
    if plymouth-set-default-theme 2>/dev/null | grep -q "beosound5c"; then
        log_success "Plymouth theme set: beosound5c"
    else
        log_warn "Plymouth theme not set as default"
    fi

    # Check config file
    if [ -f "$CONFIG_FILE" ]; then
        log_success "Configuration file exists: $CONFIG_FILE"
    else
        log_error "Configuration file missing"
        ((FAILED_CHECKS++))
    fi

    # Check service files have no unresolved placeholders
    if grep -rl '__USER__\|__HOME__' /etc/systemd/system/beo-*.service &>/dev/null; then
        log_error "Service files contain unresolved placeholders (__USER__/__HOME__)"
        log_error "Re-run: sudo ./install.sh services"
        ((FAILED_CHECKS++))
    else
        log_success "Service files configured for user: $INSTALL_USER"
    fi

    # Check services
    log_info "Checking service status..."
    local player_type
    player_type=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('player',{}).get('type','sonos'))" 2>/dev/null || echo "sonos")
    local SERVICES="beo-http beo-input beo-ui"
    if [ "$player_type" != "none" ]; then
        SERVICES="beo-http beo-player-${player_type} beo-input beo-ui"
    fi
    for svc in $SERVICES; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            log_success "Service running: $svc"
        else
            local status
            status=$(systemctl is-active "$svc" 2>/dev/null || echo "not-found")
            log_warn "Service not running: $svc ($status)"
        fi
    done

    # Test HTTP server
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ 2>/dev/null | grep -q "200"; then
        log_success "HTTP server responding on port 8000"
    else
        log_warn "HTTP server not responding (may need reboot)"
    fi

    return $FAILED_CHECKS
}
