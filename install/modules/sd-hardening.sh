#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — SD card longevity hardening
# =============================================================================

harden_sd_card() {
    log_section "SD Card Longevity Hardening"

    local FSTAB="/etc/fstab"
    local JOURNALD_CONF="/etc/systemd/journald.conf"

    # --- Disable swap ---
    log_info "Disabling swap..."
    if command -v dphys-swapfile &>/dev/null; then
        dphys-swapfile swapoff 2>/dev/null
        dphys-swapfile uninstall 2>/dev/null
        systemctl disable dphys-swapfile 2>/dev/null
        echo "CONF_SWAPSIZE=0" > /etc/dphys-swapfile
        rm -f /var/swap
        log_success "Swap disabled and swap file removed"
    else
        log_info "dphys-swapfile not installed — no swap to disable"
    fi

    # --- Add commit=120 to root filesystem ---
    log_info "Configuring filesystem mount options..."
    if grep -q "commit=" "$FSTAB"; then
        log_info "commit= already set in fstab"
    else
        sed -i '/[[:space:]]\/[[:space:]].*ext4/ s/defaults,noatime/defaults,noatime,commit=120/' "$FSTAB"
        if ! grep -q "commit=" "$FSTAB"; then
            sed -i '/[[:space:]]\/[[:space:]].*ext4/ s/defaults/defaults,noatime,commit=120/' "$FSTAB"
        fi
        if grep -q "commit=120" "$FSTAB"; then
            log_success "Added commit=120 to root filesystem (reduces write frequency)"
        else
            log_warn "Could not add commit=120 — check /etc/fstab manually"
        fi
    fi

    # --- Mount /tmp as tmpfs ---
    if grep -q "tmpfs.*/tmp " "$FSTAB"; then
        log_info "/tmp tmpfs already configured in fstab"
    else
        echo "tmpfs /tmp tmpfs defaults,noatime,nosuid,nodev,size=200M,mode=1777 0 0" >> "$FSTAB"
        log_success "Added /tmp as 200MB tmpfs"
    fi

    # --- Mount /var/log as tmpfs ---
    if grep -q "tmpfs.*/var/log" "$FSTAB"; then
        log_info "/var/log tmpfs already configured in fstab"
    else
        echo "tmpfs /var/log tmpfs defaults,noatime,nosuid,nodev,size=50M,mode=0755 0 0" >> "$FSTAB"
        log_success "Added /var/log as 50MB tmpfs"
    fi

    # --- Configure journald for volatile storage ---
    log_info "Configuring journald for volatile storage..."
    if grep -q "^Storage=volatile" "$JOURNALD_CONF" 2>/dev/null; then
        log_info "journald already set to volatile"
    else
        sed -i 's/^#\?Storage=.*/Storage=volatile/' "$JOURNALD_CONF"
        if ! grep -q "^Storage=volatile" "$JOURNALD_CONF"; then
            echo "Storage=volatile" >> "$JOURNALD_CONF"
        fi
        log_success "journald set to volatile (logs stay in RAM)"
    fi

    # --- Disable journald rate limiting ---
    sed -i 's/^#\?RateLimitIntervalSec=.*/RateLimitIntervalSec=0/' "$JOURNALD_CONF"
    sed -i 's/^#\?RateLimitBurst=.*/RateLimitBurst=0/' "$JOURNALD_CONF"

    # --- Create tmpfiles.d config for /var/log subdirectories ---
    log_info "Creating tmpfiles.d config for /var/log..."
    cat > /etc/tmpfiles.d/var-log.conf << 'EOF'
# Create /var/log subdirectories on tmpfs
d /var/log 0755 root root -
d /var/log/apt 0755 root root -
d /var/log/cups 0755 root root -
d /var/log/journal 2755 root systemd-journal -
d /var/log/lightdm 0710 root root -
d /var/log/nginx 0755 root adm -
d /var/log/private 0700 root root -
EOF
    log_success "tmpfiles.d config created"

    # --- Redirect Xorg logs to tmpfs ---
    local XORG_LOG_DIR="/home/$INSTALL_USER/.local/share/xorg"
    if [ -L "$XORG_LOG_DIR" ]; then
        log_info "Xorg log directory already symlinked"
    else
        rm -rf "$XORG_LOG_DIR"
        sudo -u "$INSTALL_USER" mkdir -p "/home/$INSTALL_USER/.local/share"
        sudo -u "$INSTALL_USER" ln -s /tmp "$XORG_LOG_DIR"
        log_success "Xorg logs redirected to tmpfs (/tmp)"
    fi

    # --- Mount ~/.cache as tmpfs ---
    local USER_CACHE="/home/$INSTALL_USER/.cache"
    local USER_UID
    local USER_GID
    USER_UID=$(id -u "$INSTALL_USER")
    USER_GID=$(id -g "$INSTALL_USER")
    if grep -q "$USER_CACHE" "$FSTAB"; then
        log_info "~/.cache tmpfs already configured in fstab"
    else
        echo "tmpfs $USER_CACHE tmpfs defaults,noatime,nosuid,nodev,size=300M,uid=$USER_UID,gid=$USER_GID,mode=0700 0 0" >> "$FSTAB"
        log_success "Added ~/.cache as 300MB tmpfs"
    fi

    # --- Redirect ~/.fehbg to tmpfs ---
    local FEHBG="/home/$INSTALL_USER/.fehbg"
    if [ -L "$FEHBG" ]; then
        log_info "~/.fehbg already symlinked"
    else
        rm -f "$FEHBG"
        sudo -u "$INSTALL_USER" ln -s /tmp/.fehbg "$FEHBG"
        log_success "~/.fehbg redirected to tmpfs"
    fi

    # --- Redirect WirePlumber state to tmpfs ---
    local WIREPLUMBER_DIR="/home/$INSTALL_USER/.local/state/wireplumber"
    if [ -L "$WIREPLUMBER_DIR" ]; then
        log_info "WirePlumber state already symlinked"
    else
        rm -rf "$WIREPLUMBER_DIR"
        sudo -u "$INSTALL_USER" mkdir -p "/home/$INSTALL_USER/.local/state"
        sudo -u "$INSTALL_USER" ln -s /tmp "$WIREPLUMBER_DIR"
        log_success "WirePlumber state redirected to tmpfs"
    fi

    # --- Disable WirePlumber Bluetooth audio monitor ---
    local WP_BT_OVERRIDE="/home/$INSTALL_USER/.config/wireplumber/bluetooth.lua.d"
    local WP_BT_SYSTEM="/usr/share/wireplumber/bluetooth.lua.d"
    if [ -d "$WP_BT_SYSTEM" ]; then
        if [ -f "$WP_BT_OVERRIDE/90-enable-all.lua" ] && grep -q "disabled" "$WP_BT_OVERRIDE/90-enable-all.lua" 2>/dev/null; then
            log_info "WirePlumber Bluetooth monitor already disabled"
        else
            log_info "Disabling WirePlumber Bluetooth audio monitor..."
            sudo -u "$INSTALL_USER" mkdir -p "$WP_BT_OVERRIDE"
            cp "$WP_BT_SYSTEM/00-functions.lua" "$WP_BT_OVERRIDE/"
            chown "$INSTALL_USER:$INSTALL_USER" "$WP_BT_OVERRIDE/00-functions.lua"
            echo '-- Bluetooth audio monitor disabled (BS5c only uses BLE HID, not A2DP)' | sudo -u "$INSTALL_USER" tee "$WP_BT_OVERRIDE/90-enable-all.lua" > /dev/null
            log_success "WirePlumber Bluetooth monitor disabled (saves ~40% CPU on bluetoothd)"
        fi
    else
        log_info "WirePlumber bluetooth.lua.d not found — skipping"
    fi

    # --- Symlink ~/.config/chromium to tmpfs ---
    local CHROMIUM_CFG="/home/$INSTALL_USER/.config/chromium"
    if [ -L "$CHROMIUM_CFG" ]; then
        log_info "~/.config/chromium already symlinked"
    else
        rm -rf "$CHROMIUM_CFG"
        sudo -u "$INSTALL_USER" mkdir -p "/home/$INSTALL_USER/.config"
        sudo -u "$INSTALL_USER" ln -s /tmp/chromium-profile "$CHROMIUM_CFG"
        log_success "~/.config/chromium redirected to tmpfs"
    fi

    # --- Disable unnecessary services ---
    log_info "Disabling unnecessary services..."
    for svc in cups cups-browsed cups.socket cups.path; do
        systemctl disable --now "$svc" 2>/dev/null
        systemctl mask "$svc" 2>/dev/null
    done
    for timer in apt-daily.timer apt-daily-upgrade.timer man-db.timer logrotate.timer dpkg-db-backup.timer; do
        systemctl disable --now "$timer" 2>/dev/null
    done
    log_success "Disabled CUPS, apt-daily, logrotate, man-db, dpkg-db-backup"

    log_success "SD card hardening complete"
}
