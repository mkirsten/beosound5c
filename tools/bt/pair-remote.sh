#!/bin/bash
# =============================================================================
# BeoRemote One Bluetooth Pairing Script
# =============================================================================
# Scans for a BeoRemote One and pairs it with this device.
# Can be run standalone after initial installation.
#
# Usage: sudo ./pair-remote.sh
# =============================================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Must run as root (bluetoothctl needs it for pairing)
if [ "$EUID" -ne 0 ]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

CONFIG_FILE="/etc/beosound5c/config.json"

echo ""
echo -e "${CYAN}BeoRemote One Bluetooth Pairing${NC}"
echo ""

while true; do
    echo -e "${YELLOW}Please prepare your BeoRemote One for pairing:${NC}"
    echo ""
    echo "  If this is a NEW remote (never paired):"
    echo "    -> It will automatically be in pairing mode when powered on"
    echo ""
    echo "  If the remote was previously paired:"
    echo "    1. Press the LIST button"
    echo "    2. Use UP/DOWN to select SETTINGS, press the center button"
    echo "    3. Select PAIRING, press the center button"
    echo "    4. Select PAIR, press the center button"
    echo ""
    echo "  The remote display should show 'Open for pairing'"
    echo ""
    read -p "Press Enter when the remote is ready for pairing (or 'q' to quit): " PAIRING_READY

    if [[ "$PAIRING_READY" =~ ^[Qq]$ ]]; then
        log_info "Cancelled"
        exit 0
    fi

    # -------------------------------------------------------------------------
    # Scan
    # -------------------------------------------------------------------------
    echo ""
    log_info "Starting Bluetooth scan..."

    bluetoothctl power on &>/dev/null
    sleep 1

    echo -n "  Scanning for BeoRemote One "
    bluetoothctl --timeout 2 scan on &>/dev/null &

    SCAN_ATTEMPTS=0
    MAX_SCAN_ATTEMPTS=15
    FOUND_MAC=""

    while [ $SCAN_ATTEMPTS -lt $MAX_SCAN_ATTEMPTS ]; do
        echo -n "."
        sleep 2

        FOUND_MAC=$(bluetoothctl devices | grep -i "BEORC" | awk '{print $2}' | head -1)

        if [ -n "$FOUND_MAC" ]; then
            echo ""
            log_success "Found BeoRemote One: $FOUND_MAC"
            break
        fi

        ((SCAN_ATTEMPTS++))
    done

    bluetoothctl scan off &>/dev/null

    if [ -z "$FOUND_MAC" ]; then
        echo ""
        log_warn "BeoRemote One not found"
        echo ""
        echo "  [r] Retry scanning"
        echo "  [q] Quit"
        echo ""
        read -p "Choose an option: " RETRY_OPTION

        if [[ "$RETRY_OPTION" =~ ^[Qq]$ ]]; then
            exit 0
        fi
        continue
    fi

    # -------------------------------------------------------------------------
    # Pair and Trust
    # -------------------------------------------------------------------------
    echo ""
    log_info "Pairing with BeoRemote One..."
    echo -n "  Pairing "

    bluetoothctl pair "$FOUND_MAC" &>/dev/null &

    for i in {1..10}; do
        echo -n "."
        sleep 1
    done
    echo ""

    if bluetoothctl info "$FOUND_MAC" 2>/dev/null | grep -q "Paired: yes"; then
        log_success "Paired successfully!"

        echo -n "  Trusting device "
        bluetoothctl trust "$FOUND_MAC" &>/dev/null
        for i in {1..3}; do
            echo -n "."
            sleep 1
        done
        echo ""
        log_success "Device trusted"

        echo -n "  Connecting "
        bluetoothctl connect "$FOUND_MAC" &>/dev/null &
        for i in {1..5}; do
            echo -n "."
            sleep 1
        done
        echo ""

        if bluetoothctl info "$FOUND_MAC" 2>/dev/null | grep -q "Connected: yes"; then
            log_success "Connected!"
        else
            log_info "Connection will be established when remote is used"
        fi

        # Update config.json if it exists
        if [ -f "$CONFIG_FILE" ] && command -v jq &>/dev/null; then
            CURRENT_MAC=$(jq -r '.bluetooth.remote_mac // empty' "$CONFIG_FILE" 2>/dev/null)
            if [ "$CURRENT_MAC" != "$FOUND_MAC" ]; then
                echo ""
                read -p "Update config.json with MAC $FOUND_MAC? (Y/n): " UPDATE_CONFIG
                if [[ ! "$UPDATE_CONFIG" =~ ^[Nn]$ ]]; then
                    TMP=$(mktemp)
                    jq --arg mac "$FOUND_MAC" '.bluetooth.remote_mac = $mac' "$CONFIG_FILE" > "$TMP" && mv "$TMP" "$CONFIG_FILE"
                    log_success "Updated $CONFIG_FILE"
                    log_info "Restart services with: sudo systemctl restart beo-bluetooth"
                fi
            fi
        fi

        echo ""
        log_success "BeoRemote One pairing complete!"
        echo ""
        echo "  MAC address: $FOUND_MAC"
        echo ""
        exit 0
    else
        log_warn "Pairing failed"
        echo ""
        echo "  [r] Retry pairing"
        echo "  [q] Quit"
        echo ""
        read -p "Choose an option: " RETRY_OPTION

        if [[ "$RETRY_OPTION" =~ ^[Qq]$ ]]; then
            exit 1
        fi
        continue
    fi
done
