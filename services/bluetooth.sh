#!/usr/bin/env bash
set -euo pipefail

# Set these up based on device and endpoint for webhooks
MAC="48:D0:CF:BD:CE:35"
WEBHOOK="http://homeassistant.local:8123/api/webhook/beoremote-event"

# Handles for Bluetooth GATT
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

  # Log HCI state after reset
  log ">>> HCI state after reset:"
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

log "=========================================="
log "BeoRemote Bluetooth Service Starting"
log "=========================================="
log "MAC: $MAC"
log "Webhook: $WEBHOOK"
log "PID: $$"
log "=========================================="

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
  while true; do
    echo "connect" >&"$GIN"
    while read -r -u "$GOUT" line; do
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

      elif [[ "$line" == *"Function not implemented"* ]]; then
        consecutive_failures=$((consecutive_failures + 1))
        total_failures=$((total_failures + 1))
        log ">>> HCI function not implemented (failure #$consecutive_failures)—doing full restart"
        kill "$GPID" 2>/dev/null || true
        sleep 2
        break 3  # exit all the way to outer loop → cleanup/HCI reset

      elif [[ "$line" == *"Too many open files"* ]]; then
        consecutive_failures=$((consecutive_failures + 1))
        total_failures=$((total_failures + 1))
        log ">>> Too many open files (failure #$consecutive_failures)—forcing full restart"
        kill "$GPID" 2>/dev/null || true
        sleep 1
        break 3  # exit all loops → cleanup/HCI reset

      elif [[ "$line" =~ ^Error: ]]; then
        consecutive_failures=$((consecutive_failures + 1))
        total_failures=$((total_failures + 1))
        log ">>> Fatal error (failure #$consecutive_failures)—restarting"
        kill "$GPID" 2>/dev/null || true
        sleep 2
        break 3  # exit all loops → cleanup/HCI reset
      fi
    done
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
          # New command - send webhook immediately
          log "[EVENT] Press: $command (new) [total events: $events_received]"
          if curl -G "${WEBHOOK}" \
            --silent --output /dev/null \
            --connect-timeout 1 \
            --max-time 2 \
            --data-urlencode "address=${address}" \
            --data-urlencode "command=${command}"; then
            log "[WEBHOOK] Success: $command sent to ${WEBHOOK}"
          else
            log "[WEBHOOK] Failed: Could not send $command to ${WEBHOOK} (exit code: $?)"
          fi
          last_command="$command"
          repeat_count=1
          pressed=true
        else
          # Same command - increment counter
          ((repeat_count++))

          # Send webhook on first press and after 3rd repeat, as debouncing logic
          if [[ $repeat_count -gt 3 ]]; then
            log "[EVENT] Press: $command (repeat $repeat_count)"
            if curl -G "${WEBHOOK}" \
              --silent --output /dev/null \
              --connect-timeout 1 \
              --max-time 2 \
              --data-urlencode "address=${address}" \
              --data-urlencode "command=${command}"; then
              log "[WEBHOOK] Success: $command (repeat) sent to ${WEBHOOK}"
            else
              log "[WEBHOOK] Failed: Could not send $command (repeat) to ${WEBHOOK} (exit code: $?)"
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
        # Timeout occurred, check if process is still alive and continue
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
