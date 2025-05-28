#!/usr/bin/env bash
MAC="48:D0:CF:BD:CE:35"
DESC1="0x0025"
DESC2="0x0026"

while true; do
  echo "🧹 Cleaning up…"
  # kill any leftover gatttool sessions
  pkill -f "gatttool -b $MAC" 2>/dev/null || true

  # reset the HCI interface so “Resource busy” goes away
  echo "🔄 Resetting hci0…"
  sudo hciconfig hci0 down
  sleep 0.5
  sudo hciconfig hci0 up
  sleep 0.5

  echo "→ Connecting to $MAC…"
  gatttool -b "$MAC" -I <<-EOF
    connect
    # enable notifications
    char-write-req $DESC1 0100
    char-write-req $DESC2 0100
    # stay here and print notifications until disconnect
EOF

  echo "⚠️  Disconnected or error, retrying in 5 s…"
  sleep 5
done
