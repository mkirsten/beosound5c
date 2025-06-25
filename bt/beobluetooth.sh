#!/usr/bin/env bash
set -euo pipefail

# Set these up based on device and endpoint for webhooks
MAC="48:D0:CF:BD:CE:35"
WEBHOOK="http://homeassistant.local:8123/api/webhook/beoremote-event"

# Handles for Bluetooth GATT
DESC1="0x0025"
DESC2="0x0026"

# The idea is basically to
# 1) Kill old gatttool CLI tools running and reset bt controller
# 2) Start gatttool CLI and try to connect to the B&O BT remote
# 3a) If connection works; listen for any button events and send then raw to a (HA) webhook, even repeats
# 3b) If connection fails; go back to step 1 or 2 depending on type of failure
# May seem stupid, but work contrary to all libs that I've tried

while true; do
  echo "=== CLEANUP ==="
  pkill -f "gatttool -b $MAC" 2>/dev/null || true

  # (1) Multiple ways exist to reset the bt controller, and the below appears to be working well
  echo "=== RESET HCI ==="
  sudo btmgmt power off; sleep 0.5
  sudo btmgmt power on; sleep 0.5

  # (2) Start gatttool
  echo "=== SPAWN gatttool ==="
  # stderr → stdout so we can catch everything
  coproc GTOOL { gatttool -b "$MAC" -I 2>&1; }
  GIN="${GTOOL[1]}"
  GOUT="${GTOOL[0]}"
  GPID=$!

  # (2) ...and try to conncet
  echo "=== CONNECTING ==="
  # Keep issuing "connect" until success (3a), or until we force a restart in various ways (3b)
  while true; do
    echo "connect" >&"$GIN"
    while read -r -u "$GOUT" line; do
      [[ -n "$line" ]] && echo "[gatttool] $line"

      if [[ "$line" == *"Connection successful"* ]]; then
        echo ">>> Connected!"
        break 2   # exit both read-loop and connecting-loop; go on to LISTENING

      elif [[ "$line" == *"Connection refused"* ]]; then
        echo ">>> Connection refused—restarting bluetooth.service & retrying in 1s"
        sudo systemctl restart bluetooth
        sleep 1
        kill "$GPID" 2>/dev/null || true
        sleep 1
        break 2   # exit read-loop and connecting-loop → back to outer cleanup/HCI reset

      elif [[ "$line" == *"Connection timed out"* ]]; then
        echo ">>> Timed out—retry in 1s"
        sleep 1
        break    # exit read-loop only; retry connect in the inner connecting-loop

      elif [[ "$line" == *"Function not implemented"* ]]; then
        echo ">>> HCI function not implemented—doing full restart"
        kill "$GPID" 2>/dev/null || true
        sleep 2
        break 3  # exit all the way to outer loop → cleanup/HCI reset

      elif [[ "$line" == *"Too many open files"* ]]; then
        echo ">>> Too many open files—forcing full restart"
        kill "$GPID" 2>/dev/null || true
        sleep 1
        break 3  # exit all loops → cleanup/HCI reset

      elif [[ "$line" =~ ^Error: ]]; then
        echo ">>> Fatal error—restarting"
        kill "$GPID" 2>/dev/null || true
        sleep 2
        break 3  # exit all loops → cleanup/HCI reset
      fi
    done
  done

  # (3a) Awesome, let's listen for button events from the remote
  echo "=== LISTENING ==="
  pressed=false
  last_command=""
  repeat_count=0

  # Listen loop: breaks back to outer while on EOF or FD error
  while read -r -u "$GOUT" line; do
    echo "[gatttool] $line"

    # Catch GLib warnings and restart
    if [[ "$line" == *"GLib-WARNING"* ]]; then
      echo ">>> Caught GLib warning—restarting"
      break
    fi

    # Catch invalid FD
    if [[ "$line" == *"Invalid file descriptor"* ]]; then
      echo ">>> Invalid FD—restarting"
      break
    fi

    # Parse notifications
    if [[ "$line" =~ Notification[[:space:]]handle[[:space:]]\=[[:space:]]([^[:space:]]+)[[:space:]]value:[[:space:]]([0-9A-Fa-f]{2})[[:space:]]([0-9A-Fa-f]{2}) ]]; then
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
        # New command - send webhook immediately
        echo "[EVENT] Press: $command (new)"
        curl -G "${WEBHOOK}" \
          --silent --output /dev/null \
          --data-urlencode "address=${address}" \
          --data-urlencode "command=${command}"
        last_command="$command"
        repeat_count=1
        pressed=true
      else
        # Same command - increment counter
        ((repeat_count++))
        
        # Send webhook on first press and after 3rd repeat, as debouncing logic
        if [[ $repeat_count -gt 3 ]]; then
          echo "[EVENT] Press: $command (repeat $repeat_count)"
          curl -G "${WEBHOOK}" \
            --silent --output /dev/null \
            --data-urlencode "address=${address}" \
            --data-urlencode "command=${command}"
        else
          echo "[EVENT] Press: $command (ignored repeat $repeat_count)"
        fi
      fi
    fi
  done

  echo "=== RESTARTING IN 5s ==="
  kill "$GPID" 2>/dev/null || true
  sleep 5
done