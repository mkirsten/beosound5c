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
BS5C_BASE_PATH="${BS5C_BASE_PATH:-/home/kirsten/beosound5c}"

# Home Assistant webhook (use environment variable)
WEBHOOK="${HA_WEBHOOK_URL:-http://homeassistant.local:8123/api/webhook/beosound5c}"

# Handles for Bluetooth GATT (hardware-specific, don't change)
DESC1="0x0025"
DESC2="0x0026"

# Recovery configuration
MAX_CONSECUTIVE_FAILURES=30      # After this many failures, do a cooling-off period
COOLING_OFF_PERIOD=600           # 10 minutes cooling off
MAX_FAILURES_BEFORE_EXIT=50      # After this many, exit and let systemd restart us
RESET_LEVEL_THRESHOLD=5          # Escalate reset level after this many failures at current level

# Logging helper with timestamp
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log_stats() {
  log "[STATS] Consecutive failures: $consecutive_failures, Total failures: $total_failures, Reset level: $reset_level, Successful connections: $successful_connections"
}

# Systemd watchdog helper - notify systemd we're alive
watchdog_ping() {
  if [[ -n "${WATCHDOG_USEC:-}" ]]; then
    systemd-notify WATCHDOG=1
  fi
}

# Notify systemd we're ready and start watchdog
watchdog_ready() {
  if [[ -n "${NOTIFY_SOCKET:-}" ]]; then
    systemd-notify --ready --status="Starting up..."
    log ">>> Systemd watchdog enabled (interval: ${WATCHDOG_USEC:-0}us)"
  fi
}

# Escalating reset function
# Level 1: btmgmt power cycle (fast, usually works)
# Level 2: hciconfig down/up (more thorough)
# Level 3: Full bluetooth service restart + hciconfig
# Level 4: Kernel module reload (nuclear option)
do_reset() {
  local level=$1
  log "=== RESET HCI (Level $level) ==="

  case $level in
    1)
      log ">>> Level 1: btmgmt power cycle"
      sudo btmgmt power off 2>&1 | while read -r line; do log "[btmgmt] $line"; done
      sleep 0.5
      sudo btmgmt power on 2>&1 | while read -r line; do log "[btmgmt] $line"; done
      sleep 0.5
      ;;
    2)
      log ">>> Level 2: hciconfig down/up"
      sudo hciconfig hci0 down 2>&1 | while read -r line; do log "[hciconfig] $line"; done
      sleep 1
      sudo hciconfig hci0 up 2>&1 | while read -r line; do log "[hciconfig] $line"; done
      sleep 1
      ;;
    3)
      log ">>> Level 3: Full bluetooth service restart"
      sudo systemctl stop bluetooth 2>&1 | while read -r line; do log "[systemctl] $line"; done
      sleep 2
      sudo hciconfig hci0 down 2>&1 || true
      sleep 1
      sudo systemctl start bluetooth 2>&1 | while read -r line; do log "[systemctl] $line"; done
      sleep 3
      ;;
    4)
      log ">>> Level 4: Kernel module reload (nuclear option)"
      sudo systemctl stop bluetooth 2>&1 || true
      sleep 1
      # Try to unload and reload the HCI UART module
      if lsmod | grep -q hci_uart; then
        log ">>> Unloading hci_uart module..."
        sudo modprobe -r hci_uart 2>&1 | while read -r line; do log "[modprobe] $line"; done || log ">>> Warning: Could not unload hci_uart"
        sleep 2
        log ">>> Reloading hci_uart module..."
        sudo modprobe hci_uart 2>&1 | while read -r line; do log "[modprobe] $line"; done || log ">>> Warning: Could not reload hci_uart"
        sleep 3
      fi
      sudo systemctl start bluetooth 2>&1 | while read -r line; do log "[systemctl] $line"; done
      sleep 3
      ;;
  esac

  # Verify HCI is UP after reset, retry if needed
  log ">>> Verifying HCI state after reset..."
  local hci_up=false
  for attempt in 1 2 3; do
    if hciconfig hci0 2>&1 | grep -q "UP RUNNING"; then
      hci_up=true
      log ">>> HCI is UP RUNNING (attempt $attempt)"
      break
    else
      log ">>> HCI not UP (attempt $attempt), trying to bring it up..."
      sudo hciconfig hci0 up 2>&1 || true
      sleep 2
    fi
  done

  if [[ "$hci_up" != "true" ]]; then
    log "!!! WARNING: HCI failed to come UP after reset - will retry on next cycle"
  fi

  # Log final HCI state
  log ">>> Final HCI state:"
  hciconfig hci0 2>&1 | head -5 | while read -r line; do log "[hciconfig] $line"; done || log ">>> Warning: Could not get HCI state"
}

# Calculate backoff time based on failure count
get_backoff_time() {
  local failures=$1
  if [[ $failures -lt 3 ]]; then
    echo 2
  elif [[ $failures -lt 10 ]]; then
    echo 5
  elif [[ $failures -lt 20 ]]; then
    echo 15
  elif [[ $failures -lt 30 ]]; then
    echo 30
  else
    echo 60
  fi
}

# State tracking
consecutive_failures=0
total_failures=0
successful_connections=0
reset_level=1
last_successful_connection=""

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

# Send webhook with device_type (same JSON format as IR remote)
# Always returns 0 to prevent script exit with set -e
send_webhook() {
  local action="$1"
  local device_type="$2"
  local extra_fields="${3:-}"  # Optional extra JSON fields (without leading comma)

  local json="{\"device_name\":\"${DEVICE_NAME}\",\"source\":\"bluetooth\",\"action\":\"${action}\",\"device_type\":\"${device_type}\"${extra_fields:+,$extra_fields}}"

  if curl -X POST "${WEBHOOK}" \
    --silent --output /dev/null \
    --connect-timeout 1 \
    --max-time 2 \
    -H "Content-Type: application/json" \
    -d "$json"; then
    log "[WEBHOOK] Success: action=$action device_type=$device_type"
  else
    log "[WEBHOOK] Failed: action=$action device_type=$device_type (curl exit: $?)"
  fi
  return 0  # Always succeed to prevent script crash
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

# Signal systemd we're ready (for Type=notify, optional for Type=simple)
watchdog_ready

# The idea is basically to
# 1) Kill old gatttool CLI tools running and reset bt controller
# 2) Start gatttool CLI and try to connect to the B&O BT remote
# 3a) If connection works; listen for any button events and send then raw to a (HA) webhook, even repeats
# 3b) If connection fails; go back to step 1 or 2 depending on type of failure
# May seem stupid, but work contrary to all libs that I've tried

while true; do
  log "=== CLEANUP ==="
  log ">>> Starting new connection attempt cycle"
  log_stats
  watchdog_ping  # Keep systemd watchdog alive

  pkill -f "gatttool -b $MAC" 2>/dev/null || true

  # Check if we've hit the cooling-off threshold
  if [[ $consecutive_failures -ge $MAX_CONSECUTIVE_FAILURES ]] && [[ $consecutive_failures -lt $MAX_FAILURES_BEFORE_EXIT ]]; then
    log "!!! Too many consecutive failures ($consecutive_failures), entering cooling-off period (${COOLING_OFF_PERIOD}s)"
    log "!!! This allows the Bluetooth stack time to recover"
    sleep $COOLING_OFF_PERIOD
    # After cooling off, reset to level 1 and try fresh
    reset_level=1
    log ">>> Cooling-off complete, resuming connection attempts"
  fi

  # Check if we should give up and let systemd restart us
  if [[ $consecutive_failures -ge $MAX_FAILURES_BEFORE_EXIT ]]; then
    log "!!! FATAL: $consecutive_failures consecutive failures, exiting to allow systemd restart"
    log "!!! This may indicate a hardware issue or the remote being out of range/powered off"
    log_stats
    exit 1
  fi

  # Escalate reset level based on failure count
  if [[ $consecutive_failures -gt 0 ]]; then
    new_level=$(( (consecutive_failures / RESET_LEVEL_THRESHOLD) + 1 ))
    if [[ $new_level -gt 4 ]]; then
      new_level=4
    fi
    if [[ $new_level -ne $reset_level ]]; then
      log ">>> Escalating reset level from $reset_level to $new_level"
      reset_level=$new_level
    fi
  fi

  # (1) Reset the bluetooth controller
  do_reset $reset_level

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
  connection_success=false

  # Keep issuing "connect" until success (3a), or until we force a restart in various ways (3b)
  # Limit total connection time to prevent infinite loops
  connection_start=$SECONDS
  max_connection_time=120  # 2 minutes max per connection attempt cycle

  while true; do
    # Check if we've exceeded max connection time
    if (( SECONDS - connection_start > max_connection_time )); then
      log ">>> Connection attempt exceeded ${max_connection_time}s—restarting cycle"
      consecutive_failures=$((consecutive_failures + 1))
      total_failures=$((total_failures + 1))
      kill "$GPID" 2>/dev/null || true
      break
    fi

    watchdog_ping  # Keep watchdog alive during connection attempts

    echo "connect" >&"$GIN" 2>/dev/null || {
      log ">>> Failed to send connect command—gatttool may have died"
      break
    }

    # Read with timeout to prevent hanging forever
    while read -r -u "$GOUT" -t 45 line; do
      [[ -n "$line" ]] && log "[gatttool] $line"

      if [[ "$line" == *"Connection successful"* ]]; then
        log ">>> Connected successfully!"
        last_successful_connection=$(date '+%Y-%m-%d %H:%M:%S')
        successful_connections=$((successful_connections + 1))
        consecutive_failures=0
        reset_level=1
        connection_success=true
        log_stats
        break 2   # exit both read-loop and connecting-loop; go on to LISTENING

      elif [[ "$line" == *"Connection refused"* ]]; then
        consecutive_failures=$((consecutive_failures + 1))
        total_failures=$((total_failures + 1))
        backoff=$(get_backoff_time $consecutive_failures)
        log ">>> Connection refused (failure #$consecutive_failures)—waiting ${backoff}s before retry"
        kill "$GPID" 2>/dev/null || true
        sleep $backoff
        break 2   # exit read-loop and connecting-loop → back to outer cleanup/HCI reset

      elif [[ "$line" == *"Connection timed out"* ]]; then
        log ">>> Timed out—retry in 2s"
        sleep 2
        break    # exit read-loop only; retry connect in the inner connecting-loop

      elif [[ "$line" == *"Function not implemented"* ]] || [[ "$line" == *"Too many open files"* ]] || [[ "$line" =~ ^Error: ]]; then
        consecutive_failures=$((consecutive_failures + 1))
        total_failures=$((total_failures + 1))
        log ">>> Fatal error (failure #$consecutive_failures): $line"
        kill "$GPID" 2>/dev/null || true
        sleep 2
        break 2  # exit to outer cleanup/HCI reset
      fi
    done

    # If read timed out (exit code > 128), check if process died
    if [[ $? -gt 128 ]]; then
      if ! kill -0 "$GPID" 2>/dev/null; then
        log ">>> gatttool died during connection attempt"
        break
      fi
      log ">>> Connection read timed out, retrying..."
    fi
  done

  # Check if we should be listening or restarting
  if ! kill -0 "$GPID" 2>/dev/null; then
    log ">>> gatttool process not running, restarting outer loop"
    continue
  fi

  # (3a) Awesome, let's listen for button events from the remote
  log "=== LISTENING ==="
  log ">>> Waiting for button events from BeoRemote One..."
  pressed=false
  last_command=""
  repeat_count=0
  events_received=0

  # Listen loop: breaks back to outer while on EOF or FD error
  while true; do
    # Check if gatttool process is still running
    if ! kill -0 "$GPID" 2>/dev/null; then
      log ">>> gatttool process died—restarting"
      consecutive_failures=$((consecutive_failures + 1))
      total_failures=$((total_failures + 1))
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
        consecutive_failures=$((consecutive_failures + 1))
        total_failures=$((total_failures + 1))
        break
      fi

      # Catch invalid FD
      if [[ "$line" == *"Invalid file descriptor"* ]]; then
        log ">>> Invalid FD—connection likely lost"
        consecutive_failures=$((consecutive_failures + 1))
        total_failures=$((total_failures + 1))
        break
      fi

      # Catch disconnection
      if [[ "$line" == *"Connection lost"* ]] || [[ "$line" == *"Disconnected"* ]]; then
        log ">>> Connection lost—restarting"
        consecutive_failures=$((consecutive_failures + 1))
        total_failures=$((total_failures + 1))
        break
      fi

      # Parse notifications
      if [[ "$line" =~ Notification[[:space:]]handle[[:space:]]\=[[:space:]]([^[:space:]]+)[[:space:]]value:[[:space:]]([0-9A-Fa-f]{2})[[:space:]]([0-9A-Fa-f]{2}) ]]; then
        address="${BASH_REMATCH[1]}"
        command="${BASH_REMATCH[2],,}"
        events_received=$((events_received + 1))

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

          log "[EVENT] Press: $command -> $button_result (mode: $current_mode) [total events: $events_received]"

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

            log "[EVENT] Press: $command (repeat $repeat_count)"

            # Only repeat nav commands, not mode switches
            if [[ "$result_type" == "nav" ]]; then
              if [[ "$current_mode" == "Video" ]]; then
                send_webhook "$result_value" "Video"
              else
                case "$result_value" in
                  "up"|"right") send_webhook "up" "Audio" ;;
                  "down"|"left") send_webhook "down" "Audio" ;;
                  "go") send_webhook "go" "Audio" ;;
                  "stop"|"off") send_webhook "stop" "Audio" ;;
                  *) send_webhook "$result_value" "Audio" ;;
                esac
              fi
            fi
          else
            log "[EVENT] Press: $command (ignored repeat $repeat_count)"
          fi
        fi
      fi
    else
      # read failed or timed out
      read_exit_code=$?
      if [[ $read_exit_code -gt 128 ]]; then
        # Timeout occurred - ping watchdog and check if process is still alive
        watchdog_ping  # Critical: keep systemd watchdog alive
        # Only log every 5 minutes to reduce noise
        if [[ $((SECONDS % 300)) -lt 30 ]]; then
          log ">>> Heartbeat: Connection alive, waiting for events... (events received: $events_received)"
        fi
        if ! kill -0 "$GPID" 2>/dev/null; then
          log ">>> Process died during timeout—restarting"
          consecutive_failures=$((consecutive_failures + 1))
          total_failures=$((total_failures + 1))
          break
        fi
        continue
      else
        # EOF or other error
        log ">>> Read failed with exit code $read_exit_code—restarting"
        consecutive_failures=$((consecutive_failures + 1))
        total_failures=$((total_failures + 1))
        break
      fi
    fi
  done

  backoff=$(get_backoff_time $consecutive_failures)
  log "=== RESTARTING IN ${backoff}s ==="
  log_stats
  kill "$GPID" 2>/dev/null || true
  sleep $backoff
done
