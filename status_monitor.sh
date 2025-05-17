#!/bin/bash

# Refresh interval (seconds)
INTERVAL=3

# Color helpers
green='\033[1;32m'
yellow='\033[1;33m'
red='\033[1;31m'
blue='\033[1;34m'
reset='\033[0m'

# Function to colorize signal quality
quality_icon() {
    local signal=$1
    if [ $signal -ge -60 ]; then
        echo -e "${green}📶 Excellent ($signal dBm)${reset}"
    elif [ $signal -ge -70 ]; then
        echo -e "${yellow}📶 Good ($signal dBm)${reset}"
    elif [ $signal -ge -80 ]; then
        echo -e "${red}📶 Weak ($signal dBm)${reset}"
    else
        echo -e "${red}📶 Very Poor ($signal dBm)${reset}"
    fi
}

while true; do
  clear
  echo -e "${blue}===== BEOSOUND 5C SYSTEM STATUS =====${reset}"

  echo -e "\n${blue}Hostname and Network Interfaces:${reset}"
  hostname
  ip -4 addr show | grep -v ' lo' | awk '/inet / {print $2, "on", $NF}'

  echo -e "\n${blue}Wi-Fi Info (wlan1):${reset}"
  SSID=$(iw wlan1 info 2>/dev/null | awk '/ssid/ {print $2}')
  BSSID=$(iw wlan1 link 2>/dev/null | awk '/Connected to/ {print $3}')
  SIGNAL=$(iw wlan1 link 2>/dev/null | awk '/signal:/ {print $2}')
  echo "SSID: $SSID"
  echo "BSSID: $BSSID"
  echo -n "Signal: "
  quality_icon $SIGNAL

  echo -e "\n${blue}Ping (8.8.8.8):${reset}"
  ping -c 1 -W 1 8.8.8.8 | grep 'time=' || echo "Ping failed"

  echo -e "\n${blue}Disk Usage:${reset}"
  df -h / | awk 'NR==1 || /\//'

  echo -e "\n${blue}CPU Temp & Throttling:${reset}"
  TEMP=$(vcgencmd measure_temp 2>/dev/null || echo "n/a")
  THROTTLE=$(vcgencmd get_throttled 2>/dev/null || echo "n/a")
  echo "Temp: $TEMP"
  echo "Throttling: $THROTTLE"

  echo -e "\n${blue}USB Devices:${reset}"
  lsusb

  echo -e "\nPress Ctrl+C to exit"
  sleep $INTERVAL
done
