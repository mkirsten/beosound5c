#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Spotify setup
# =============================================================================

configure_spotify() {
    echo ""
    log_section "Spotify Integration (Optional)"
    echo ""
    echo "Spotify integration allows you to browse and play your Spotify playlists"
    echo "through the BeoSound 5c interface. This requires a Spotify account and"
    echo "creating a free Spotify Developer app."
    echo ""
    read -p "Do you want to set up Spotify playlist access? (y/N): " SETUP_SPOTIFY

    if [[ "$SETUP_SPOTIFY" =~ ^[Yy]$ ]]; then
        echo ""
        log_info "Starting Spotify OAuth Setup Wizard..."
        echo ""
        echo -e "${YELLOW}This will start a temporary web server on this device.${NC}"
        echo ""
        echo "You'll need:"
        echo "  1. A phone/tablet/computer on the same network"
        echo "  2. A Spotify account"
        echo "  3. To create a free app at developer.spotify.com"
        echo ""
        echo -e "${YELLOW}IMPORTANT: The SSL certificate is self-signed.${NC}"
        echo "When you open the URL, your browser will show a security warning."
        echo "You must tap 'Advanced' → 'Proceed anyway' to continue."
        echo ""
        read -p "Press Enter to start the setup wizard (or 's' to skip): " START_SPOTIFY_SETUP

        if [[ ! "$START_SPOTIFY_SETUP" =~ ^[Ss]$ ]]; then
            echo ""
            log_info "Spotify setup is done via the web interface."
            log_info "After installation, the beo-source-spotify service will start and serve"
            log_info "a setup page at http://<device-ip>:8771/setup"
            echo ""
            # Check if tokens already exist from a previous setup
            local SPOTIFY_TOKENS="/etc/beosound5c/spotify_tokens.json"
            if [ -f "$SPOTIFY_TOKENS" ]; then
                local client_id refresh_token
                client_id=$(jq -r '.client_id // empty' "$SPOTIFY_TOKENS" 2>/dev/null)
                refresh_token=$(jq -r '.refresh_token // empty' "$SPOTIFY_TOKENS" 2>/dev/null)
                if [ -n "$refresh_token" ]; then
                    log_success "Spotify tokens found from previous setup!"
                    if [ -n "$client_id" ]; then
                        cfg_set_str '.spotify.client_id' "$client_id"
                    fi
                fi
            fi
            if [ -z "$refresh_token" ]; then
                log_info "Open http://<device-ip>:8771/setup after starting services"
            fi
        else
            log_info "Skipping Spotify setup"
        fi
    else
        log_info "Skipping Spotify integration"
        log_info "You can set it up later at http://<device-ip>:8771/setup"
    fi
}
