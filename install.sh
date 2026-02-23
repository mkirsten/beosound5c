#!/bin/bash
# =============================================================================
# BeoSound 5c Installation Script
# =============================================================================
# Takes a vanilla Raspberry Pi 5 running Raspberry Pi OS to a fully
# operational BeoSound 5c system with interactive configuration.
#
# Usage:
#   sudo ./install.sh                      Full install (all steps)
#   sudo ./install.sh system               System packages, boot, SD card, X11, Plymouth
#   sudo ./install.sh configure            Full interactive configuration wizard
#   sudo ./install.sh configure player     Reconfigure player only
#   sudo ./install.sh configure ha         Reconfigure Home Assistant only
#   sudo ./install.sh configure bluetooth  Pair/re-pair BT remote
#   sudo ./install.sh configure transport  Reconfigure webhook/MQTT
#   sudo ./install.sh configure audio      Reconfigure volume output
#   sudo ./install.sh configure menu       Choose which menu items to show
#   sudo ./install.sh services             Install/restart systemd services
#   sudo ./install.sh verify               Run verification checks
#        ./install.sh status               Show current config + service status
#   sudo ./install.sh help                 Show this usage info
#
# Options:
#   --user USERNAME    Install for specified user (default: $SUDO_USER)
#
# =============================================================================
set -e

# Resolve the directory containing this script
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared definitions
source "$SCRIPT_ROOT/install/lib/common.sh"

# =============================================================================
# Argument parsing
# =============================================================================
INSTALL_USER="${SUDO_USER:-$(whoami)}"
SUBCOMMAND=""
SUBSTEP=""

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
        system|configure|services|verify|status)
            SUBCOMMAND="$1"
            shift
            # Grab optional sub-step for configure
            if [[ "$SUBCOMMAND" == "configure" && $# -gt 0 && ! "$1" =~ ^-- ]]; then
                SUBSTEP="$1"
                shift
            fi
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
INSTALL_DIR="/home/$INSTALL_USER/beosound5c"
CONFIG_DIR="/etc/beosound5c"
CONFIG_FILE="$CONFIG_DIR/config.json"
SECRETS_FILE="$CONFIG_DIR/secrets.env"
PLYMOUTH_THEME_DIR="/usr/share/plymouth/themes/beosound5c"

# =============================================================================
# Source all modules (they define functions, don't execute anything)
# =============================================================================
source "$SCRIPT_ROOT/install/lib/checks.sh"
source "$SCRIPT_ROOT/install/lib/network.sh"
source "$SCRIPT_ROOT/install/lib/config-utils.sh"

source "$SCRIPT_ROOT/install/modules/system-packages.sh"
source "$SCRIPT_ROOT/install/modules/python-packages.sh"
source "$SCRIPT_ROOT/install/modules/udev.sh"
source "$SCRIPT_ROOT/install/modules/user-groups.sh"
source "$SCRIPT_ROOT/install/modules/boot.sh"
source "$SCRIPT_ROOT/install/modules/sd-hardening.sh"
source "$SCRIPT_ROOT/install/modules/x11.sh"
source "$SCRIPT_ROOT/install/modules/plymouth.sh"
source "$SCRIPT_ROOT/install/modules/verify.sh"

source "$SCRIPT_ROOT/install/configure/device.sh"
source "$SCRIPT_ROOT/install/configure/player.sh"
source "$SCRIPT_ROOT/install/configure/homeassistant.sh"
source "$SCRIPT_ROOT/install/configure/bluetooth.sh"
source "$SCRIPT_ROOT/install/configure/spotify.sh"
source "$SCRIPT_ROOT/install/configure/transport.sh"
source "$SCRIPT_ROOT/install/configure/audio.sh"
source "$SCRIPT_ROOT/install/configure/menu.sh"
source "$SCRIPT_ROOT/install/configure/generate.sh"
source "$SCRIPT_ROOT/install/configure/wizard.sh"

# =============================================================================
# Helper: show usage
# =============================================================================
show_help() {
    echo "Usage: sudo $0 [--user USERNAME] [COMMAND [STEP]]"
    echo ""
    echo "Commands:"
    echo "  (none)             Full install (all steps below in sequence)"
    echo "  system             System packages, boot config, SD hardening, X11, Plymouth"
    echo "  configure          Full interactive configuration wizard"
    echo "  configure STEP     Reconfigure a single section (see steps below)"
    echo "  services           Install/restart systemd services"
    echo "  verify             Run verification checks"
    echo "  status             Show current config + service status (no sudo needed)"
    echo "  help               Show this help"
    echo ""
    echo "Configure steps:"
    echo "  device             Device name"
    echo "  player             Player type and IP (Sonos/BlueSound/Local)"
    echo "  ha                 Home Assistant URL, webhook, dashboard, token"
    echo "  bluetooth          Pair/re-pair BeoRemote One"
    echo "  spotify            Spotify integration"
    echo "  transport          Webhook/MQTT transport"
    echo "  audio              Volume output and adapter"
    echo "  menu               Choose which menu items to show"
    echo ""
    echo "Options:"
    echo "  --user USERNAME    Install for specified user (default: \$SUDO_USER)"
    echo ""
    echo "Examples:"
    echo "  sudo ./install.sh                      # Full install"
    echo "  sudo ./install.sh configure player      # Change player only"
    echo "  sudo ./install.sh configure bluetooth   # Re-pair BT remote"
    echo "  sudo ./install.sh services              # Reinstall/restart services"
    echo "  ./install.sh status                     # View current config"
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
# Helper: install services
# =============================================================================
install_services() {
    log_section "Installing System Services"

    local SERVICE_SCRIPT="$INSTALL_DIR/services/system/install-services.sh"

    if [ ! -f "$CONFIG_FILE" ]; then
        log_error "No configuration found at $CONFIG_FILE"
        log_error "Run: sudo $0 configure"
        exit 1
    fi

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
    harden_sd_card
    configure_x11
    install_plymouth_theme
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
            log_info "Run: sudo $0 configure"
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

    configure)
        show_banner
        # configure doesn't need full preflight (may not be root for read-only steps)
        if [ "$EUID" -ne 0 ]; then
            log_error "Configuration requires root (use sudo)"
            exit 1
        fi
        run_wizard "$SUBSTEP"
        show_summary
        ;;

    services)
        show_banner
        if [ "$EUID" -ne 0 ]; then
            log_error "Service installation requires root (use sudo)"
            exit 1
        fi
        install_services
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
        run_wizard
        install_services
        run_verification || true
        show_summary

        # Show verification result
        if [ $FAILED_CHECKS -eq 0 ]; then
            echo -e "  ${GREEN}✓ All verification checks passed${NC}"
        else
            echo -e "  ${YELLOW}⚠ ${FAILED_CHECKS} verification check(s) need attention${NC}"
        fi

        # Next steps
        echo ""
        echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${CYAN}║${NC}  ${YELLOW}Next Steps${NC}                                                ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  1. Reboot to apply all changes:                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}sudo reboot${NC}                                             ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  2. After reboot, verify services are running:            ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}systemctl status beo-*${NC}                                  ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  3. View live logs:                                       ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}journalctl -u beo-ui -f${NC}                                 ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}╠══════════════════════════════════════════════════════════╣${NC}"
        echo -e "${CYAN}║${NC}                                                          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}  ${YELLOW}To modify settings later:${NC}                                ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}sudo ./install.sh configure player${NC}   (player)          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}sudo ./install.sh configure ha${NC}       (HA)              ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}sudo ./install.sh configure audio${NC}    (volume)          ${CYAN}║${NC}"
        echo -e "${CYAN}║${NC}     ${GREEN}sudo ./install.sh status${NC}             (overview)        ${CYAN}║${NC}"
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
