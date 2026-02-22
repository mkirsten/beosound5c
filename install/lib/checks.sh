#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Pre-flight checks
# =============================================================================
# Sourced by install.sh. Uses globals and logging from common.sh.

run_preflight_checks() {
    log_section "Pre-flight Checks"

    # Must run as root
    if [ "$EUID" -ne 0 ]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
    log_success "Running as root"

    # Check if user exists
    if ! id "$INSTALL_USER" &>/dev/null; then
        log_error "User '$INSTALL_USER' does not exist"
        exit 1
    fi
    log_success "User '$INSTALL_USER' exists"

    # Check if running on Raspberry Pi 5
    if [ -f /proc/device-tree/model ]; then
        MODEL=$(cat /proc/device-tree/model)
        if [[ "$MODEL" == *"Raspberry Pi 5"* ]]; then
            log_success "Raspberry Pi 5 detected: $MODEL"
        else
            log_warn "Not a Raspberry Pi 5: $MODEL"
            log_warn "Some features may not work correctly"
        fi
    else
        log_warn "Could not detect hardware model - proceeding anyway"
    fi

    # Check internet connectivity
    if ping -c 1 -W 5 google.com &>/dev/null; then
        log_success "Internet connectivity confirmed"
    else
        log_error "No internet connectivity - required for package installation"
        exit 1
    fi

    # Check if install directory exists
    if [ ! -d "$INSTALL_DIR" ]; then
        log_error "Installation directory not found: $INSTALL_DIR"
        log_error "Please clone the repository first:"
        log_error "  git clone <repo-url> $INSTALL_DIR"
        exit 1
    fi
    log_success "Installation directory found: $INSTALL_DIR"
}
