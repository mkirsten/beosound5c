#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Audio HAT detection and configuration
# =============================================================================
#
# Detects I2S audio HATs on the Raspberry Pi GPIO header and configures
# the appropriate device-tree overlay in /boot/firmware/config.txt.
#
# Supported HATs (detected by I2C chip probe or EEPROM):
#   - InnoMaker Digi One (WM8804 @ 0x3b)  → dtoverlay=allo-digione
#   - HiFiBerry Digi/Digi+  (WM8804 @ 0x3b) → dtoverlay=hifiberry-digi
#   - HiFiBerry DAC+        (PCM5122 @ 0x4d) → dtoverlay=hifiberry-dacplus
#   - IQaudIO DAC+          (PCM5122 @ 0x4c) → dtoverlay=iqaudio-dacplus
#
# The detected HAT type is stored in /etc/beosound5c/audio-hat for runtime
# services to read (e.g., SYSTEM page display).
#
# Safe on RPis without a HAT — the overlay probes fail gracefully.
# =============================================================================

AUDIO_HAT_STATE_FILE="/etc/beosound5c/audio-hat"

detect_audio_hat() {
    # Returns the HAT name via $DETECTED_HAT and overlay via $DETECTED_OVERLAY
    # Returns 1 if no HAT detected.
    DETECTED_HAT=""
    DETECTED_OVERLAY=""

    local BOOT_CONFIG="/boot/firmware/config.txt"
    [ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"

    # Method 1: Check HAT EEPROM (populated by well-behaved HATs)
    local hat_product
    hat_product=$(cat /proc/device-tree/hat/product 2>/dev/null | tr -d '\0')
    if [ -n "$hat_product" ]; then
        case "$hat_product" in
            *"Digi One"*|*"DigiOne"*)
                DETECTED_HAT="InnoMaker Digi One"
                DETECTED_OVERLAY="allo-digione"
                return 0 ;;
            *"HiFiBerry Digi"*)
                DETECTED_HAT="HiFiBerry Digi"
                DETECTED_OVERLAY="hifiberry-digi"
                return 0 ;;
            *"HiFiBerry DAC"*)
                DETECTED_HAT="HiFiBerry DAC+"
                DETECTED_OVERLAY="hifiberry-dacplus"
                return 0 ;;
            *"IQaudIO"*|*"IQaudio"*)
                DETECTED_HAT="IQaudIO DAC+"
                DETECTED_OVERLAY="iqaudio-dacplus"
                return 0 ;;
        esac
    fi

    # Method 2: Check if the overlay is already loaded and producing a sound card
    if grep -q "snd_allo_digione\|allo-digione" /proc/asound/cards 2>/dev/null; then
        DETECTED_HAT="InnoMaker Digi One"
        DETECTED_OVERLAY="allo-digione"
        return 0
    fi
    if grep -q "hifiberry.*digi\|HiFiBerry Digi" /proc/asound/cards 2>/dev/null; then
        DETECTED_HAT="HiFiBerry Digi"
        DETECTED_OVERLAY="hifiberry-digi"
        return 0
    fi

    # Method 3: I2C chip probe (requires i2c-tools and I2C enabled)
    if command -v i2cdetect &>/dev/null && [ -e /dev/i2c-1 ]; then
        local i2c_scan
        i2c_scan=$(i2cdetect -y 1 2>/dev/null)

        # WM8804 at 0x3b → S/PDIF HAT (Digi One or HiFiBerry Digi)
        if echo "$i2c_scan" | grep -q " 3b "; then
            # Distinguish by EEPROM or default to Digi One (most common WM8804 HAT)
            # If allo-digione overlay is already in config.txt, it's a Digi One
            if grep -q "allo-digione" "$BOOT_CONFIG" 2>/dev/null; then
                DETECTED_HAT="InnoMaker Digi One"
                DETECTED_OVERLAY="allo-digione"
            else
                # Default WM8804 HATs to allo-digione (compatible driver)
                DETECTED_HAT="InnoMaker Digi One"
                DETECTED_OVERLAY="allo-digione"
            fi
            return 0
        fi

        # PCM5122 at 0x4d → HiFiBerry DAC+
        if echo "$i2c_scan" | grep -q " 4d "; then
            DETECTED_HAT="HiFiBerry DAC+"
            DETECTED_OVERLAY="hifiberry-dacplus"
            return 0
        fi

        # PCM5122 at 0x4c → IQaudIO DAC+
        if echo "$i2c_scan" | grep -q " 4c "; then
            DETECTED_HAT="IQaudIO DAC+"
            DETECTED_OVERLAY="iqaudio-dacplus"
            return 0
        fi
    fi

    return 1
}

setup_audio_hat() {
    log_section "Audio HAT Detection"

    local BOOT_CONFIG="/boot/firmware/config.txt"
    [ ! -f "$BOOT_CONFIG" ] && BOOT_CONFIG="/boot/config.txt"

    # Ensure I2C and I2S are enabled (needed for HAT detection and operation)
    local need_reboot=false

    if grep -q "^#dtparam=i2c_arm=on" "$BOOT_CONFIG" 2>/dev/null; then
        log_info "Enabling I2C bus..."
        sed -i 's/^#dtparam=i2c_arm=on/dtparam=i2c_arm=on/' "$BOOT_CONFIG"
        need_reboot=true
    fi

    if grep -q "^#dtparam=i2s=on" "$BOOT_CONFIG" 2>/dev/null; then
        log_info "Enabling I2S bus..."
        sed -i 's/^#dtparam=i2s=on/dtparam=i2s=on/' "$BOOT_CONFIG"
        need_reboot=true
    fi

    # Install i2c-tools if missing (needed for chip detection)
    if ! command -v i2cdetect &>/dev/null; then
        log_info "Installing i2c-tools..."
        apt-get install -y -qq i2c-tools > /dev/null 2>&1
    fi

    # WirePlumber: always set volume to 100% (external adapters handle actual level)
    mkdir -p /etc/wireplumber/wireplumber.conf.d
    cp "$INSTALL_DIR/configs/51-beosound5c-volume.conf" /etc/wireplumber/wireplumber.conf.d/
    log_info "WirePlumber default volume set to 100%"

    # Try to detect the HAT
    if detect_audio_hat; then
        log_success "Detected audio HAT: $DETECTED_HAT"

        # Add overlay if not already present
        if ! grep -q "^dtoverlay=$DETECTED_OVERLAY" "$BOOT_CONFIG" 2>/dev/null; then
            log_info "Adding dtoverlay=$DETECTED_OVERLAY to $BOOT_CONFIG..."
            cat >> "$BOOT_CONFIG" << EOF

# Audio HAT: $DETECTED_HAT (S/PDIF digital output)
dtoverlay=$DETECTED_OVERLAY
EOF
            need_reboot=true
            log_success "Overlay added"
        else
            log_info "Overlay dtoverlay=$DETECTED_OVERLAY already present"
        fi

        # Save HAT info for runtime services
        mkdir -p "$(dirname "$AUDIO_HAT_STATE_FILE")"
        cat > "$AUDIO_HAT_STATE_FILE" << EOF
HAT_NAME=$DETECTED_HAT
HAT_OVERLAY=$DETECTED_OVERLAY
HAT_CARD=sndallodigione
EOF
        log_info "HAT info saved to $AUDIO_HAT_STATE_FILE"

    elif aplay -l 2>/dev/null | grep -qi "usb"; then
        # No HAT but USB audio device present (e.g. DollaTek PCM2704 S/PDIF)
        local usb_card
        usb_card=$(aplay -l 2>/dev/null | grep -i "usb" | head -1 | sed 's/.*: \(.*\) \[.*/\1/' | xargs)
        log_success "Detected USB audio: ${usb_card:-USB Audio Device}"

        # Save USB audio info for runtime services
        mkdir -p "$(dirname "$AUDIO_HAT_STATE_FILE")"
        cat > "$AUDIO_HAT_STATE_FILE" << EOF
HAT_NAME=USB Audio (${usb_card:-USB S/PDIF})
HAT_OVERLAY=none
HAT_CARD=usb
EOF
        log_info "USB audio info saved to $AUDIO_HAT_STATE_FILE"

        # Give USB audio sink priority over HDMI in WirePlumber
        cp "$INSTALL_DIR/configs/52-beosound5c-usb-audio.conf" /etc/wireplumber/wireplumber.conf.d/
        log_info "WirePlumber USB audio priority rule installed"

    else
        log_info "No audio HAT or USB audio detected (HDMI audio only)"
        # Clean up state file if no dedicated audio output
        rm -f "$AUDIO_HAT_STATE_FILE"
    fi

    if [ "$need_reboot" = true ]; then
        log_warn "Reboot required for audio HAT changes to take effect"
    fi
}
