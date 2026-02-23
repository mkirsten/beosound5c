#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Python package installation
# =============================================================================

install_python_packages() {
    log_section "Installing Python Packages"

    log_info "Installing Python packages via pip..."
    pip3 install --break-system-packages -r "$INSTALL_DIR/requirements.txt"

    log_info "Installing CD service Python dependencies..."
    pip3 install --break-system-packages -q discid musicbrainzngs

    log_success "Python packages installed"
}
