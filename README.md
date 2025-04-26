## beosound5c
Beosound 5c Recreatedpython





## /boot/firmware/config.txt
# — BeoSound 5 Panel (10.4" 1024×768 @ 60Hz) forced HDMI settings —
hdmi_force_hotplug=1      # pretend HDMI is always connected
disable_overscan=1        # remove any black borders

hdmi_group=2              # use DMT timings (computer monitor)
hdmi_mode=16              # 1024x768 @ 60 Hz (DMT mode 16)
hdmi_drive=2              # full HDMI (with audio), not DVI mode

# framebuffer console to match panel
framebuffer_width=1024
framebuffer_height=768


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
