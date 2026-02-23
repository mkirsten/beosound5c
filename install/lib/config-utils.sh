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
cfg_set() {
    local tmp
    tmp=$(mktemp)
    if jq "$1" "$CONFIG_FILE" > "$tmp"; then
        mv "$tmp" "$CONFIG_FILE"
        chmod 644 "$CONFIG_FILE"
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
    tmp=$(mktemp)
    if jq --arg v "$value" "$path = \$v" "$CONFIG_FILE" > "$tmp"; then
        mv "$tmp" "$CONFIG_FILE"
        chmod 644 "$CONFIG_FILE"
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
    tmp=$(mktemp)
    grep -v "^${key}=" "$SECRETS_FILE" > "$tmp" 2>/dev/null || true
    echo "${key}=\"${val}\"" >> "$tmp"
    mv "$tmp" "$SECRETS_FILE"
    chmod 600 "$SECRETS_FILE"
}
