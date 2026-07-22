#!/bin/bash
# Reconcile BeoSound 5c services with /etc/beosound5c/config.json.
#
# Idempotent — safe to re-run any time the config changes:
#   - enables + starts the player + dependencies for player.type
#   - disables + stops the other player implementations
#   - enables + starts optional sources listed in the menu, disables others
#   - try-restarts running beo-* services so they pick up the new config
#
# Called by:
#   - install-services.sh (initial install / re-deploy)
#   - input.py:handle_config_save (config UI save → live switch)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=service-registry.sh
source "$SCRIPT_DIR/service-registry.sh"

CONFIG_FILE="/etc/beosound5c/config.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ $CONFIG_FILE not found — cannot reconcile"
    exit 1
fi

# Individual start/stop failures must NOT abort the reconcile mid-way
# (set -e is active): this script runs live from config-save, and bailing
# halfway would leave the old player stopped and the new one absent.
# Collect failures, keep going, report at the end — but distinguish:
#   REQUIRED_FAILED — the configured player and core-service restarts.
#     These failing means the device won't play; exit 1 so callers
#     (install-services.sh) see a real failure.
#   OPTIONAL_FAILED — menu-driven optional sources. Warn but exit 0 so
#     one broken optional source doesn't abort an install.
REQUIRED_FAILED=()
OPTIONAL_FAILED=()

# --- Determine desired player set ----------------------------------------
PLAYER_TYPE=$(python3 -c "import json;print(json.load(open('$CONFIG_FILE')).get('player',{}).get('type','sonos'))" 2>/dev/null || echo "sonos")
echo "ℹ️  Configured player type: $PLAYER_TYPE"

ALL_PLAYERS=(beo-player-sonos beo-player-bluesound beo-player-heos beo-player-local beo-librespot)

case "$PLAYER_TYPE" in
    local)     WANT_PLAYERS=(beo-librespot beo-player-local) ;;
    sonos)     WANT_PLAYERS=(beo-player-sonos) ;;
    bluesound) WANT_PLAYERS=(beo-player-bluesound) ;;
    heos)      WANT_PLAYERS=(beo-player-heos) ;;
    none)      WANT_PLAYERS=() ;;
    *)
        echo "⚠️  Unknown player type '$PLAYER_TYPE' — defaulting to sonos"
        WANT_PLAYERS=(beo-player-sonos)
        ;;
esac

# --- Disable/stop players not in want set --------------------------------
for svc in "${ALL_PLAYERS[@]}"; do
    keep=0
    for want in "${WANT_PLAYERS[@]}"; do
        [ "$svc" = "$want" ] && keep=1 && break
    done
    if [ "$keep" -eq 0 ]; then
        systemctl disable "$svc.service" 2>/dev/null || true
        systemctl stop    "$svc.service" 2>/dev/null || true
    fi
done

# --- Enable/start wanted players -----------------------------------------
for svc in "${WANT_PLAYERS[@]}"; do
    systemctl enable "$svc.service" 2>/dev/null || true
    systemctl start  "$svc.service" || REQUIRED_FAILED+=("$svc.service")
done

# --- Optional sources (driven by config.json menu) -----------------------
for entry in "${OPTIONAL_SOURCES[@]}"; do
    IFS='|' read -r menu_key service _ _ <<< "$entry"
    if grep -q "\"$menu_key\"" "$CONFIG_FILE"; then
        systemctl enable "$service" 2>/dev/null || true
        systemctl start  "$service" || OPTIONAL_FAILED+=("$service")
    else
        systemctl disable "$service" 2>/dev/null || true
        systemctl stop    "$service" 2>/dev/null || true
    fi
done

# --- Restart running beo-* services so they pick up the new config -------
# beo-ui reconnects automatically — skip it.
# beo-input is restarted LAST because it may host the caller of this script
# (the /config save handler runs in beo-input's process).
ACTIVE=$(systemctl list-units --state=active --no-legend --plain 'beo-*.service' \
         | awk '{print $1}' \
         | grep -Ev '^(beo-ui|beo-input)\.service$' || true)

if [ -n "$ACTIVE" ]; then
    # shellcheck disable=SC2086
    systemctl try-restart $ACTIVE || REQUIRED_FAILED+=("try-restart")
fi

systemctl try-restart beo-input.service || REQUIRED_FAILED+=("beo-input.service")

if [ ${#OPTIONAL_FAILED[@]} -gt 0 ]; then
    echo "⚠️  Optional source(s) failed to start: ${OPTIONAL_FAILED[*]} (check journalctl -u <service>)"
fi

if [ ${#REQUIRED_FAILED[@]} -gt 0 ]; then
    echo "❌ Reconcile FAILED — required service(s) did not start: ${REQUIRED_FAILED[*]} (check journalctl -u <service>)"
    exit 1
fi

echo "✅ Reconcile complete"
