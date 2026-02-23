#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Configuration wizard orchestrator
# =============================================================================
# Called with optional sub-step argument:
#   run_wizard           — full wizard (all steps)
#   run_wizard player    — reconfigure player only
#   run_wizard ha        — reconfigure HA only
#   etc.

run_wizard() {
    local step="${1:-all}"

    # Ensure config infrastructure exists
    cfg_ensure
    secrets_ensure

    case "$step" in
        all)
            log_section "Configuration"

            # Check if config already exists with real data
            local existing_device
            existing_device=$(cfg_read '.device')
            if [ -n "$existing_device" ] && [ "$existing_device" != "" ]; then
                log_info "Existing configuration found (device: $existing_device)"
                read -p "Do you want to reconfigure from scratch? (y/N): " RECONFIGURE
                if [[ "$RECONFIGURE" =~ ^[Yy]$ ]]; then
                    # Reset to default template
                    rm -f "$CONFIG_FILE"
                    cfg_ensure
                fi
            fi

            log_info "Let's configure your BeoSound 5c installation..."
            echo ""

            configure_device
            configure_player
            configure_homeassistant
            configure_bluetooth
            configure_spotify
            configure_transport
            configure_audio
            configure_menu
            generate_config
            ;;
        device)
            configure_device
            ;;
        player)
            configure_player
            # Update volume host if it tracks the player IP
            local vol_type
            vol_type=$(cfg_read '.volume.type')
            if [[ "$vol_type" == "sonos" || "$vol_type" == "bluesound" ]]; then
                cfg_set ".volume.host = .player.ip"
            fi
            ;;
        ha|homeassistant)
            configure_homeassistant
            ;;
        bluetooth|bt)
            configure_bluetooth
            ;;
        spotify)
            configure_spotify
            ;;
        transport)
            configure_transport
            ;;
        audio)
            configure_audio
            ;;
        menu)
            configure_menu
            ;;
        *)
            log_error "Unknown configure step: $step"
            echo ""
            echo "Available steps: device, player, ha, bluetooth, spotify, transport, audio, menu"
            exit 1
            ;;
    esac

    # Symlink web/json/config.json → /etc/beosound5c/config.json so the UI can load it
    mkdir -p "$INSTALL_DIR/web/json"
    ln -sf "$CONFIG_FILE" "$INSTALL_DIR/web/json/config.json"
    log_success "Config symlinked to web/json/config.json for UI"
}
