### /boot/firmware/config.txt
## Test
# — BeoSound 5 Panel (10.4" 1024×768 @ 60Hz) forced HDMI settings —
hdmi_force_hotplug=1      # pretend HDMI is always connected
disable_overscan=1        # remove any black borders

hdmi_group=2              # use DMT timings (computer monitor)
hdmi_mode=16              # 1024x768 @ 60 Hz (DMT mode 16)
hdmi_drive=2              # full HDMI (with audio), not DVI mode

# framebuffer console to match panel
framebuffer_width=1024
framebuffer_height=768

# IR driver
dtoverlay=gpio-ir,gpio_pin=18

## USB setup
sudo tee /etc/udev/rules.d/99-bs5.rules <<'EOF'
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", \
  ATTRS{idVendor}=="0cd4", ATTRS{idProduct}=="1112", \
  MODE="0666"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger

## Install packages
sudo apt update
pip3 uninstall -y hid
sudo apt remove --purge python3-hid

# Install the true hidapi binding
sudo apt install -y libhidapi-hidraw0 libhidapi-dev python3-dev
sudo apt install libhidapi-hidraw0 python3-hidapi

# Set up IR driver
sudo apt install lirc

## HA configuration
# Kiosk mode for nicer integration into iframe
https://github.com/NemesisRE/kiosk-mode

# configuration.yaml update (all in root config)
frontend:
  themes: !include_dir_merge_named themes
  extra_module_url:
   - /hacsfiles/kiosk-mode/kiosk-mode.js?v1.0.0

http:
  cors_allowed_origins:
    - "*"   # allow any client to call HA’s REST API
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
