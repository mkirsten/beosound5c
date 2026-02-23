#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Network discovery helpers
# =============================================================================
# Sourced by install.sh. Uses globals and logging from common.sh.

# Scan for Sonos devices on the network
scan_sonos_devices() {
    log_info "Scanning for Sonos devices on the network..."
    local sonos_devices=()
    local timeout=2

    # Method 1: Try avahi/mDNS discovery (most reliable)
    if command -v avahi-browse &>/dev/null; then
        while IFS= read -r line; do
            if [[ "$line" =~ ([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+) ]]; then
                local ip="${BASH_REMATCH[1]}"
                if timeout $timeout bash -c "echo >/dev/tcp/$ip/1400" 2>/dev/null; then
                    local name=$(curl -s --connect-timeout $timeout "http://$ip:1400/xml/device_description.xml" 2>/dev/null | grep -oP '(?<=<roomName>)[^<]+' | head -1)
                    if [ -n "$name" ]; then
                        sonos_devices+=("$ip|$name")
                    else
                        sonos_devices+=("$ip|Sonos Device")
                    fi
                fi
            fi
        done < <(avahi-browse -rtp _sonos._tcp 2>/dev/null | grep "=" | head -10)
    fi

    # Method 2: Fallback - scan common ports if avahi didn't find anything
    if [ ${#sonos_devices[@]} -eq 0 ]; then
        local network=$(ip route | grep -oP 'src \K[0-9.]+' | head -1 | sed 's/\.[0-9]*$/./')
        if [ -n "$network" ]; then
            log_info "Scanning network ${network}0/24 for Sonos devices (port 1400)..."
            local tmpfile
            tmpfile=$(mktemp)
            (
                for i in $(seq 1 254); do
                    local ip="${network}${i}"
                    if timeout $timeout bash -c "echo >/dev/tcp/$ip/1400" 2>/dev/null; then
                        local name=$(curl -s --connect-timeout $timeout "http://$ip:1400/xml/device_description.xml" 2>/dev/null | grep -oP '(?<=<roomName>)[^<]+' | head -1)
                        if [ -n "$name" ]; then
                            echo "$ip|$name" >> "$tmpfile"
                        fi
                    fi
                done
            ) &
            local scan_pid=$!
            sleep 10
            kill $scan_pid 2>/dev/null
            wait $scan_pid 2>/dev/null
            while IFS= read -r line; do
                sonos_devices+=("$line")
            done < "$tmpfile"
            rm -f "$tmpfile"
        fi
    fi

    if [ ${#sonos_devices[@]} -gt 0 ]; then
        printf '%s\n' "${sonos_devices[@]}"
    fi
}

# Scan for Bluesound devices on the network
scan_bluesound_devices() {
    log_info "Scanning for Bluesound devices on the network..."
    local bluesound_devices=()
    local timeout=2

    # Method 1: Try avahi/mDNS discovery (_musc._tcp is BluOS service type)
    if command -v avahi-browse &>/dev/null; then
        while IFS= read -r line; do
            if [[ "$line" =~ ([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+) ]]; then
                local ip="${BASH_REMATCH[1]}"
                if timeout $timeout bash -c "echo >/dev/tcp/$ip/11000" 2>/dev/null; then
                    local name=$(curl -s --connect-timeout $timeout "http://$ip:11000/SyncStatus" 2>/dev/null | grep -oP '(?<=<name>)[^<]+' | head -1)
                    if [ -z "$name" ]; then
                        name=$(curl -s --connect-timeout $timeout "http://$ip:11000/Status" 2>/dev/null | grep -oP '(?<=<name>)[^<]+' | head -1)
                    fi
                    if [ -n "$name" ]; then
                        bluesound_devices+=("$ip|$name")
                    else
                        bluesound_devices+=("$ip|Bluesound Device")
                    fi
                fi
            fi
        done < <(avahi-browse -rtp _musc._tcp 2>/dev/null | grep "=" | head -10)
    fi

    # Method 2: Fallback - scan for port 11000 on local network
    if [ ${#bluesound_devices[@]} -eq 0 ]; then
        local network=$(ip route | grep -oP 'src \K[0-9.]+' | head -1 | sed 's/\.[0-9]*$/./')
        if [ -n "$network" ]; then
            log_info "Scanning network ${network}0/24 for Bluesound devices (port 11000)..."
            local tmpfile
            tmpfile=$(mktemp)
            (
                for i in $(seq 1 254); do
                    local ip="${network}${i}"
                    if timeout $timeout bash -c "echo >/dev/tcp/$ip/11000" 2>/dev/null; then
                        local name=$(curl -s --connect-timeout $timeout "http://$ip:11000/SyncStatus" 2>/dev/null | grep -oP '(?<=<name>)[^<]+' | head -1)
                        if [ -n "$name" ]; then
                            echo "$ip|$name" >> "$tmpfile"
                        fi
                    fi
                done
            ) &
            local scan_pid=$!
            sleep 10
            kill $scan_pid 2>/dev/null
            wait $scan_pid 2>/dev/null
            while IFS= read -r line; do
                bluesound_devices+=("$line")
            done < "$tmpfile"
            rm -f "$tmpfile"
        fi
    fi

    if [ ${#bluesound_devices[@]} -gt 0 ]; then
        printf '%s\n' "${bluesound_devices[@]}"
    fi
}

# Detect Home Assistant on the network
detect_home_assistant() {
    log_info "Looking for Home Assistant..."
    local ha_urls=()
    local timeout=3

    # Method 1: Try homeassistant.local first (most common)
    if curl -s --connect-timeout $timeout -o /dev/null -w "%{http_code}" "http://homeassistant.local:8123/api/" 2>/dev/null | grep -qE "^(200|401|403)$"; then
        ha_urls+=("http://homeassistant.local:8123")
        log_success "Found Home Assistant at homeassistant.local:8123"
    fi

    # Method 2: Try common hostnames
    for hostname in "home-assistant.local" "hass.local" "ha.local"; do
        if [ ${#ha_urls[@]} -eq 0 ]; then
            if curl -s --connect-timeout $timeout -o /dev/null -w "%{http_code}" "http://${hostname}:8123/api/" 2>/dev/null | grep -qE "^(200|401|403)$"; then
                ha_urls+=("http://${hostname}:8123")
                log_success "Found Home Assistant at ${hostname}:8123"
            fi
        fi
    done

    # Method 3: Scan local network for port 8123
    if [ ${#ha_urls[@]} -eq 0 ]; then
        local network=$(ip route | grep -oP 'src \K[0-9.]+' | head -1 | sed 's/\.[0-9]*$/./')
        if [ -n "$network" ]; then
            log_info "Scanning network ${network}0/24 for Home Assistant (port 8123)..."
            for i in $(seq 1 254); do
                local ip="${network}${i}"
                if timeout $timeout bash -c "echo >/dev/tcp/$ip/8123" 2>/dev/null; then
                    if curl -s --connect-timeout $timeout "http://$ip:8123/api/" 2>/dev/null | grep -q "API running"; then
                        ha_urls+=("http://$ip:8123")
                        log_success "Found Home Assistant at $ip:8123"
                        break
                    fi
                fi
            done
        fi
    fi

    if [ ${#ha_urls[@]} -gt 0 ]; then
        printf '%s\n' "${ha_urls[@]}"
    fi
}

# Display menu and get user selection
# Usage: if selection=$(select_from_list "Prompt:" "${options[@]}"); then ...
select_from_list() {
    local prompt="$1"
    shift
    local options=("$@")
    local count=${#options[@]}

    if [ $count -eq 0 ]; then
        return 1
    fi

    echo ""
    echo "$prompt"
    for i in "${!options[@]}"; do
        echo "  $((i+1))) ${options[$i]}"
    done
    echo "  $((count+1))) Enter manually"
    echo ""

    while true; do
        read -p "Select option [1-$((count+1))]: " choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le $((count+1)) ]; then
            if [ "$choice" -eq $((count+1)) ]; then
                return 1  # User wants manual entry
            else
                echo "${options[$((choice-1))]}"
                return 0
            fi
        fi
        echo "Invalid selection. Please enter a number between 1 and $((count+1))."
    done
}
