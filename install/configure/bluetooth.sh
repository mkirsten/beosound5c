#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Bluetooth remote configuration
# =============================================================================

configure_bluetooth() {
    echo ""
    log_section "Bluetooth Remote (Optional)"

    local current_mac
    current_mac=$(cfg_read '.bluetooth.remote_mac')

    if [ -n "$current_mac" ] && [ "$current_mac" != "" ] && [ "$current_mac" != "00:00:00:00:00:00" ]; then
        log_info "Current BeoRemote MAC: $current_mac"
    fi

    echo ""
    echo "The BeoRemote One works out of the box in IR mode (point at the device)."
    echo "For Bluetooth mode (works from anywhere), you need to pair the remote."
    echo ""
    read -p "Do you want to set up a BeoRemote One via Bluetooth? (y/N): " SETUP_BLUETOOTH

    local BEOREMOTE_MAC="${current_mac:-00:00:00:00:00:00}"
    local BT_DEVICE_NAME=""

    if [[ "$SETUP_BLUETOOTH" =~ ^[Yy]$ ]]; then
        # --- Device name shown on BeoRemote One ---
        echo ""
        log_info "Device Name on BeoRemote One"
        echo ""
        echo "Choose how this BeoSound 5c will appear on the remote's display."
        echo "Examples: BeoVision Frame, Sonos, Spotify, HomeAssistant, Great Room"
        echo ""

        local device_name
        device_name=$(cfg_read '.device')
        local DEFAULT_BT_NAME="BeoSound 5c"
        if [ -n "$device_name" ] && [ "$device_name" != "BeoSound5c" ] && [ "$device_name" != "" ]; then
            DEFAULT_BT_NAME="$device_name"
        fi
        read -p "Device name on remote [$DEFAULT_BT_NAME]: " BT_DEVICE_NAME
        BT_DEVICE_NAME="${BT_DEVICE_NAME:-$DEFAULT_BT_NAME}"

        # --- Pairing loop ---
        while true; do
            echo ""
            log_section "BeoRemote One Pairing"
            echo ""
            echo -e "${YELLOW}Please prepare your BeoRemote One for pairing:${NC}"
            echo ""
            echo "  If this is a NEW remote (never paired):"
            echo "    → It will automatically be in pairing mode when powered on"
            echo ""
            echo "  If the remote was previously paired:"
            echo "    1. Press the LIST button"
            echo "    2. Use UP/DOWN to select SETTINGS, press the center button"
            echo "    3. Select PAIRING, press the center button"
            echo "    4. Select PAIR, press the center button"
            echo ""
            echo "  The remote display should show 'Open for pairing'"
            echo ""
            read -p "Press Enter when the remote is ready for pairing (or 's' to skip): " PAIRING_READY

            if [[ "$PAIRING_READY" =~ ^[Ss]$ ]]; then
                log_info "Skipping Bluetooth pairing"
                break
            fi

            # --- Scanning ---
            echo ""
            log_info "Starting Bluetooth scan..."
            echo ""

            bluetoothctl power on &>/dev/null
            sleep 1

            echo -n "  Scanning for BeoRemote One "
            bluetoothctl --timeout 2 scan on &>/dev/null &

            local SCAN_ATTEMPTS=0
            local MAX_SCAN_ATTEMPTS=15
            local FOUND_MAC=""

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
                echo "  [s] Skip Bluetooth setup"
                echo ""
                read -p "Choose an option: " RETRY_OPTION
                if [[ "$RETRY_OPTION" =~ ^[Ss]$ ]]; then
                    log_info "Skipping Bluetooth pairing"
                    break
                fi
                continue
            fi

            # --- Pair and Trust ---
            echo ""
            log_info "Pairing with BeoRemote One..."
            echo -n "  Pairing "

            bluetoothctl pair "$FOUND_MAC" 2>&1 &
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

                BEOREMOTE_MAC="$FOUND_MAC"

                echo ""
                echo -e "${GREEN}Pairing appears successful!${NC}"
                echo ""
                echo "Please check your BeoRemote One display."
                echo "It should show '$BT_DEVICE_NAME' as a paired device."
                echo ""
                read -p "Does the remote show the pairing was successful? (Y/n): " PAIRING_CONFIRMED

                if [[ ! "$PAIRING_CONFIRMED" =~ ^[Nn]$ ]]; then
                    log_success "BeoRemote One paired successfully!"
                    break
                else
                    echo ""
                    echo "  [r] Retry pairing"
                    echo "  [s] Skip Bluetooth setup"
                    echo ""
                    read -p "Choose an option: " RETRY_OPTION
                    if [[ "$RETRY_OPTION" =~ ^[Ss]$ ]]; then
                        log_info "Skipping Bluetooth pairing"
                        BEOREMOTE_MAC="00:00:00:00:00:00"
                        break
                    fi
                    bluetoothctl remove "$FOUND_MAC" &>/dev/null
                    continue
                fi
            else
                log_warn "Pairing failed"
                echo ""
                echo "  [r] Retry pairing"
                echo "  [s] Skip Bluetooth setup"
                echo ""
                read -p "Choose an option: " RETRY_OPTION
                if [[ "$RETRY_OPTION" =~ ^[Ss]$ ]]; then
                    log_info "Skipping Bluetooth pairing"
                    break
                fi
                continue
            fi
        done
    else
        log_info "Skipping Bluetooth remote setup"
        log_info "You can pair a remote later using: sudo $INSTALL_DIR/tools/bt/pair-remote.sh"
        BT_DEVICE_NAME="BeoSound 5c"
    fi

    cfg_set_str '.bluetooth.remote_mac' "$BEOREMOTE_MAC"
}
