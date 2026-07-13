#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Anonymous startup ping (the world map)
# =============================================================================

configure_telemetry() {
    local optout_file="$INSTALL_DIR/NO_TELEMETRY"

    # Non-interactive run (piped/scripted): don't prompt, just say how to opt out.
    if [ ! -t 0 ]; then
        if [ -f "$optout_file" ]; then
            log_info "Anonymous startup ping: disabled (NO_TELEMETRY present)"
        else
            log_info "Anonymous startup ping: enabled — opt out anytime with: touch $optout_file"
        fi
        return 0
    fi

    echo ""
    log_info "One more thing — the world map of revived BeoSound 5s"
    echo ""
    echo "  On startup, your device can send a tiny anonymous hello to"
    echo "  beosound5c.com. It contains exactly five things: the software"
    echo "  version, the names of your enabled sources (e.g. \"spotify\"),"
    echo "  the player type, the volume type, and an anonymous device ID"
    echo "  (a one-way hash of the Pi's MAC — the MAC itself never leaves"
    echo "  the device). No names, no accounts, no credentials, nothing"
    echo "  about your music. The whole thing is ~100 lines you can read:"
    echo "  services/lib/beacon.py"
    echo ""
    echo "  Why? Honestly: every time a new dot appears somewhere in the"
    echo "  world, it makes the developer's day. That's the entire reason."
    echo ""
    echo "  Saying no is completely fine — everything works exactly the same."
    echo ""

    if [ -f "$optout_file" ]; then
        log_info "Currently: disabled (NO_TELEMETRY file present)"
        read -p "Send the anonymous hello on startup? (y/N): " TELEMETRY_ANSWER
        if [[ "$TELEMETRY_ANSWER" =~ ^[Yy]$ ]]; then
            rm -f "$optout_file"
            log_success "Thank you! Your dot on the map will make someone's day."
        else
            log_success "Staying off — no ping will ever be sent."
        fi
    else
        read -p "Send the anonymous hello on startup? (Y/n): " TELEMETRY_ANSWER
        if [[ "$TELEMETRY_ANSWER" =~ ^[Nn]$ ]]; then
            touch "$optout_file"
            log_success "No problem — NO_TELEMETRY created, no ping will ever be sent."
        else
            log_success "Thank you! Your dot on the map will make someone's day."
        fi
    fi
}
