#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Home Assistant configuration
# =============================================================================

configure_homeassistant() {
    echo ""
    log_section "Home Assistant Configuration"

    local current_url current_webhook current_dashboard
    current_url=$(cfg_read '.home_assistant.url')
    current_webhook=$(cfg_read '.home_assistant.webhook_url')
    current_dashboard=$(cfg_read '.home_assistant.security_dashboard // empty')
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

    # --- Security dashboard ---
    echo ""
    log_info "Home Assistant Dashboard for SECURITY Page (Optional)"
    echo ""
    echo "The SECURITY menu item can display a Home Assistant dashboard (e.g., camera feeds)."
    echo "Enter the dashboard path without leading slash."
    echo "Examples: lovelace-cameras/0, dashboard-cameras/home"
    echo ""
    local HA_SECURITY_DASHBOARD
    if [ -n "$current_dashboard" ]; then
        read -p "HA dashboard for SECURITY page [$current_dashboard]: " HA_SECURITY_DASHBOARD
        HA_SECURITY_DASHBOARD="${HA_SECURITY_DASHBOARD:-$current_dashboard}"
    else
        read -p "HA dashboard for SECURITY page (press Enter to skip): " HA_SECURITY_DASHBOARD
    fi
    if [ -n "$HA_SECURITY_DASHBOARD" ]; then
        log_success "Security dashboard: $HA_SECURITY_DASHBOARD"
    else
        log_info "No security dashboard configured - SECURITY page will be empty"
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
    cfg_set ".home_assistant.url = \"$HA_URL\" | .home_assistant.webhook_url = \"$HA_WEBHOOK_URL\""

    # Handle security dashboard in menu
    if [ -n "$HA_SECURITY_DASHBOARD" ]; then
        # Add or update SECURITY menu entry
        cfg_set "if .menu.SECURITY then .menu.SECURITY.dashboard = \"$HA_SECURITY_DASHBOARD\" else .menu.SECURITY = {\"id\": \"security\", \"dashboard\": \"$HA_SECURITY_DASHBOARD\"} end"
    fi

    # Write token to secrets
    secret_set "HA_TOKEN" "$HA_TOKEN"
}
