# BeoSound 5c

A modern recreation of the Bang & Olufsen BeoSound 5 experience using web technologies and a Raspberry Pi 5.

**Website: [www.beosound5c.com](https://www.beosound5c.com)**

This project replaces the original BeoSound 5 software with a circular arc-based touch UI that integrates with Sonos players, Spotify, and Home Assistant. It works with the original BS5 hardware (rotary encoder, laser pointer, display) and supports BeoRemote One for wireless control.

I built this for my own setup, but it runs daily on multiple BeoSound 5 units. Your setup may require some configuration — particularly for Home Assistant integration.

## Quick Start

### Try Without Hardware (Emulator Mode)

The web interface includes built-in hardware emulation using keyboard and mouse/trackpad:

```bash
# Start web server
cd web && python3 -m http.server 8000

# Optional: Start media server for Sonos artwork
cd services && python3 media.py

# Open http://localhost:8000
```

**Controls:**
- Laser pointer: Mouse wheel / trackpad scroll
- Navigation wheel: Arrow Up/Down
- Buttons: Arrow Left/Right, Enter

For Sonos integration in emulator mode, configure the Sonos IP in `services/config.env` (copy from `services/config.env.example`).

### Install on Raspberry Pi 5

Tested on [Raspberry Pi 5 4GB](https://www.raspberrypi.com/products/raspberry-pi-5/). 

1. Flash **Raspberry Pi OS Lite (64-bit)** and enable SSH
2. Clone and run the installer:

```bash
git clone https://github.com/mkirsten/beosound5c.git ~/beosound5c
cd ~/beosound5c
sudo ./install.sh
```

The installer handles everything: packages, USB permissions, display config, service installation, configuration prompts, and optional BeoRemote One pairing. It will ask if you want to reboot when complete.

## Configuration

Configuration is set during installation and stored in `/etc/beosound5c/config.env`.

See [`services/config.env.example`](services/config.env.example) for all available options:

```bash
# Required
DEVICE_NAME="Living Room"           # Identifies this unit in Home Assistant
HA_URL="http://homeassistant.local:8123"
HA_WEBHOOK_URL="http://homeassistant.local:8123/api/webhook/beosound5c"
SONOS_IP="192.168.1.100"

# Optional
HA_SECURITY_DASHBOARD="dashboard-cameras/home"  # HA dashboard for SECURITY page
BEOREMOTE_MAC="00:00:00:00:00:00"   # BeoRemote One Bluetooth MAC
SPOTIFY_USER_ID=""                   # For playlist fetching
```

To reconfigure: `sudo nano /etc/beosound5c/config.env` then restart services.

## Services

| Service | File | Description |
|---------|------|-------------|
| `beo-input` | [`services/input.py`](services/input.py) | Python USB HID driver for BS5 rotary encoder, buttons, and laser pointer |
| `beo-media` | [`services/media.py`](services/media.py) | Python WebSocket server for Sonos monitoring with artwork caching |
| `beo-masterlink` | [`services/masterlink.py`](services/masterlink.py) | Python USB sniffer for B&O IR and MasterLink bus commands |
| `beo-bluetooth` | [`services/bluetooth.sh`](services/bluetooth.sh) | Bash script for BeoRemote One wireless control with debouncing |
| `beo-http` | — | Python simple HTTP server for static files |
| `beo-ui` | [`services/ui.sh`](services/ui.sh) | Bash script launching Chromium in kiosk mode (1024×768) |

Service definitions: [`services/system/`](services/system/)

## Directory Structure

```
web/                 # Web UI (HTML, CSS, JavaScript)
├── js/              # UI logic, hardware emulation, config
├── json/            # Scenes, settings, playlists
└── softarc/         # Arc-based navigation subpages
services/            # Python and Bash backend services
├── system/          # Systemd service files and install scripts
└── config.env.example
tools/               # Spotify OAuth, USB debugging utilities
```

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

**Security note**: These settings allow the BeoSound 5c to embed Home Assistant pages and send webhooks without authentication. Only add IPs you trust to `trusted_networks` and `cors_allowed_origins`. This is intended for devices on your local network.

See [`homeassistant/example-automation.yaml`](homeassistant/example-automation.yaml) for webhook handling examples.

## Acknowledgments

Arc geometry in `web/js/arcs.js` derived from [Beolyd5](https://github.com/larsbaunwall/Beolyd5) by Lars Baunwall (Apache 2.0).

This project is not affiliated with Bang & Olufsen. "Bang & Olufsen", "BeoSound", "BeoRemote", and "MasterLink" are trademarks of Bang & Olufsen A/S.
