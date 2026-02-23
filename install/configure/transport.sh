#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Transport configuration
# =============================================================================

configure_transport() {
    echo ""
    log_section "Transport Configuration"

    local current_mode current_broker current_port
    current_mode=$(cfg_read '.transport.mode')
    current_broker=$(cfg_read '.transport.mqtt_broker')
    current_port=$(cfg_read '.transport.mqtt_port')

    if [ -n "$current_mode" ] && [ "$current_mode" != "" ]; then
        log_info "Current transport mode: $current_mode"
        if [ -n "$current_broker" ]; then
            log_info "Current MQTT broker: $current_broker:${current_port:-1883}"
        fi
    fi

    echo ""
    echo "BeoSound 5c can communicate with Home Assistant via:"
    echo ""
    echo "  1) Webhook  - HTTP POST requests (default, works out of the box)"
    echo "  2) MQTT     - Persistent connection via MQTT broker (lower latency, bidirectional)"
    echo "  3) Both     - Send events via both webhook AND MQTT"
    echo ""
    echo "MQTT requires the Mosquitto add-on or another MQTT broker running on your network."
    echo ""

    # Determine default based on current config
    local default_choice="1"
    case "$current_mode" in
        webhook) default_choice="1" ;;
        mqtt)    default_choice="2" ;;
        both)    default_choice="3" ;;
    esac

    local TRANSPORT_MODE="webhook"
    local MQTT_BROKER=""
    local MQTT_PORT="1883"
    local MQTT_USER=""
    local MQTT_PASSWORD=""

    while true; do
        read -p "Select transport mode [1-3, default $default_choice]: " TRANSPORT_CHOICE
        TRANSPORT_CHOICE="${TRANSPORT_CHOICE:-$default_choice}"
        case "$TRANSPORT_CHOICE" in
            1) TRANSPORT_MODE="webhook"; break ;;
            2) TRANSPORT_MODE="mqtt"; break ;;
            3) TRANSPORT_MODE="both"; break ;;
            *) echo "Invalid selection. Please enter 1, 2, or 3." ;;
        esac
    done
    log_success "Transport mode: $TRANSPORT_MODE"

    if [[ "$TRANSPORT_MODE" == "mqtt" || "$TRANSPORT_MODE" == "both" ]]; then
        echo ""
        log_info "MQTT Broker Configuration"
        echo ""

        local DEFAULT_MQTT_BROKER="${current_broker:-homeassistant.local}"
        read -p "MQTT broker hostname [$DEFAULT_MQTT_BROKER]: " MQTT_BROKER
        MQTT_BROKER="${MQTT_BROKER:-$DEFAULT_MQTT_BROKER}"

        local DEFAULT_MQTT_PORT="${current_port:-1883}"
        read -p "MQTT broker port [$DEFAULT_MQTT_PORT]: " MQTT_PORT
        MQTT_PORT="${MQTT_PORT:-$DEFAULT_MQTT_PORT}"

        # Read existing MQTT creds from secrets
        local existing_mqtt_user existing_mqtt_pass
        existing_mqtt_user=$(secret_read "MQTT_USER")
        existing_mqtt_pass=$(secret_read "MQTT_PASSWORD")

        if [ -n "$existing_mqtt_user" ]; then
            echo "(MQTT user '$existing_mqtt_user' already configured. Press Enter to keep.)"
            read -p "MQTT username [$existing_mqtt_user]: " MQTT_USER
            MQTT_USER="${MQTT_USER:-$existing_mqtt_user}"
        else
            read -p "MQTT username (press Enter if none): " MQTT_USER
        fi

        if [[ -n "$MQTT_USER" ]]; then
            if [ -n "$existing_mqtt_pass" ]; then
                echo "(Password already set. Press Enter to keep, or type a new one.)"
                read -s -p "MQTT password: " MQTT_PASSWORD
                echo ""
                MQTT_PASSWORD="${MQTT_PASSWORD:-$existing_mqtt_pass}"
            else
                read -s -p "MQTT password: " MQTT_PASSWORD
                echo ""
            fi
        fi

        log_success "MQTT broker: $MQTT_BROKER:$MQTT_PORT"

        # Test MQTT connectivity
        if command -v mosquitto_pub &>/dev/null; then
            echo -n "  Testing MQTT connection "
            local mqtt_auth=()
            if [[ -n "$MQTT_USER" ]]; then
                mqtt_auth=(-u "$MQTT_USER")
                if [[ -n "$MQTT_PASSWORD" ]]; then
                    mqtt_auth+=(-P "$MQTT_PASSWORD")
                fi
            fi
            if mosquitto_pub -h "$MQTT_BROKER" -p "$MQTT_PORT" "${mqtt_auth[@]}" \
                -t "beosound5c/test" -m "install_test" 2>/dev/null; then
                echo ""
                log_success "MQTT connection successful!"
            else
                echo ""
                log_warn "Could not connect to MQTT broker - check settings later"
            fi
        fi

        # Write MQTT creds to secrets
        secret_set "MQTT_USER" "$MQTT_USER"
        secret_set "MQTT_PASSWORD" "$MQTT_PASSWORD"
    fi

    # Write transport config
    cfg_set_str '.transport.mode' "$TRANSPORT_MODE"
    if [[ "$TRANSPORT_MODE" == "mqtt" || "$TRANSPORT_MODE" == "both" ]]; then
        local tmp
        tmp=$(mktemp)
        if jq --arg broker "$MQTT_BROKER" --argjson port "$MQTT_PORT" \
            '.transport.mqtt_broker = $broker | .transport.mqtt_port = $port' "$CONFIG_FILE" > "$tmp"; then
            mv "$tmp" "$CONFIG_FILE"; chmod 644 "$CONFIG_FILE"
        else
            rm -f "$tmp"; log_error "Failed to update config.json"
        fi
    fi
}
