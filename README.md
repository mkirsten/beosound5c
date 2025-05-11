# Beosound 5c Recreated

This guide covers configuration for:

- 10.4" 1024×768 display via HDMI
- USB permissions for BeoSound 5 rotary encoder
- HID configuration
- IR configuration
- Required packages and software
- Home Assistant Configuration
  - Kiosk-mode Home Assistant interface
  - Trusted network login and CORS for iFrame embedding
  - IR processing of Beo4/Beo5/Beo6 events
  
---

## 🔧 `/boot/firmware/config.txt`

### 🖥 BeoSound 5 Panel — Forced HDMI Settings (1024×768 @ 60Hz)
```ini
hdmi_force_hotplug=1      # Pretend HDMI is always connected
disable_overscan=1        # Remove any black borders

hdmi_group=2              # Use DMT timings (computer monitor)
hdmi_mode=16              # 1024x768 @ 60 Hz (DMT mode 16)
hdmi_drive=2              # Full HDMI (with audio), not DVI mode
```

### 📐 Match Framebuffer to Panel Resolution
```ini
framebuffer_width=1024
framebuffer_height=768
```

---

## 🔌 USB Permissions (BeoSound 5 Rotary Encoder)

Create a custom `udev` rule to allow non-root access to the device:

```bash
sudo tee /etc/udev/rules.d/99-bs5.rules <<'EOF'
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", \
  ATTRS{idVendor}=="0cd4", ATTRS{idProduct}=="1112", \
  MODE="0666"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```

---

## 📦 Install Required Packages

### 🧼 Remove Conflicting Python `hid` Bindings
```bash
sudo apt update
pip3 uninstall -y hid
sudo apt remove --purge python3-hid
```

### ✅ Install True `hidapi` Backend
```bash
sudo apt install -y libhidapi-hidraw0 libhidapi-dev python3-dev
sudo apt install libhidapi-hidraw0 python3-hidapi
```

---

## 🏠 Home Assistant Configuration

### 🖼 Kiosk Mode for Fullscreen Display

Use [`kiosk-mode`](https://github.com/NemesisRE/kiosk-mode) to remove the HA sidebar and top bar when embedding in an iframe or touch interface.

### 🛠 `configuration.yaml`

Add the following sections to your HA config:

```yaml
frontend:
  themes: !include_dir_merge_named themes
  extra_module_url:
    - /hacsfiles/kiosk-mode/kiosk-mode.js?v1.0.0

http:
  cors_allowed_origins:
    - "*"  # Allow any client to call HA’s REST API
  use_x_forwarded_for: true
  use_x_frame_options: false
  trusted_proxies:
    - 127.0.0.1

homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - 192.168.1.233
        - 192.168.1.75
      trusted_users:
        192.168.1.233:
          - fa968e59c60a41ada8617d51349fd341
        192.168.1.75:
          - fa968e59c60a41ada8617d51349fd341
      allow_bypass_login: true
    - type: homeassistant
```

---
