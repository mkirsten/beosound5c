#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — System package installation
# =============================================================================

install_system_packages() {
    log_section "Installing System Packages"

    log_info "Updating package lists..."
    apt-get update -qq

    log_info "Installing X11 and display packages..."
    apt-get install -y --no-install-recommends \
        xserver-xorg \
        x11-xserver-utils \
        x11-utils \
        xdotool \
        xinit \
        openbox \
        chromium-browser \
        fbi \
        feh \
        unclutter-xfixes

    log_info "Installing Python packages..."
    apt-get install -y \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        libhidapi-hidraw0 \
        libhidapi-dev \
        python3-hidapi \
        python3-hid \
        python3-websockets \
        python3-websocket

    log_info "Installing USB and Bluetooth packages..."
    apt-get install -y \
        libudev-dev \
        libusb-1.0-0-dev \
        bluetooth \
        bluez

    log_info "Installing Plymouth (boot splash)..."
    apt-get install -y \
        plymouth \
        plymouth-themes

    log_info "Installing audio/TTS packages..."
    apt-get install -y \
        espeak-ng

    log_info "Installing utilities..."
    apt-get install -y \
        curl \
        git \
        jq \
        mosquitto-clients

    log_success "System packages installed"
}
