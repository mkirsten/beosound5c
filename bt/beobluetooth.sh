#!/usr/bin/env bash
set -euo pipefail

MAC="48:D0:CF:BD:CE:35"
DESC1="0x0025"
DESC2="0x0026"
WEBHOOK="http://homeassistant.local:8123/api/webhook/beoremote-event"

while true; do
  echo "=== CLEANUP ==="
  pkill -f "gatttool -b $MAC" 2>/dev/null || true

  echo "=== RESET HCI ==="
  sudo hciconfig hci0 down;  sleep 0.5
  sudo hciconfig hci0 up;  sleep 0.5

  echo "=== SPAWN gatttool ==="
  # stderr → /dev/null so GLib warnings disappear
  coproc GTOOL { gatttool -b "$MAC" -I 2>&1; }
  GIN="${GTOOL[1]}"
  GOUT="${GTOOL[0]}"
  GPID=$!

  echo "=== CONNECTING ==="
  # keep issuing "connect" until success
  while true; do
    echo "connect" >&"$GIN"
    while read -r -u "$GOUT" line; do
      [[ -n "$line" ]] && echo "[gatttool] $line"
      if [[ "$line" == *"Connection successful"* ]]; then
        echo ">>> Connected!"
        break 2
      elif [[ "$line" == *"Connection refused"* ]]; then
        echo ">>> Refused—retry in 1s"
        sleep 1
        break
      elif [[ "$line" == *"Function not implemented"* ]]; then
        echo ">>> HCI function not implemented, restarting…"
        kill "$GPID" 2>/dev/null || true
        sleep 2
        break 3
      elif [[ "$line" =~ ^Error: ]]; then
        echo ">>> Fatal error, restarting..."
        kill "$GPID" 2>/dev/null || true
        exec 1>&3 2>&4   # restore fds if you remapped them
        sleep 2
        continue 3      # back to top of the big while
      fi
    done
  done

  echo "=== LISTENING ==="
  pressed=false

  # Listen loop: breaks back to outer while on EOF or FD error
  while read -r -u "$GOUT" line; do
    echo "[gatttool] $line"

    # 1) Catch GLib warnings and restart
    if [[ "$line" == *"GLib-WARNING"* ]]; then
      echo ">>> Caught GLib warning, restarting…"
      break
    fi

    # 2) Catch invalid FD
    if [[ "$line" == *"Invalid file descriptor"* ]]; then
      echo ">>> Invalid FD, restarting…"
      break
    fi

    # parse notifications
    if [[ "$line" =~ Notification[[:space:]]handle[[:space:]]\=[[:space:]]([^[:space:]]+)[[:space:]]value:[[:space:]]([0-9A-Fa-f]{2})[[:space:]]([0-9A-Fa-f]{2}) ]]; then
      address="${BASH_REMATCH[1]}"
      command="${BASH_REMATCH[2],,}"

      if [[ "$command" != "00" && $pressed == false ]]; then
        echo "[EVENT] Press: $command"
          # fire webhook
          curl -G "${WEBHOOK}" \
            --silent --output /dev/null \
            --data-urlencode "address=${address}" \
            --data-urlencode "command=${command}"
        pressed=true
      elif [[ "$command" == "00" && $pressed == true ]]; then
        # echo "[EVENT] Release"
        pressed=false
      fi
    fi
  done

  echo "=== RESTARTING IN 5s ==="
  kill "$GPID" 2>/dev/null || true
  sleep 5
done
