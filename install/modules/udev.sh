#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — udev rules
# =============================================================================

install_udev_rules() {
    log_section "Configuring udev Rules"

    local udev_file="/etc/udev/rules.d/99-bs5.rules"

    log_info "Creating udev rules for BeoSound 5 hardware..."
    cat > "$udev_file" << 'EOF'
# BeoSound 5 USB HID device (rotary encoder + buttons)
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", \
  ATTRS{idVendor}=="0cd4", ATTRS{idProduct}=="1112", \
  MODE="0666"

# BeoSound 5 PC2/MasterLink interface
SUBSYSTEM=="usb", ATTR{idVendor}=="0cd4", ATTR{idProduct}=="0101", \
  MODE="0666"

# TTY devices - allow access for X11 kiosk mode
KERNEL=="tty[0-9]*", MODE="0666"
EOF

    log_info "Reloading udev rules..."
    udevadm control --reload-rules
    udevadm trigger

    log_success "udev rules configured"
}
