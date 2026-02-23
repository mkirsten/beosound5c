#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Home Assistant configuration
# =============================================================================

configure_homeassistant() {
    echo ""
    log_section "Home Assistant Configuration"

    local current_url current_webhook
    current_url=$(cfg_read '.home_assistant.url')
    current_webhook=$(cfg_read '.home_assistant.webhook_url')
    local current_token
    current_token=$(secret_read "HA_TOKEN")

    if [ -n "$current_url" ] && [ "$current_url" != "" ]; then
        log_info "Current HA URL: $current_url"
    fi

    # --- HA URL ---
    local HA_URL=""
    mapfile -t ha_results < <(detect_home_assistant)

    if [ ${#ha_results[@]} -gt 0 ]; then
        if [ ${#ha_results[@]} -eq 1 ]; then
            HA_URL="${ha_results[0]}"
            log_success "Using detected Home Assistant: $HA_URL"
        else
            if selection=$(select_from_list "Select Home Assistant instance:" "${ha_results[@]}"); then
                HA_URL="$selection"
            else
                local default_url="${current_url:-http://homeassistant.local:8123}"
                read -p "Enter Home Assistant URL [$default_url]: " HA_URL
                HA_URL="${HA_URL:-$default_url}"
            fi
        fi
    else
        log_warn "Home Assistant not found automatically"
        local default_url="${current_url:-http://homeassistant.local:8123}"
        read -p "Enter Home Assistant URL (e.g., http://homeassistant.local:8123) [$default_url]: " HA_URL
        HA_URL="${HA_URL:-$default_url}"
    fi
    log_success "Home Assistant URL: $HA_URL"

    # --- Webhook URL ---
    local DEFAULT_WEBHOOK="${HA_URL}/api/webhook/beosound5c"
    local HA_WEBHOOK_URL
    read -p "Home Assistant webhook URL [$DEFAULT_WEBHOOK]: " HA_WEBHOOK_URL
    HA_WEBHOOK_URL="${HA_WEBHOOK_URL:-$DEFAULT_WEBHOOK}"

    # --- Embedded web page (e.g., camera dashboard, climate view) ---
    echo ""
    log_info "Embedded Web Page in Menu (Optional)"
    echo ""
    echo "You can embed a web page in the menu (e.g., camera dashboard, climate view)."
    echo "The page will appear as a menu item with an iframe."
    echo ""
    local WEBPAGE_NAME="" WEBPAGE_URL=""
    local current_webpage_url
    current_webpage_url=$(cfg_read '.menu.SECURITY.url // empty')
    if [ -n "$current_webpage_url" ]; then
        log_info "Current embedded page: SECURITY -> $current_webpage_url"
        read -p "Keep this embedded page? (Y/n): " KEEP_WEBPAGE
        if [[ "$KEEP_WEBPAGE" =~ ^[Nn]$ ]]; then
            cfg_set 'del(.menu.SECURITY)'
            log_info "Embedded page removed"
        else
            log_info "Keeping existing embedded page"
        fi
    else
        read -p "Menu item name (press Enter to skip): " WEBPAGE_NAME
        if [ -n "$WEBPAGE_NAME" ]; then
            local default_webpage_url="${HA_URL}/dashboard-cameras/home?kiosk"
            read -p "Full URL [$default_webpage_url]: " WEBPAGE_URL
            WEBPAGE_URL="${WEBPAGE_URL:-$default_webpage_url}"
            log_success "Webpage: $WEBPAGE_NAME -> $WEBPAGE_URL"
        fi
    fi

    # --- HA Token ---
    echo ""
    log_info "Home Assistant Long-Lived Access Token"
    echo ""
    echo "A token is recommended for features like Apple TV status and camera feeds."
    echo ""
    echo "To create a token:"
    echo "  1. Open Home Assistant in your browser: ${HA_URL}"
    echo "  2. Click your profile icon (bottom-left corner)"
    echo "  3. Scroll down to 'Long-Lived Access Tokens'"
    echo "  4. Click 'Create Token'"
    echo "  5. Name it 'BeoSound 5c' and click 'OK'"
    echo "  6. Copy the token (you won't be able to see it again!)"
    echo ""
    echo "Direct link: ${HA_URL}/profile/security"
    echo ""

    local HA_TOKEN
    if [ -n "$current_token" ]; then
        echo "(A token is already configured. Press Enter to keep it, or paste a new one.)"
        read -p "Home Assistant token: " HA_TOKEN
        HA_TOKEN="${HA_TOKEN:-$current_token}"
    else
        read -p "Paste your Home Assistant token (or press Enter to skip): " HA_TOKEN
    fi

    if [ -z "$HA_TOKEN" ]; then
        log_warn "No token provided - some features will be unavailable"
        log_info "You can add a token later by editing: $SECRETS_FILE"
    else
        log_success "Token configured"
    fi

    # --- Write to config ---
    local tmp
    tmp=$(mktemp)
    if jq --arg url "$HA_URL" --arg wh "$HA_WEBHOOK_URL" \
        '.home_assistant.url = $url | .home_assistant.webhook_url = $wh' "$CONFIG_FILE" > "$tmp"; then
        mv "$tmp" "$CONFIG_FILE"; chmod 644 "$CONFIG_FILE"
    else
        rm -f "$tmp"; log_error "Failed to update config.json"
    fi

    # Handle embedded webpage in menu
    if [ -n "$WEBPAGE_URL" ]; then
        tmp=$(mktemp)
        if jq --arg name "$WEBPAGE_NAME" --arg url "$WEBPAGE_URL" \
            '.menu[$name] = {"url": $url}' "$CONFIG_FILE" > "$tmp"; then
            mv "$tmp" "$CONFIG_FILE"; chmod 644 "$CONFIG_FILE"
        else
            rm -f "$tmp"; log_error "Failed to update config.json"
        fi
    fi

    # Write token to secrets
    secret_set "HA_TOKEN" "$HA_TOKEN"
}
