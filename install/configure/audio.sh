#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Audio output / volume configuration
# =============================================================================

configure_audio() {
    echo ""
    log_section "Audio Output Configuration"

    local current_output current_vol_type current_vol_host current_vol_max
    current_output=$(cfg_read '.volume.output_name')
    current_vol_type=$(cfg_read '.volume.type')
    current_vol_host=$(cfg_read '.volume.host')
    current_vol_max=$(cfg_read '.volume.max')

    if [ -n "$current_vol_type" ] && [ "$current_vol_type" != "" ]; then
        log_info "Current: $current_output, volume: $current_vol_type @ ${current_vol_host:-local} (max ${current_vol_max:-70}%)"
    fi

    echo ""
    echo "Configure how BeoSound 5c controls volume on your speakers."
    echo ""

    local DEFAULT_OUTPUT_NAME="${current_output:-BeoLab 5}"
    read -p "Audio output name (shown in UI) [$DEFAULT_OUTPUT_NAME]: " OUTPUT_NAME
    OUTPUT_NAME="${OUTPUT_NAME:-$DEFAULT_OUTPUT_NAME}"

    echo ""
    echo "Volume control method:"
    echo ""

    # Read player type from config (may have been set earlier in this session)
    local player_type
    player_type="${_PLAYER_TYPE:-$(cfg_read '.player.type')}"
    local player_ip
    player_ip="${_PLAYER_IP:-$(cfg_read '.player.ip')}"

    local VOLUME_TYPE=""
    local VOLUME_HOST=""
    local VOLUME_MAX="${current_vol_max:-70}"
    local VOLUME_ZONE=""
    local VOLUME_INPUT=""
    local VOLUME_MIXER_PORT=""

    # Determine default choice based on current config
    local default_vol_choice="1"

    if [[ "$player_type" == "sonos" ]]; then
        echo "  1) Sonos       - Control volume directly on the Sonos speaker (Recommended)"
        echo "  2) BeoLab 5    - BeoLab 5 via controller REST API"
        echo "  3) PowerLink   - B&O speakers via MasterLink mixer"
        echo "  4) C4 Amp      - Control4 amplifier via UDP"
        echo "  5) HDMI        - HDMI audio output (ALSA software volume)"
        echo "  6) S/PDIF      - S/PDIF HAT output (ALSA software volume)"
        echo "  7) RCA         - RCA analog output (no volume control)"
        echo ""

        case "$current_vol_type" in
            sonos)     default_vol_choice="1" ;;
            beolab5)   default_vol_choice="2" ;;
            powerlink) default_vol_choice="3" ;;
            c4amp)     default_vol_choice="4" ;;
            hdmi)      default_vol_choice="5" ;;
            spdif)     default_vol_choice="6" ;;
            rca)       default_vol_choice="7" ;;
        esac

        while true; do
            read -p "Select volume control [1-7, default $default_vol_choice]: " VOLUME_CHOICE
            VOLUME_CHOICE="${VOLUME_CHOICE:-$default_vol_choice}"
            case "$VOLUME_CHOICE" in
                1) VOLUME_TYPE="sonos"; break ;;
                2) VOLUME_TYPE="beolab5"; break ;;
                3) VOLUME_TYPE="powerlink"; break ;;
                4) VOLUME_TYPE="c4amp"; break ;;
                5) VOLUME_TYPE="hdmi"; break ;;
                6) VOLUME_TYPE="spdif"; break ;;
                7) VOLUME_TYPE="rca"; break ;;
                *) echo "Invalid selection. Please enter 1-7." ;;
            esac
        done
    elif [[ "$player_type" == "bluesound" ]]; then
        echo "  1) BlueSound   - Control volume directly on the BlueSound player (Recommended)"
        echo "  2) BeoLab 5    - BeoLab 5 via controller REST API"
        echo "  3) PowerLink   - B&O speakers via MasterLink mixer"
        echo "  4) C4 Amp      - Control4 amplifier via UDP"
        echo "  5) HDMI        - HDMI audio output (ALSA software volume)"
        echo "  6) S/PDIF      - S/PDIF HAT output (ALSA software volume)"
        echo "  7) RCA         - RCA analog output (no volume control)"
        echo ""

        case "$current_vol_type" in
            bluesound) default_vol_choice="1" ;;
            beolab5)   default_vol_choice="2" ;;
            powerlink) default_vol_choice="3" ;;
            c4amp)     default_vol_choice="4" ;;
            hdmi)      default_vol_choice="5" ;;
            spdif)     default_vol_choice="6" ;;
            rca)       default_vol_choice="7" ;;
        esac

        while true; do
            read -p "Select volume control [1-7, default $default_vol_choice]: " VOLUME_CHOICE
            VOLUME_CHOICE="${VOLUME_CHOICE:-$default_vol_choice}"
            case "$VOLUME_CHOICE" in
                1) VOLUME_TYPE="bluesound"; break ;;
                2) VOLUME_TYPE="beolab5"; break ;;
                3) VOLUME_TYPE="powerlink"; break ;;
                4) VOLUME_TYPE="c4amp"; break ;;
                5) VOLUME_TYPE="hdmi"; break ;;
                6) VOLUME_TYPE="spdif"; break ;;
                7) VOLUME_TYPE="rca"; break ;;
                *) echo "Invalid selection. Please enter 1-7." ;;
            esac
        done
    else
        echo "  1) PowerLink   - B&O speakers via MasterLink mixer (Recommended)"
        echo "  2) BeoLab 5    - BeoLab 5 via controller REST API"
        echo "  3) C4 Amp      - Control4 amplifier via UDP"
        echo "  4) HDMI        - HDMI audio output (ALSA software volume)"
        echo "  5) S/PDIF      - S/PDIF HAT output (ALSA software volume)"
        echo "  6) RCA         - RCA analog output (no volume control)"
        echo ""

        case "$current_vol_type" in
            powerlink) default_vol_choice="1" ;;
            beolab5)   default_vol_choice="2" ;;
            c4amp)     default_vol_choice="3" ;;
            hdmi)      default_vol_choice="4" ;;
            spdif)     default_vol_choice="5" ;;
            rca)       default_vol_choice="6" ;;
        esac

        while true; do
            read -p "Select volume control [1-6, default $default_vol_choice]: " VOLUME_CHOICE
            VOLUME_CHOICE="${VOLUME_CHOICE:-$default_vol_choice}"
            case "$VOLUME_CHOICE" in
                1) VOLUME_TYPE="powerlink"; break ;;
                2) VOLUME_TYPE="beolab5"; break ;;
                3) VOLUME_TYPE="c4amp"; break ;;
                4) VOLUME_TYPE="hdmi"; break ;;
                5) VOLUME_TYPE="spdif"; break ;;
                6) VOLUME_TYPE="rca"; break ;;
                *) echo "Invalid selection. Please enter 1-6." ;;
            esac
        done
    fi

    # Per-type configuration
    case "$VOLUME_TYPE" in
        beolab5)
            local DEFAULT_VOLUME_HOST="${current_vol_host:-beolab5-controller.local}"
            read -p "BeoLab 5 controller hostname [$DEFAULT_VOLUME_HOST]: " VOLUME_HOST
            VOLUME_HOST="${VOLUME_HOST:-$DEFAULT_VOLUME_HOST}"
            ;;
        sonos|bluesound)
            VOLUME_HOST="$player_ip"
            log_info "Using player IP ($player_ip) for volume control"
            ;;
        powerlink)
            local DEFAULT_VOLUME_HOST="${current_vol_host:-localhost}"
            read -p "MasterLink mixer host [$DEFAULT_VOLUME_HOST]: " VOLUME_HOST
            VOLUME_HOST="${VOLUME_HOST:-$DEFAULT_VOLUME_HOST}"
            local current_mixer_port
            current_mixer_port=$(cfg_read '.volume.mixer_port // 8768')
            read -p "Mixer HTTP port [${current_mixer_port:-8768}]: " VOLUME_MIXER_PORT
            VOLUME_MIXER_PORT="${VOLUME_MIXER_PORT:-${current_mixer_port:-8768}}"
            ;;
        c4amp)
            local DEFAULT_C4_HOST="${current_vol_host:-192.168.1.100}"
            read -p "C4 amplifier IP address [$DEFAULT_C4_HOST]: " VOLUME_HOST
            VOLUME_HOST="${VOLUME_HOST:-$DEFAULT_C4_HOST}"
            local current_zone
            current_zone=$(cfg_read '.volume.zone // "01"')
            read -p "Output zone [${current_zone:-01}]: " VOLUME_ZONE
            VOLUME_ZONE="${VOLUME_ZONE:-${current_zone:-01}}"
            local current_input
            current_input=$(cfg_read '.volume.input // "01"')
            read -p "Source input [${current_input:-01}]: " VOLUME_INPUT
            VOLUME_INPUT="${VOLUME_INPUT:-${current_input:-01}}"
            ;;
        hdmi|spdif|rca)
            VOLUME_HOST=""
            ;;
    esac

    read -p "Maximum volume percentage [$VOLUME_MAX]: " input_max
    VOLUME_MAX="${input_max:-$VOLUME_MAX}"

    # Build volume config safely using --arg for all user strings
    local tmp
    tmp=$(mktemp)
    if jq --arg vtype "$VOLUME_TYPE" --arg vhost "$VOLUME_HOST" \
        --argjson vmax "$VOLUME_MAX" --arg oname "$OUTPUT_NAME" \
        --arg vzone "$VOLUME_ZONE" --arg vinput "$VOLUME_INPUT" \
        --arg vmixport "$VOLUME_MIXER_PORT" \
        '
        .volume.type = $vtype | .volume.host = $vhost |
        .volume.max = $vmax | .volume.output_name = $oname |
        if $vzone != "" then .volume.zone = $vzone else del(.volume.zone) end |
        if $vinput != "" then .volume.input = $vinput else del(.volume.input) end |
        if $vmixport != "" then .volume.mixer_port = ($vmixport | tonumber) else del(.volume.mixer_port) end
        ' "$CONFIG_FILE" > "$tmp"; then
        mv "$tmp" "$CONFIG_FILE"; chmod 644 "$CONFIG_FILE"
    else
        rm -f "$tmp"; log_error "Failed to update config.json"
    fi

    log_success "Output: $OUTPUT_NAME, volume: $VOLUME_TYPE @ ${VOLUME_HOST:-local} (max $VOLUME_MAX%%)"
}
