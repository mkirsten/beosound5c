#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Player configuration
# =============================================================================

configure_player() {
    echo ""
    log_section "Player Configuration"

    local current_type current_ip
    current_type=$(cfg_read '.player.type')
    current_ip=$(cfg_read '.player.ip')

    if [ -n "$current_type" ] && [ "$current_type" != "" ]; then
        log_info "Current player: $current_type${current_ip:+ @ $current_ip}"
        echo ""
    fi

    echo "Select the network player type:"
    echo ""
    echo "  1) Sonos      - Sonos speaker (most common)"
    echo "  2) BlueSound  - BlueSound player"
    echo "  3) Local      - Local from the BeoSound 5c"
    echo ""

    # Determine default based on current config
    local default_choice="1"
    case "$current_type" in
        sonos)     default_choice="1" ;;
        bluesound) default_choice="2" ;;
        none)      default_choice="3" ;;
    esac

    local PLAYER_TYPE=""
    local PLAYER_IP=""

    while true; do
        read -p "Select player type [1-3, default $default_choice]: " PLAYER_CHOICE
        PLAYER_CHOICE="${PLAYER_CHOICE:-$default_choice}"
        case "$PLAYER_CHOICE" in
            1) PLAYER_TYPE="sonos"; break ;;
            2) PLAYER_TYPE="bluesound"; break ;;
            3) PLAYER_TYPE="none"; break ;;
            *) echo "Invalid selection. Please enter 1, 2, or 3." ;;
        esac
    done

    if [[ "$PLAYER_TYPE" == "sonos" ]]; then
        # Scan for Sonos devices
        mapfile -t sonos_results < <(scan_sonos_devices)

        if [ ${#sonos_results[@]} -gt 0 ]; then
            local sonos_display=()
            for result in "${sonos_results[@]}"; do
                local ip name
                ip=$(echo "$result" | cut -d'|' -f1)
                name=$(echo "$result" | cut -d'|' -f2)
                sonos_display+=("$name ($ip)")
            done

            log_success "Found ${#sonos_results[@]} Sonos device(s)!"

            if selection=$(select_from_list "Select Sonos speaker to control:" "${sonos_display[@]}"); then
                PLAYER_IP=$(echo "$selection" | grep -oP '\(([0-9.]+)\)' | tr -d '()')
            else
                local default_ip="${current_ip:-192.168.1.100}"
                read -p "Enter Sonos speaker IP address [$default_ip]: " PLAYER_IP
                PLAYER_IP="${PLAYER_IP:-$default_ip}"
            fi
        else
            log_warn "No Sonos devices found on the network"
            log_info "Make sure your Sonos speaker is powered on and connected to the same network"
            local default_ip="${current_ip:-192.168.1.100}"
            read -p "Enter Sonos speaker IP address [$default_ip]: " PLAYER_IP
            PLAYER_IP="${PLAYER_IP:-$default_ip}"
        fi
    elif [[ "$PLAYER_TYPE" == "bluesound" ]]; then
        # Scan for Bluesound devices
        mapfile -t bluesound_results < <(scan_bluesound_devices)

        if [ ${#bluesound_results[@]} -gt 0 ]; then
            local bluesound_display=()
            for result in "${bluesound_results[@]}"; do
                local ip name
                ip=$(echo "$result" | cut -d'|' -f1)
                name=$(echo "$result" | cut -d'|' -f2)
                bluesound_display+=("$name ($ip)")
            done

            log_success "Found ${#bluesound_results[@]} Bluesound device(s)!"

            if selection=$(select_from_list "Select Bluesound player to control:" "${bluesound_display[@]}"); then
                PLAYER_IP=$(echo "$selection" | grep -oP '\(([0-9.]+)\)' | tr -d '()')
            else
                local default_ip="${current_ip:-192.168.1.100}"
                read -p "Enter Bluesound player IP address [$default_ip]: " PLAYER_IP
                PLAYER_IP="${PLAYER_IP:-$default_ip}"
            fi
        else
            log_warn "No Bluesound devices found on the network"
            log_info "Make sure your Bluesound player is powered on and connected to the same network"
            local default_ip="${current_ip:-192.168.1.100}"
            read -p "Enter Bluesound player IP address [$default_ip]: " PLAYER_IP
            PLAYER_IP="${PLAYER_IP:-$default_ip}"
        fi
    fi

    local tmp
    tmp=$(mktemp)
    if jq --arg t "$PLAYER_TYPE" --arg ip "$PLAYER_IP" \
        '.player.type = $t | .player.ip = $ip' "$CONFIG_FILE" > "$tmp"; then
        mv "$tmp" "$CONFIG_FILE"; chmod 644 "$CONFIG_FILE"
    else
        rm -f "$tmp"; log_error "Failed to update config.json"
    fi
    log_success "Player: $PLAYER_TYPE${PLAYER_IP:+ @ $PLAYER_IP}"

    # Export for use by other configure steps in full-wizard mode
    _PLAYER_TYPE="$PLAYER_TYPE"
    _PLAYER_IP="$PLAYER_IP"
}
