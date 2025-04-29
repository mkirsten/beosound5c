# BeoSound 5 Controller

A web-based controller interface for BeoSound 5, with Home Assistant integration.

## Running the Application

There are two ways to run this application:

### Option 1: Using the Built-in Server (Recommended)

This approach provides the best experience with full functionality, including access to Home Assistant features.

1. Make sure you have Python installed
2. Run the server script:
   ```
   python server.py
   ```
3. Your browser will open automatically to http://localhost:8000/web/index.html

If the browser doesn't open automatically, manually navigate to:
http://localhost:8000/web/index.html

### Option 2: Direct File Access (Limited Functionality)

You can run the application by directly opening the HTML file, but with some limitations:

1. Navigate to the `web` folder
2. Open `index.html` in your browser

**Note**: When running directly from file, the following limitations apply:
- The Home Assistant integration (Doorcam and Home Status pages) will not work due to browser security restrictions
- You'll see informational messages explaining how to access these features

## Features

- **Playing Now**: View and control currently playing media
- **Playlists**: Browse and select playlists
- **Scenes**: (Under construction)
- **Security**: (Under construction)
- **Control**: (Under construction)
- **Settings**: (Under construction)
- **Doorcam**: View doorbell camera via Home Assistant
- **Home Status**: Monitor home status via Home Assistant

## Browser Requirements

This application works best in modern browsers:
- Chrome/Edge (recommended)
- Firefox
- Safari

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
