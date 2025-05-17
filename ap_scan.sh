#!/bin/bash

CURRENT_BSSID="e0:63:da:a8:2e:1a"
IFACE="wlan1"

SSID=$(iwgetid "$IFACE" --raw)

if [ -z "$SSID" ]; then
  echo "Not connected to any Wi-Fi network."
  exit 1
fi

# Scan networks and clean up output
sudo iw "$IFACE" scan | awk -v ssid="$SSID" -v current="$CURRENT_BSSID" '
/^BSS / {
  bssid = $2
  gsub(/\(.*\)/, "", bssid)
}
/SSID:/ {
  ssid_line = $0
  sub(/^.*SSID: /, "", ssid_line)
}
/signal:/ {
  signal_line = $0
  sub(/^.*signal: /, "", signal_line)
  sub(/ dBm.*/, "", signal_line)

  if (ssid_line == ssid) {
    signal = signal_line + 0
    if (bssid == current) {
      current_sig = signal
    } else {
      if (signal > current_sig + 5) {
        print "Better AP found: " bssid " (" signal " dBm)"
        found = 1
      }
    }
  }
}
END {
  if (!found) {
    print "No better AP found."
  }
}'
