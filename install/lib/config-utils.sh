#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Config read/merge helpers (jq-based)
# =============================================================================
# Sourced by install.sh. Uses globals from common.sh (CONFIG_DIR, CONFIG_FILE).

# Read a value from config.json.  Returns empty string if missing/no file.
# Usage: val=$(cfg_read '.player.type')
cfg_read() {
    jq -r "$1 // empty" "$CONFIG_FILE" 2>/dev/null
}

# Merge a jq expression into config.json.
# Usage: cfg_set '.player.type = "sonos" | .player.ip = "1.2.3.4"'
# The temp file MUST live next to the target: mktemp's default is /tmp,
# which is tmpfs, and a cross-filesystem mv is copy-then-unlink — a crash
# mid-copy truncates config.json (services then silently fall back to
# default.json).  Same-fs mv is an atomic rename.
cfg_set() {
    local tmp
    tmp=$(mktemp "${CONFIG_FILE}.XXXXXX")
    if jq "$1" "$CONFIG_FILE" > "$tmp"; then
        chmod 644 "$tmp"
        mv "$tmp" "$CONFIG_FILE"
    else
        rm -f "$tmp"
        log_error "Failed to update config.json"
        return 1
    fi
}

# Set a single config key to a string value (safe against special characters).
# Usage: cfg_set_str '.device' "$DEVICE_NAME"
cfg_set_str() {
    local path="$1"
    local value="$2"
    local tmp
    tmp=$(mktemp "${CONFIG_FILE}.XXXXXX")
    if jq --arg v "$value" "$path = \$v" "$CONFIG_FILE" > "$tmp"; then
        chmod 644 "$tmp"
        mv "$tmp" "$CONFIG_FILE"
    else
        rm -f "$tmp"
        log_error "Failed to update config.json"
        return 1
    fi
}

# Ensure config.json exists (copy default.json if missing, create dir)
cfg_ensure() {
    mkdir -p "$CONFIG_DIR"
    if [ ! -f "$CONFIG_FILE" ]; then
        local default_cfg="$INSTALL_DIR/config/default.json"
        if [ -f "$default_cfg" ]; then
            cp "$default_cfg" "$CONFIG_FILE"
        else
            # Minimal skeleton
            cat > "$CONFIG_FILE" << 'SKEL'
{
  "device": "",
  "menu": { "PLAYING": "playing", "SYSTEM": "system" },
  "scenes": [],
  "player": { "type": "", "ip": "" },
  "bluetooth": { "remote_mac": "" },
  "home_assistant": { "url": "", "webhook_url": "" },
  "transport": { "mode": "webhook" },
  "volume": { "type": "", "host": "", "max": 70, "step": 3, "output_name": "" },
  "cd": { "device": "/dev/sr0" },
  "spotify": { "client_id": "" }
}
SKEL
        fi
        chmod 644 "$CONFIG_FILE"
    fi
}

# Ensure radio_favourites.json exists (seed default SR P1-P4 + Mix Megapol +
# SomaFM Groove Salad/Mission Control + Radio Paradise + Linn so the BeoRemote
# radio menu has working defaults). The file must be writable by INSTALL_USER
# — beo-source-radio runs as that user and rewrites it on toggle_favourite,
# the new POST /favourites/short_name endpoint, and via the Config UI editor.
# Without the chown, _save_favourites silently fails with EACCES and aliases
# revert on service restart. Same applies to radio_last_station.json which
# is written each time playback starts.
radio_favs_ensure() {
    local favs_file="$CONFIG_DIR/radio_favourites.json"
    local last_station_file="$CONFIG_DIR/radio_last_station.json"
    mkdir -p "$CONFIG_DIR"
    # /etc/beosound5c itself must be writable by INSTALL_USER — _save_favourites
    # uses an atomic ``write .tmp + os.replace`` pattern that needs to create
    # a sibling .tmp file, and Python's os.replace can't cross filesystems.
    # Without this chown, the radio service silently fails to persist any
    # favourite/alias change, so on-device edits and short_name auto-suggest
    # both revert on every service restart.
    chown "$INSTALL_USER":"$INSTALL_USER" "$CONFIG_DIR"
    if [ ! -f "$favs_file" ]; then
        local default_favs="$INSTALL_DIR/config/radio_favourites.default.json"
        if [ -f "$default_favs" ]; then
            cp "$default_favs" "$favs_file"
        fi
    fi
    if [ -f "$favs_file" ]; then
        chown "$INSTALL_USER":"$INSTALL_USER" "$favs_file"
        chmod 644 "$favs_file"
    fi
    # Pre-create radio_last_station.json owned by INSTALL_USER so the first
    # save attempt doesn't fail with permission-denied on the .tmp file.
    if [ ! -f "$last_station_file" ]; then
        : > "$last_station_file"
    fi
    chown "$INSTALL_USER":"$INSTALL_USER" "$last_station_file"
    chmod 644 "$last_station_file"
}

# Ensure secrets.env exists (create empty template if missing)
secrets_ensure() {
    mkdir -p "$CONFIG_DIR"
    if [ ! -f "$SECRETS_FILE" ]; then
        cat > "$SECRETS_FILE" << 'SKEL'
# BeoSound 5c Secrets

# Home Assistant Long-Lived Access Token
HA_TOKEN=""

# MQTT credentials (only needed if transport.mode includes "mqtt")
MQTT_USER=""
MQTT_PASSWORD=""

# Tailscale auth key for remote support (optional)
# Generate at: https://login.tailscale.com/admin/settings/keys
TAILSCALE_AUTH_KEY=""
SKEL
        chmod 600 "$SECRETS_FILE"
    fi
}

# Read a value from secrets.env
# Usage: val=$(secret_read "HA_TOKEN")
secret_read() {
    local key="$1"
    grep "^${key}=" "$SECRETS_FILE" 2>/dev/null | head -1 | sed 's/^[^=]*="//; s/"$//'
}

# Set a value in secrets.env (creates key if missing, updates if exists)
# Uses grep+append instead of sed to avoid delimiter collisions with token values.
# Usage: secret_set "HA_TOKEN" "abc123"
secret_set() {
    local key="$1"
    local val="$2"
    secrets_ensure
    local tmp
    # Same-fs temp so the mv is an atomic rename (see cfg_set).
    tmp=$(mktemp "${SECRETS_FILE}.XXXXXX")
    grep -v "^${key}=" "$SECRETS_FILE" > "$tmp" 2>/dev/null || true
    echo "${key}=\"${val}\"" >> "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$SECRETS_FILE"
}
