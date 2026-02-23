#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Menu configuration
# =============================================================================
# Lets the user choose which optional menu items to include.
# PLAYING and SYSTEM are always present. SPOTIFY is handled by configure_spotify.

configure_menu() {
    echo ""
    log_section "Menu Configuration"

    echo "Choose which items appear in your BeoSound 5c menu."
    echo "PLAYING and SYSTEM are always included."
    echo ""

    # Optional items the user can toggle (order matches default.json)
    local -a ITEMS=("CD" "USB" "SPOTIFY" "NEWS" "SCENES" "SHOWING")
    local -a DESCS=(
        "CD player (requires USB CD drive)"
        "USB music files (auto-mounted USB drives)"
        "Spotify playlists and playback"
        "News articles from The Guardian"
        "Home Assistant scene control"
        "Apple TV — show what's currently playing"
    )

    # Determine current state from config
    local -a ENABLED=()
    for item in "${ITEMS[@]}"; do
        local val
        val=$(cfg_read ".menu.\"$item\" // empty")
        if [ -n "$val" ]; then
            ENABLED+=("y")
        else
            ENABLED+=("n")
        fi
    done

    # Display toggle menu
    echo "Current menu items (enter numbers to toggle, then press Enter to confirm):"
    echo ""
    for i in "${!ITEMS[@]}"; do
        local marker="  "
        if [[ "${ENABLED[$i]}" == "y" ]]; then
            marker="✓ "
        fi
        echo -e "  $((i+1))) [${marker}] ${ITEMS[$i]}  — ${DESCS[$i]}"
    done
    echo ""
    echo "Enter numbers to toggle (space-separated), or press Enter to keep current:"

    read -p "> " TOGGLES

    if [ -n "$TOGGLES" ]; then
        for num in $TOGGLES; do
            if [[ "$num" =~ ^[0-9]+$ ]] && [ "$num" -ge 1 ] && [ "$num" -le ${#ITEMS[@]} ]; then
                local idx=$((num - 1))
                if [[ "${ENABLED[$idx]}" == "y" ]]; then
                    ENABLED[$idx]="n"
                else
                    ENABLED[$idx]="y"
                fi
            fi
        done
    fi

    # Rebuild the menu section in config.json
    # Start with PLAYING (always present)
    local jq_expr='.menu = { "PLAYING": "playing" }'

    for i in "${!ITEMS[@]}"; do
        if [[ "${ENABLED[$i]}" == "y" ]]; then
            case "${ITEMS[$i]}" in
                CD)
                    jq_expr="$jq_expr | .menu.CD = {\"id\": \"cd\", \"hidden\": true}"
                    ;;
                USB)
                    jq_expr="$jq_expr | .menu.USB = {\"id\": \"usb\", \"paths\": [\"/mnt/usb-music\"]}"
                    ;;
                SPOTIFY)
                    jq_expr="$jq_expr | .menu.SPOTIFY = \"spotify\""
                    ;;
                NEWS)
                    jq_expr="$jq_expr | .menu.NEWS = \"news\""
                    ;;
                SCENES)
                    jq_expr="$jq_expr | .menu.SCENES = \"scenes\""
                    ;;
                SHOWING)
                    jq_expr="$jq_expr | .menu.SHOWING = \"showing\""
                    ;;
            esac
        fi
    done

    # SYSTEM always last
    jq_expr="$jq_expr | .menu.SYSTEM = \"system\""

    # Preserve any existing SECURITY entry (configured in HA step)
    local security_val
    security_val=$(cfg_read '.menu.SECURITY // empty')
    if [ -n "$security_val" ]; then
        # Re-read the full object (could be a string or object with url)
        local security_json
        security_json=$(jq -c '.menu.SECURITY' "$CONFIG_FILE" 2>/dev/null)
        if [ -n "$security_json" ] && [ "$security_json" != "null" ]; then
            jq_expr="$jq_expr | .menu.SECURITY = $security_json"
        fi
    fi

    cfg_set "$jq_expr"

    # If NEWS was enabled, prompt for Guardian API key
    for i in "${!ITEMS[@]}"; do
        if [[ "${ITEMS[$i]}" == "NEWS" && "${ENABLED[$i]}" == "y" ]]; then
            local current_key
            current_key=$(cfg_read '.news.guardian_api_key')
            if [ -z "$current_key" ]; then
                echo ""
                log_info "NEWS uses The Guardian's open API."
                echo ""
                echo "  The built-in 'test' key works but is rate-limited."
                echo "  For reliable use, get a free key at:"
                echo "  https://open-platform.theguardian.com/access/"
                echo ""
                read -p "Guardian API key (or press Enter to use 'test'): " GUARDIAN_KEY
                if [ -n "$GUARDIAN_KEY" ]; then
                    cfg_set_str '.news.guardian_api_key' "$GUARDIAN_KEY"
                    log_success "Guardian API key configured"
                else
                    cfg_set_str '.news.guardian_api_key' "test"
                    log_success "Using built-in 'test' key (rate-limited)"
                fi
            fi
        fi
    done

    # Show result
    echo ""
    local enabled_list="PLAYING"
    for i in "${!ITEMS[@]}"; do
        if [[ "${ENABLED[$i]}" == "y" ]]; then
            enabled_list="$enabled_list, ${ITEMS[$i]}"
        fi
    done
    enabled_list="$enabled_list, SYSTEM"
    log_success "Menu: $enabled_list"
}
