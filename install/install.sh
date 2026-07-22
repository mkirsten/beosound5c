#!/bin/bash
# =============================================================================
# BeoSound 5c Installation Script
# =============================================================================
# Takes a vanilla Raspberry Pi 5 running Raspberry Pi OS to a fully
# operational BeoSound 5c system. No interactive configuration — after
# install + reboot the device shows a QR code; scan it with a phone to
# finish setup in the web config UI.
#
# Usage (from repo root):
#   sudo ./install/install.sh                      Full install (all steps)
#   sudo ./install/install.sh system               System packages, boot, SD card, X11, Plymouth
#   sudo ./install/install.sh services             Install/restart systemd services
#   sudo ./install/install.sh verify               Run verification checks
#        ./install/install.sh status               Show current config + service status
#   sudo ./install/install.sh help                 Show this usage info
#
# Options:
#   --user USERNAME    Install for specified user (default: $SUDO_USER)
#
# =============================================================================
set -e

# Resolve the project root (parent of the install/ directory containing this script)
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Source shared definitions
source "$SCRIPT_ROOT/install/lib/common.sh"

# =============================================================================
# Argument parsing
# =============================================================================
INSTALL_USER="${SUDO_USER:-$(whoami)}"
SUBCOMMAND=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --user)
            INSTALL_USER="$2"
            shift 2
            ;;
        --help|-h|help)
            SUBCOMMAND="help"
            shift
            ;;
        system|services|update|verify|status)
            SUBCOMMAND="$1"
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Run: $0 help"
            exit 1
            ;;
    esac
done

# Default to full install when no subcommand given
SUBCOMMAND="${SUBCOMMAND:-full}"

# =============================================================================
# Globals
# =============================================================================
INSTALL_DIR="$SCRIPT_ROOT"
INSTALL_HOME=$(eval echo "~$INSTALL_USER")
CONFIG_DIR="/etc/beosound5c"
CONFIG_FILE="$CONFIG_DIR/config.json"
SECRETS_FILE="$CONFIG_DIR/secrets.env"
PLYMOUTH_THEME_DIR="/usr/share/plymouth/themes/beosound5c"

# =============================================================================
# Source all modules (they define functions, don't execute anything)
# =============================================================================
source "$SCRIPT_ROOT/install/lib/checks.sh"
source "$SCRIPT_ROOT/install/lib/config-utils.sh"

source "$SCRIPT_ROOT/install/modules/system-packages.sh"
source "$SCRIPT_ROOT/install/modules/python-packages.sh"
source "$SCRIPT_ROOT/install/modules/udev.sh"
source "$SCRIPT_ROOT/install/modules/user-groups.sh"
source "$SCRIPT_ROOT/install/modules/boot.sh"
source "$SCRIPT_ROOT/install/modules/sd-hardening.sh"
source "$SCRIPT_ROOT/install/modules/x11.sh"
source "$SCRIPT_ROOT/install/modules/audio-hat.sh"
source "$SCRIPT_ROOT/install/modules/plymouth.sh"
source "$SCRIPT_ROOT/install/modules/librespot.sh"
source "$SCRIPT_ROOT/install/modules/piper.sh"
source "$SCRIPT_ROOT/install/modules/ytdlp.sh"
source "$SCRIPT_ROOT/install/modules/verify.sh"

# =============================================================================
# Helper: show usage
# =============================================================================
show_help() {
    echo "Usage: sudo $0 [--user USERNAME] [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  (none)             Full install — system packages, services, start everything"
    echo "  update             Apply updates after git pull (services + sudoers + pip)"
    echo "  system             System packages, boot config, SD hardening, X11, Plymouth"
    echo "  verify             Run verification checks"
    echo "  status             Show current config + service status (no sudo needed)"
    echo "  help               Show this help"
    echo ""
    echo "Options:"
    echo "  --user USERNAME    Install for specified user (default: \$SUDO_USER)"
    echo ""
    echo "Examples:"
    echo "  sudo ./install/install.sh               # Fresh install"
    echo "  git pull && sudo ./install/install.sh update  # After updating"
    echo "  ./install/install.sh status             # View current config"
    echo ""
    echo "All configuration happens in the web UI: after install + reboot the"
    echo "device shows a QR code — scan it (or open http://<device-ip>/config)"
    echo "to set up player, audio, Home Assistant, and sources from any browser."
}

# =============================================================================
# Helper: show configuration summary
# =============================================================================
show_summary() {
    local DEVICE_NAME PLAYER_TYPE PLAYER_IP HA_URL TRANSPORT_MODE
    local MQTT_BROKER MQTT_PORT OUTPUT_NAME VOLUME_TYPE VOLUME_HOST VOLUME_MAX
    local BEOREMOTE_MAC HA_TOKEN SPOTIFY_REFRESH_TOKEN BT_DEVICE_NAME

    if [ -f "$CONFIG_FILE" ]; then
        DEVICE_NAME=$(cfg_read '.device')
        PLAYER_TYPE=$(cfg_read '.player.type')
        PLAYER_IP=$(cfg_read '.player.ip')
        HA_URL=$(cfg_read '.home_assistant.url')
        TRANSPORT_MODE=$(cfg_read '.transport.mode')
        MQTT_BROKER=$(cfg_read '.transport.mqtt_broker')
        MQTT_PORT=$(cfg_read '.transport.mqtt_port')
        OUTPUT_NAME=$(cfg_read '.volume.output_name')
        VOLUME_TYPE=$(cfg_read '.volume.type')
        VOLUME_HOST=$(cfg_read '.volume.host')
        VOLUME_MAX=$(cfg_read '.volume.max')
        BEOREMOTE_MAC=$(cfg_read '.bluetooth.remote_mac')
    fi
    if [ -f "$SECRETS_FILE" ]; then
        HA_TOKEN=$(secret_read "HA_TOKEN")
    fi

    local SPOTIFY_TOKENS="/etc/beosound5c/spotify_tokens.json"
    if [ -f "$SPOTIFY_TOKENS" ]; then
        SPOTIFY_REFRESH_TOKEN=$(jq -r '.refresh_token // empty' "$SPOTIFY_TOKENS" 2>/dev/null)
    fi

    echo ""
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}          ${GREEN}BeoSound 5c — Configuration Summary${NC}            ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  Device Name:      ${GREEN}${DEVICE_NAME:-Not set}${NC}"
    echo -e "${CYAN}║${NC}  Player:           ${GREEN}${PLAYER_TYPE:-Not set}${PLAYER_IP:+ @ $PLAYER_IP}${NC}"
    echo -e "${CYAN}║${NC}  Home Assistant:   ${GREEN}${HA_URL:-Not set}${NC}"
    if [ -n "$HA_TOKEN" ] && [ "$HA_TOKEN" != "" ]; then
        echo -e "${CYAN}║${NC}  HA Token:         ${GREEN}Configured${NC}"
    else
        echo -e "${CYAN}║${NC}  HA Token:         ${YELLOW}Not configured${NC}"
    fi
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${YELLOW}Transport${NC}"
    echo -e "${CYAN}║${NC}  Mode:             ${GREEN}${TRANSPORT_MODE:-webhook}${NC}"
    if [ -n "$MQTT_BROKER" ] && [[ "$TRANSPORT_MODE" == "mqtt" || "$TRANSPORT_MODE" == "both" ]]; then
        echo -e "${CYAN}║${NC}  MQTT Broker:      ${GREEN}${MQTT_BROKER}:${MQTT_PORT}${NC}"
    fi
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${YELLOW}Audio Output${NC}"
    echo -e "${CYAN}║${NC}  Output:           ${GREEN}${OUTPUT_NAME:-BeoLab 5}${NC}"
    echo -e "${CYAN}║${NC}  Volume Control:   ${GREEN}${VOLUME_TYPE:-beolab5} @ ${VOLUME_HOST:-beolab5-controller.local}${NC}"
    echo -e "${CYAN}║${NC}  Max Volume:       ${GREEN}${VOLUME_MAX:-70}%%${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${YELLOW}Bluetooth Remote${NC}"
    if [ "$BEOREMOTE_MAC" != "00:00:00:00:00:00" ] && [ -n "$BEOREMOTE_MAC" ]; then
        echo -e "${CYAN}║${NC}  Status:           ${GREEN}Paired${NC}"
        echo -e "${CYAN}║${NC}  Remote MAC:       ${GREEN}${BEOREMOTE_MAC}${NC}"
    else
        echo -e "${CYAN}║${NC}  Status:           ${BLUE}Not configured (IR mode works)${NC}"
    fi
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${YELLOW}Spotify${NC}"
    if [ -n "$SPOTIFY_REFRESH_TOKEN" ]; then
        echo -e "${CYAN}║${NC}  Status:           ${GREEN}Connected${NC}"
    else
        echo -e "${CYAN}║${NC}  Status:           ${BLUE}Not configured${NC}"
    fi
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${YELLOW}File Locations${NC}"
    echo -e "${CYAN}║${NC}  Config:  ${GREEN}${CONFIG_FILE}${NC}"
    echo -e "${CYAN}║${NC}  Secrets: ${GREEN}${SECRETS_FILE}${NC}"
    echo -e "${CYAN}║${NC}  Install: ${GREEN}${INSTALL_DIR}${NC}"
    echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# =============================================================================
# Helper: bootstrap default config if none exists yet
# =============================================================================
ensure_default_config() {
    if [ -f "$CONFIG_FILE" ]; then
        return
    fi
    local DEFAULT="$INSTALL_DIR/config/default.json"
    if [ ! -f "$DEFAULT" ]; then
        log_warn "No default config found at $DEFAULT — skipping"
        return
    fi
    mkdir -p "$CONFIG_DIR"
    cp "$DEFAULT" "$CONFIG_FILE"
    # Symlink into web/json so the UI can serve it immediately
    ln -sf "$CONFIG_FILE" "$INSTALL_DIR/web/json/config.json" 2>/dev/null || true
    log_success "Default config installed — open http://<device-ip>/ to configure"
}

# =============================================================================
# Helper: install services
# =============================================================================
install_services() {
    log_section "Installing System Services"

    local SERVICE_SCRIPT="$INSTALL_DIR/services/system/install-services.sh"

    if [ -f "$SERVICE_SCRIPT" ]; then
        log_info "Running service installation script..."

        # Export INSTALL_USER so install-services.sh can use it for placeholder replacement
        export INSTALL_USER
        bash "$SERVICE_SCRIPT"
        log_success "Services installed"
    else
        log_warn "Service installation script not found: $SERVICE_SCRIPT"
        log_warn "You may need to install services manually"
    fi

    # Always re-apply sudoers when services change — new service entries (e.g.
    # post-update.sh, tee /etc/beosound5c/config.json) must be in sync with
    # service files. Safe to call on every 'services' run — configure_user_groups
    # is idempotent (overwrites sudoers, group membership uses -aG).
    configure_user_groups

    # Sync Python packages — new dependencies added in requirements.txt must be
    # installed before services restart, just as post-update.sh does after OTA.
    install_python_packages
}

# =============================================================================
# Helper: telemetry notice (consent is managed in the web config UI)
# =============================================================================
show_telemetry_notice() {
    local optout_file="$INSTALL_DIR/NO_TELEMETRY"
    if [ -f "$optout_file" ]; then
        log_info "Anonymous startup ping: disabled (NO_TELEMETRY present)"
    else
        log_info "Anonymous startup ping: enabled — a tiny anonymous hello to"
        log_info "beosound5c.com on startup (version, source names, player/volume"
        log_info "type, hashed device id — see services/lib/beacon.py)."
        log_info "Turn it off in the web config UI, or: touch $optout_file"
    fi
}

# =============================================================================
# Helper: run system setup (all modules)
# =============================================================================
run_system_setup() {
    install_system_packages
    install_python_packages
    install_udev_rules
    configure_user_groups
    configure_boot
    setup_audio_hat
    harden_sd_card
    configure_x11
    disable_desktop_session
    install_plymouth_theme
    install_librespot
    install_piper
    install_ytdlp
    install_usb_music_support
}

# =============================================================================
# Subcommand dispatch
# =============================================================================
case "$SUBCOMMAND" in
    help)
        show_banner
        show_help
        ;;

    status)
        show_banner
        if [ ! -f "$CONFIG_FILE" ]; then
            log_warn "No configuration found at $CONFIG_FILE"
            log_info "Run: sudo $0 (full install) to get started"
            exit 0
        fi
        show_summary

        # Show service status if on a device with systemd
        if command -v systemctl &>/dev/null; then
            if [ -f "$INSTALL_DIR/services/system/status-services.sh" ]; then
                bash "$INSTALL_DIR/services/system/status-services.sh"
            fi
        fi
        ;;

    system)
        show_banner
        run_preflight_checks
        run_system_setup
        log_success "System setup complete"
        ;;

    services|update)
        show_banner
        if [ "$EUID" -ne 0 ]; then
            log_error "Service installation requires root (use sudo)"
            exit 1
        fi
        install_services
        # `update` follows a git pull — refresh yt-dlp too, since a stale build
        # is broken by YouTube changes (same reason post-update.sh does this).
        if [ "$SUBCOMMAND" = "update" ]; then
            install_ytdlp
        fi
        ;;

    verify)
        show_banner
        if [ "$EUID" -ne 0 ]; then
            log_error "Verification requires root (use sudo)"
            exit 1
        fi
        run_verification || true
        if [ $FAILED_CHECKS -eq 0 ]; then
            echo ""
            log_success "All verification checks passed"
        else
            echo ""
            log_warn "$FAILED_CHECKS verification check(s) need attention"
            exit 1
        fi
        ;;

    full)
        show_banner
        run_preflight_checks
        run_system_setup
        ensure_default_config
        install_services
        show_telemetry_notice
        run_verification || true
        show_summary

        # Show verification result
        if [ $FAILED_CHECKS -eq 0 ]; then
            echo -e "  ${GREEN}✓ All verification checks passed${NC}"
        else
            echo -e "  ${YELLOW}⚠ ${FAILED_CHECKS} verification check(s) need attention${NC}"
        fi

        # Determine device IP for the web UI hint
        DEVICE_IP=$(hostname -I 2>/dev/null | awk '{print $1}')

        echo ""
        echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${CYAN}║${NC}  ${YELLOW}Next Steps${NC}                                                ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  1. Reboot to apply all changes:                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}sudo reboot${NC}                                             ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  2. The device will show a QR code — scan it with a       ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     phone to finish setup in the browser, or open:        ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}http://${DEVICE_IP:-<device-ip>}/config${NC}                            ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  3. After reboot, verify services are running:            ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}systemctl status beo-*${NC}                                  ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  ${YELLOW}After a git pull:${NC}                                         ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}sudo ./install/install.sh update${NC}                        ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  ${YELLOW}Remote support:${NC}                                           ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}bs5c-support${NC}                          (enable)          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}bs5c-support stop${NC}                     (disable)         ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
        echo ""

        if [ $FAILED_CHECKS -gt 0 ]; then
            exit 1
        fi

        # Prompt for reboot
        echo ""
        read -p "Would you like to reboot now to apply all changes? (Y/n): " REBOOT_NOW
        if [[ ! "$REBOOT_NOW" =~ ^[Nn]$ ]]; then
            log_info "Rebooting..."
            sudo reboot
        fi
        ;;
esac
