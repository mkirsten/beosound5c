#!/usr/bin/env bash
set -euo pipefail

# Load configuration from /etc/beosound5c/config.env if it exists
CONFIG_FILE="/etc/beosound5c/config.env"
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
fi

# Use environment variable (no default - must be configured)
MAC="${BEOREMOTE_MAC:?Set BEOREMOTE_MAC in /etc/beosound5c/config.env}"

# Handles for Bluetooth GATT (hardware-specific, don't change)
DESC1="0x0025"
DESC2="0x0026"

while true; do
  echo "=== STATE: CLEANUP ==="
  pkill -f "gatttool -b $MAC" 2>/dev/null || true

  echo "=== STATE: ADAPTER RESET ==="
  sudo hciconfig hci0 down; sleep 0.5
  sudo hciconfig hci0 up;   sleep 0.5

  echo "=== STATE: SPAWN gatttool ==="
  coproc GTOOL { gatttool -b "$MAC" -I; }
  GTOOL_IN="${GTOOL[1]}"
  GTOOL_OUT="${GTOOL[0]}"
  GTOOL_PID=$!

  echo "=== STATE: CONNECT ATTEMPT ==="
  until printf "connect\n" >&"$GTOOL_IN"; do :; done
  # wait for “Connection successful” or retry/refuse as before
  connected=false
  while read -r -u "$GTOOL_OUT" line; do
    echo "[gatttool] $line"
    if [[ "$line" == *"Connection successful"* ]]; then
      connected=true; echo "[STATE] Connected"; break
    elif [[ "$line" == *"Connection refused"* ]]; then
      echo "[gatttool] Connection refused, retrying…"
      sleep 1; printf "connect\n" >&"$GTOOL_IN"
    elif [[ "$line" =~ ^Error: ]]; then
      echo "[gatttool] Fatal error, restarting…"
      break 2    # back to START
    fi
  done
  $connected || { echo "=== CONNECT FAILED ==="; sleep 5; continue; }

  echo "=== STATE: SUBSCRIBE ==="
  printf "char-write-req %s 0100\n" "$DESC1" >&"$GTOOL_IN" 2>/dev/null
  printf "char-write-req %s 0100\n" "$DESC2" >&"$GTOOL_IN" 2>/dev/null
  sleep 0.1

  echo "=== STATE: LISTEN LOOP ==="
  pressed=false

  echo "=== STATE: LISTEN LOOP ==="
  pressed=false

  # ◆◆ LISTEN LOOP ◆◆
  while read -r -u "$GTOOL_OUT" line; do
    # Drop any GLib warnings immediately and trigger reconnect
    if [[ "$line" == *"GLib-WARNING"* ]]; then
      # echo "[DEBUG] Caught GLib warning, reconnecting…"
      break 2
    fi

    echo "[gatttool] $line"

    # On broken pipe / invalid fd ⇒ reconnect
    if [[ "$line" == *"Invalid file descriptor"* ]]; then
      echo "[ERROR] Invalid FD, reconnecting…"
      break 2
    fi

    # Notification parsing
    if [[ "$line" =~ Notification\ handle\ =\ ([^[:space:]]+)\ value:\ ([0-9A-Fa-f]{2})\ ([0-9A-Fa-f]{2}) ]]; then
      code="${BASH_REMATCH[2],,}"
      if [[ "$code" != "00" && $pressed == false ]]; then
        echo "[EVENT] Press → code: $code"
        pressed=true
      elif [[ "$code" == "00" && $pressed == true ]]; then
        echo "[EVENT] Release"
        pressed=false
      fi
    fi
  done

  echo "=== ITERATION ENDED — retrying in 5s ==="
  kill "$GTOOL_PID" 2>/dev/null || true
  sleep 5
done
