#!/usr/bin/env bash
set -euo pipefail

MAC="48:D0:CF:BD:CE:35"
DESC1="0x0025"
DESC2="0x0026"

while true; do
  echo "=== STATE: CLEANUP ==="
  pkill -f "gatttool -b $MAC" 2>/dev/null || true

  echo "=== STATE: ADAPTER RESET ==="
  sudo hciconfig hci0 down
  sleep 0.5
  sudo hciconfig hci0 up
  sleep 0.5

  echo "=== STATE: SPAWN gatttool ==="
  # Launch gatttool as a coprocess
  coproc GTOOL { gatttool -b "$MAC" -I; }
  GTOOL_IN="${GTOOL[1]}"
  GTOOL_OUT="${GTOOL[0]}"
  GTOOL_PID=$!

  echo "=== STATE: CONNECT ATTEMPT ==="
  connected=false
  until $connected; do
    echo "[STATE] Sending connect…"
    printf "connect\n" >&"$GTOOL_IN"

    # Read lines until we see success, refusal, or fatal error
    while read -r -u "$GTOOL_OUT" line; do
      echo "[gatttool] $line"
      if [[ "$line" == *"Connection successful"* ]]; then
        connected=true
        echo "[STATE] Connected!"
        break
      fi
      if [[ "$line" == *"Connection refused"* ]]; then
        echo "[gatttool] Connection refused, retrying in 1s…"
        sleep 1
        break
      fi
      if [[ "$line" =~ ^Error: ]]; then
        echo "[gatttool] Fatal connect error, restarting…"
        break 2   # out to START
      fi
    done
  done

  echo "=== STATE: SUBSCRIBE ==="
  printf "char-write-req %s 0100\n" "$DESC1" >&"$GTOOL_IN"
  printf "char-write-req %s 0100\n" "$DESC2" >&"$GTOOL_IN"
  sleep 0.1

  echo "=== STATE: LISTEN LOOP ==="
  pressed=false

  # Main listen loop
  while read -r -u "$GTOOL_OUT" line; do
    echo "[gatttool] $line"

    # Handle broken pipe / invalid fd
    if [[ "$line" == *"Invalid file descriptor"* ]]; then
      echo "[ERROR] Invalid FD, restarting…"
      break
    fi

    # Match notifications
    if [[ "$line" =~ Notification\ handle\ =\ ([^[:space:]]+)\ value:\ ([0-9A-Fa-f]{2})\ ([0-9A-Fa-f]{2}) ]]; then
      code="${BASH_REMATCH[2],,}"

      if [[ "$code" != "00" && $pressed == false ]]; then
        echo "[EVENT] Button pressed → code: $code"
        pressed=true
      elif [[ "$code" == "00" && $pressed == true ]]; then
        echo "[EVENT] Button released → code: $code"
        pressed=false
      fi
    fi
  done

  echo "=== ITERATION ENDED — restarting in 5s ==="
  # Clean-up
  kill "$GTOOL_PID" 2>/dev/null || true
  exec 3>&- 4<&-  # close fds if they were remapped
  sleep 5
done
