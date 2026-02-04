# BeoSound 5c

A modern recreation of the Bang & Olufsen BeoSound 5 experience using web technologies and a Raspberry Pi 5.

**Website: [www.beosound5c.com](https://www.beosound5c.com)**

This project replaces the original BeoSound 5 software with a circular arc-based touch UI that integrates with Sonos players, Spotify, and Home Assistant. It works with the original BS5 hardware (rotary encoder, laser pointer, display) and supports BeoRemote One for wireless control.

![BeoSound 5c Interface](assets/splashscreen-red.png)

## Quick Start

### Try Without Hardware (Emulator Mode)

The web interface includes built-in hardware emulation using keyboard and mouse/trackpad:

```bash
cd web
python3 -m http.server 8000
# Open http://localhost:8000
```

**Controls:**
- Laser pointer: Mouse wheel / trackpad scroll
- Navigation wheel: Arrow Up/Down
- Buttons: Arrow Left/Right, Enter

### Install on Raspberry Pi 5

1. Flash **Raspberry Pi OS Lite (64-bit)** and enable SSH
2. Clone and run the installer:

```bash
git clone https://github.com/mkirsten/beosound5c.git ~/beosound5c
cd ~/beosound5c
sudo ./install.sh
sudo reboot
```

The installer handles everything: packages, USB permissions, display config, and services.

## Architecture

The system runs coordinated services communicating via WebSockets:

```
BS5 Hardware (USB) → input.py (8765) → Web UI
Sonos Player → media.py (8766) → Web UI
IR/MasterLink → masterlink.py → Home Assistant
BeoRemote One → bluetooth.sh → Home Assistant
Static Files → HTTP (8000) → Chromium Kiosk
```

### Services

| Service | Purpose |
|---------|---------|
| `beo-input` | USB HID input from BS5 rotary encoder and buttons |
| `beo-media` | Sonos monitoring with artwork caching |
| `beo-masterlink` | B&O IR and MasterLink processing |
| `beo-bluetooth` | BeoRemote One wireless control |
| `beo-http` | Static web server |
| `beo-ui` | Chromium kiosk display |

### Directory Structure

```
web/                 # Web UI (HTML, JS, CSS)
├── js/              # UI logic, hardware emulation
├── json/            # Scenes, settings, playlists
└── softarc/         # Arc-based navigation pages
services/            # Python/shell backend services
└── system/          # Systemd service files
tools/               # Spotify OAuth, USB debugging, etc.
```

## Configuration

After installation, edit `/etc/beosound5c/config.env` to configure:
- Sonos player IP
- Home Assistant URL and webhook
- BeoRemote One MAC address

### Pairing BeoRemote One

1. On remote: LIST → Settings → Pairing → Pair
2. On device:
```bash
sudo systemctl stop beo-bluetooth
bluetoothctl scan on       # Look for "BEORC"
bluetoothctl pair XX:XX:XX:XX:XX:XX
bluetoothctl trust XX:XX:XX:XX:XX:XX
```
3. Update `BEOREMOTE_MAC` in config and restart: `sudo systemctl start beo-bluetooth`

## Home Assistant Integration

Add to `configuration.yaml`:

```yaml
http:
  cors_allowed_origins:
    - "http://<BEOSOUND5C_IP>:8000"
  use_x_frame_options: false

homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - <BEOSOUND5C_IP>
      allow_bypass_login: true
    - type: homeassistant
```

See `homeassistant/example-automation.yaml` for webhook handling examples.

## Status

Active personal project, functional for daily use. Contributions welcome.

## Acknowledgments

Arc geometry in `web/js/arcs.js` derived from [Beolyd5](https://github.com/larsbaunwall/Beolyd5) by Lars Baunwall (Apache 2.0).
