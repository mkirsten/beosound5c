#!/usr/bin/env bash
set -euo pipefail

# Load configuration from /etc/beosound5c/config.env if it exists
CONFIG_FILE="/etc/beosound5c/config.env"
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
fi

# Use environment variables with fallbacks
MAC="${BEOREMOTE_MAC:-00:00:00:00:00:00}"
DEVICE_NAME="${DEVICE_NAME:-BeoSound5c}"
BT_DEVICE_NAME="${BT_DEVICE_NAME:-BeoSound 5c}"
BS5C_BASE_PATH="${BS5C_BASE_PATH:-/home/pi/beosound5c}"

# Home Assistant webhook (use environment variable)
WEBHOOK="${HA_WEBHOOK_URL:-http://homeassistant.local:8123/api/webhook/beosound5c}"

# Set Bluetooth adapter name
if command -v bluetoothctl &>/dev/null; then
    bluetoothctl system-alias "$BT_DEVICE_NAME" 2>/dev/null || true
fi

# Handles for Bluetooth GATT (hardware-specific, don't change)
DESC1="0x0025"
DESC2="0x0026"

# Logging helper with timestamp
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# Remote mode tracking (like IR remote's device_type)
# Modes: "Video" (TV) or "Audio" (MUSIC)
current_mode="Video"

# Button command to action mapping
# Returns: "action:device_type" or "mode:NewMode" or "ignore"
get_button_action() {
  local cmd="$1"
  case "$cmd" in
    # Source buttons - mode switching
    "13") echo "mode:Video" ;;      # TV button -> switch to Video mode + turn on TV
    "10") echo "mode:Audio" ;;      # MUSIC button -> switch to Audio mode (no action)

    # Navigation buttons - action depends on current mode
    "42") echo "nav:up" ;;     # UP
    "43") echo "nav:down" ;;   # DOWN
    "44") echo "nav:left" ;;   # LEFT
    "45") echo "nav:right" ;;  # RIGHT
    "41") echo "nav:go" ;;     # GO/SELECT
    "24") echo "nav:stop" ;;   # BACK
    "23") echo "nav:exit" ;;   # HOME
    "1e") echo "nav:off" ;;    # OFF/POWER

    # Media transport buttons (mode-aware)
    "b5") echo "nav:up" ;;          # FF/Next
    "b6") echo "nav:down" ;;        # REW/Prev
    "b0") echo "nav:play" ;;        # Play
    "b1") echo "nav:pause" ;;       # Pause

    # Volume (pass through as-is, no mode logic)
    "e9") echo "pass:volup" ;;      # VOL+
    "ea") echo "pass:voldown" ;;    # VOL-
    "e2") echo "pass:mute" ;;       # MUTE

    # Guide button
    "60") echo "pass:guide" ;;  # GUIDE

    # Channel buttons
    "9c") echo "pass:chdown" ;;  # Program/Channel Down
    "9d") echo "pass:chup" ;;    # Program/Channel Up

    # Control/Light buttons (always trigger scenes)
    "12") echo "scene:dinner" ;;      # CONTROL-1 -> Dinner scene
    "14") echo "scene:cozy" ;;        # CONTROL-2 -> Cozy scene
    "0f") echo "scene:church_off" ;;  # CONTROL-3 -> All off
    "11") echo "scene:all_on" ;;      # CONTROL-4 -> All on
    "30") echo "scene:artwork" ;;     # POWER -> Artwork scene

    # Color buttons (keep for lights/scenes)
    "01") echo "pass:red" ;;        # RED
    "02") echo "pass:green" ;;      # GREEN
    "03") echo "pass:yellow" ;;     # YELLOW
    "04") echo "pass:blue" ;;       # BLUE

    # Digit buttons (BeoRemote One: digit N = 0x05 + N)
    "05") echo "digit:0" ;;
    "06") echo "digit:1" ;;
    "07") echo "digit:2" ;;
    "08") echo "digit:3" ;;
    "09") echo "digit:4" ;;
    "0a") echo "digit:5" ;;
    "0b") echo "digit:6" ;;
    "0c") echo "digit:7" ;;
    "0d") echo "digit:8" ;;
    "0e") echo "digit:9" ;;

    # Unknown - pass through raw
    *) echo "raw:$cmd" ;;
  esac
}

# Pulse LED for visual feedback (fire-and-forget, no latency)
pulse_led() {
  curl -s "http://localhost:8767/led?mode=pulse" --max-time 0.5 &>/dev/null &
}

# Send webhook with device_type (same JSON format as IR remote)
# Fire-and-forget: runs in background for minimal latency
send_webhook() {
  local action="$1"
  local device_type="$2"
  local extra_fields="${3:-}"  # Optional extra JSON fields (without leading comma)

  local json="{\"device_name\":\"${DEVICE_NAME}\",\"source\":\"bluetooth\",\"action\":\"${action}\",\"device_type\":\"${device_type}\"${extra_fields:+,$extra_fields}}"

  # Run curl in background for faster response
  (curl -X POST "${WEBHOOK}" \
    --silent --output /dev/null \
    --connect-timeout 1 \
    --max-time 2 \
    -H "Content-Type: application/json" \
    -d "$json" && log "[WEBHOOK] Success: action=$action device_type=$device_type" \
    || log "[WEBHOOK] Failed: action=$action device_type=$device_type") &

  return 0
}

# Get playlist URI by digit - uses shared Python module
# Returns spotify:playlist:ID or empty string if not found
get_playlist_uri() {
  local digit="$1"
  python3 "${BS5C_BASE_PATH}/services/playlist_lookup.py" "$digit" 2>/dev/null
  return 0
}

log "=========================================="
log "BeoRemote Bluetooth Service Starting"
log "=========================================="
log "MAC: $MAC"
log "Webhook: $WEBHOOK"
log "PID: $$"
log "=========================================="

# Log Bluetooth adapter state for diagnostics
log ">>> Checking Bluetooth adapter state..."
btmgmt info 2>&1 | head -5 | while read -r line; do log "[btmgmt] $line"; done

# The idea is basically to
# 1) Kill old gatttool CLI tools running and reset bt controller
# 2) Start gatttool CLI and try to connect to the B&O BT remote
# 3a) If connection works; listen for any button events and send then raw to a (HA) webhook, even repeats
# 3b) If connection fails; go back to step 1 or 2 depending on type of failure
# May seem stupid, but work contrary to all libs that I've tried

while true; do
  log "=== CLEANUP ==="
  log ">>> Starting new connection attempt cycle"
  log ">>> Cleaning up old gatttool processes"
  existing_pids=$(pgrep -f "gatttool -b $MAC" 2>/dev/null || echo "none")
  log ">>> Existing gatttool PIDs: $existing_pids"
  pkill -f "gatttool -b $MAC" 2>/dev/null || true

  # (1) Simple reset - just power cycle the adapter
  log "=== RESET HCI ==="
  sudo btmgmt power off 2>&1 | while read -r line; do log "[btmgmt] $line"; done
  sleep 0.5
  sudo btmgmt power on 2>&1 | while read -r line; do log "[btmgmt] $line"; done
  sleep 0.5

  # (2) Start gatttool
  log "=== SPAWN gatttool ==="
  # stderr → stdout so we can catch everything
  coproc GTOOL { gatttool -b "$MAC" -I 2>&1; }
  GIN="${GTOOL[1]}"
  GOUT="${GTOOL[0]}"
  GPID=$!
  log ">>> gatttool PID: $GPID"

  # (2) ...and try to connect
  log "=== CONNECTING ==="
  # Keep issuing "connect" until success (3a), or until we force a restart in various ways (3b)
  while true; do
    echo "connect" >&"$GIN" 2>/dev/null || {
      log ">>> Failed to send connect command—gatttool may have died"
      break
    }

    while read -r -u "$GOUT" -t 60 line; do
      [[ -n "$line" ]] && log "[gatttool] $line"

      if [[ "$line" == *"Connection successful"* ]]; then
        log ">>> Connected!"
        break 2   # exit both read-loop and connecting-loop; go on to LISTENING

      elif [[ "$line" == *"Connection refused"* ]]; then
        log ">>> Connection refused—restarting bluetooth.service & retrying in 1s"
        sudo systemctl restart bluetooth
        sleep 1
        kill "$GPID" 2>/dev/null || true
        sleep 1
        break 2   # exit read-loop and connecting-loop → back to outer cleanup/HCI reset

      elif [[ "$line" == *"Connection timed out"* ]]; then
        log ">>> Timed out—retry in 1s"
        sleep 1
        break    # exit read-loop only; retry connect in the inner connecting-loop

      elif [[ "$line" == *"Function not implemented"* ]]; then
        log ">>> HCI function not implemented—doing full restart"
        kill "$GPID" 2>/dev/null || true
        sleep 2
        break 2  # exit to outer loop → cleanup/HCI reset

      elif [[ "$line" == *"Too many open files"* ]]; then
        log ">>> Too many open files—forcing full restart"
        kill "$GPID" 2>/dev/null || true
        sleep 1
        break 2  # exit to outer loop → cleanup/HCI reset

      elif [[ "$line" =~ ^Error: ]]; then
        log ">>> Fatal error—restarting"
        kill "$GPID" 2>/dev/null || true
        sleep 2
        break 2  # exit to outer loop → cleanup/HCI reset
      fi
    done

    # If read timed out, check if process died
    if [[ $? -gt 128 ]]; then
      if ! kill -0 "$GPID" 2>/dev/null; then
        log ">>> gatttool died during connection attempt"
        break
      fi
      log ">>> Connection read timed out (60s), retrying..."
    fi
  done

  # Check if we should be listening or restarting
  if ! kill -0 "$GPID" 2>/dev/null; then
    log ">>> gatttool process not running, restarting outer loop"
    continue
  fi

  # Enable notifications on both CCCD handles immediately after connection
  log ">>> Enabling notifications on CCCD handles..."
  echo "char-write-req $DESC1 0100" >&"$GIN" 2>/dev/null || true
  read -r -u "$GOUT" -t 5 cccd_response1 || cccd_response1="(timeout)"
  log ">>> CCCD1 response: $cccd_response1"
  sleep 0.1
  echo "char-write-req $DESC2 0100" >&"$GIN" 2>/dev/null || true
  read -r -u "$GOUT" -t 5 cccd_response2 || cccd_response2="(timeout)"
  log ">>> CCCD2 response: $cccd_response2"
  sleep 0.1

  # (3a) Awesome, let's listen for button events from the remote
  log "=== LISTENING ==="
  log ">>> Waiting for button events from BeoRemote One..."
  pressed=false
  last_command=""
  repeat_count=0
  listen_cycles=0
  notification_count=0

  # Listen loop: breaks back to outer while on EOF or FD error
  while true; do
    # Check if gatttool process is still running
    if ! kill -0 "$GPID" 2>/dev/null; then
      log ">>> gatttool process died—restarting"
      break
    fi

    # Read with timeout to avoid hanging
    if read -r -u "$GOUT" -t 30 line; then
      # Don't log every notification line (too verbose), only non-notification messages
      if [[ "$line" != *"Notification"* ]] || [[ "$line" == *"error"* ]] || [[ "$line" == *"Error"* ]]; then
        log "[gatttool] $line"
      fi

      # Catch GLib warnings and restart
      if [[ "$line" == *"GLib-WARNING"* ]]; then
        log ">>> Caught GLib warning—this often indicates connection loss"
        break
      fi

      # Catch invalid FD
      if [[ "$line" == *"Invalid file descriptor"* ]]; then
        log ">>> Invalid FD—connection likely lost"
        break
      fi

      # Catch disconnection
      if [[ "$line" == *"Connection lost"* ]] || [[ "$line" == *"Disconnected"* ]]; then
        log ">>> Connection lost—restarting"
        break
      fi

      # Parse notifications
      if [[ "$line" =~ Notification[[:space:]]handle[[:space:]]\=[[:space:]]([^[:space:]]+)[[:space:]]value:[[:space:]]([0-9A-Fa-f]{2})[[:space:]]([0-9A-Fa-f]{2}) ]]; then
        notification_count=$((notification_count + 1))
        log ">>> [DIAG] Notification #$notification_count received"
        address="${BASH_REMATCH[1]}"
        command="${BASH_REMATCH[2],,}"

        # Reset state on button release
        if [[ "$command" == "00" ]]; then
          pressed=false
          last_command=""
          repeat_count=0
          continue
        fi

        # Handle new or repeated commands
        if [[ "$command" != "$last_command" ]]; then
          # New command - process based on button mapping
          button_result=$(get_button_action "$command")
          result_type="${button_result%%:*}"
          result_value="${button_result#*:}"

          # Visual feedback: pulse LED on button press
          pulse_led

          log "[EVENT] Press: $command -> $button_result (mode: $current_mode)"

          case "$result_type" in
            "mode")
              # Mode switch button
              current_mode="$result_value"
              log "[MODE] Switched to: $current_mode"
              if [[ "$result_value" == "Video" ]]; then
                # TV button: turn on TV
                send_webhook "tv" "Video"
              fi
              # MUSIC button: just switch mode, no webhook
              ;;
            "nav")
              # Navigation button - behavior depends on current mode
              if [[ "$current_mode" == "Video" ]]; then
                send_webhook "$result_value" "Video"
              else
                # Audio mode: map navigation to media controls
                case "$result_value" in
                  "up"|"right") send_webhook "up" "Audio" ;;     # Next track
                  "down"|"left") send_webhook "down" "Audio" ;;  # Prev track
                  "go") send_webhook "go" "Audio" ;;             # Play/pause
                  "stop"|"off") send_webhook "stop" "Audio" ;;   # Pause
                  *) send_webhook "$result_value" "Audio" ;;
                esac
              fi
              ;;
            "audio")
              # Always audio (FF/REW/Play/Pause buttons)
              case "$result_value" in
                "play") send_webhook "play" "Audio" ;;
                "pause") send_webhook "pause" "Audio" ;;
                *) send_webhook "$result_value" "Audio" ;;
              esac
              ;;
            "pass")
              # Pass through with current mode
              send_webhook "$result_value" "$current_mode"
              ;;
            "scene")
              # Scene triggers - always go to Light mode
              send_webhook "$result_value" "Light"
              ;;
            "digit")
              # Digit buttons - look up playlist and send play_playlist action
              playlist_uri=$(get_playlist_uri "$result_value")
              if [[ -n "$playlist_uri" ]]; then
                log "[PLAYLIST] Digit $result_value -> $playlist_uri"
                send_webhook "play_playlist" "Audio" "\"playlist_uri\":\"${playlist_uri}\""
              else
                log "[PLAYLIST] No playlist found for digit $result_value"
                send_webhook "$result_value" "$current_mode"
              fi
              ;;
            "raw")
              # Unknown button - send for debugging
              log "[UNKNOWN] Raw command: $command address: $address"
              send_webhook "unknown_$command" "$current_mode"
              ;;
          esac

          last_command="$command"
          repeat_count=1
          pressed=true
        else
          # Same command - increment counter
          ((repeat_count++))

          # Send webhook on first press and after 3rd repeat, as debouncing logic
          if [[ $repeat_count -gt 3 ]]; then
            button_result=$(get_button_action "$command")
            result_type="${button_result%%:*}"
            result_value="${button_result#*:}"

            log "[EVENT] Repeat: $command (count $repeat_count)"

            # Handle repeats for volume and channel commands
            if [[ "$result_type" == "pass" ]]; then
              case "$result_value" in
                "volup"|"voldown"|"chup"|"chdown")
                  send_webhook "$result_value" "$current_mode"
                  ;;
              esac
            fi
          fi
        fi
      fi
    else
      # read failed or timed out
      read_exit_code=$?
      if [[ $read_exit_code -gt 128 ]]; then
        # Timeout occurred, check if process is still alive and continue
        if ! kill -0 "$GPID" 2>/dev/null; then
          log ">>> Process died during timeout—restarting"
          break
        fi
        # Still alive, continue listening
        listen_cycles=$((listen_cycles + 1))
        if (( listen_cycles % 10 == 0 )); then
          log ">>> [DIAG] Health: cycles=$listen_cycles notifications=$notification_count process=$(kill -0 $GPID 2>/dev/null && echo alive || echo dead)"
        fi
        continue
      else
        # EOF or other error
        log ">>> Read failed with exit code $read_exit_code—restarting"
        break
      fi
    fi
  done

  log "=== RESTARTING IN 3s ==="
  kill "$GPID" 2>/dev/null || true
  sleep 3
done
