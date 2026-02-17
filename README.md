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

# Optional: Start Sonos player for artwork
cd services && python3 players/sonos.py

# Open http://localhost:8000
```

**Controls:**
- Laser pointer: Mouse wheel / trackpad scroll
- Navigation wheel: Arrow Up/Down
- Buttons: Arrow Left/Right, Enter

For Sonos integration in emulator mode, configure the Sonos IP in `services/config.env` (copy from `services/config.env.example`).

### Install on Raspberry Pi 5

Tested on [Raspberry Pi 5 8GB](https://www.raspberrypi.com/products/raspberry-pi-5/), but lower RAM versions should work fine.

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
SONOS_IP="192.168.1.100"

# Transport: how BS5c communicates with Home Assistant
TRANSPORT_MODE="mqtt"               # webhook, mqtt, or both
HA_WEBHOOK_URL="http://homeassistant.local:8123/api/webhook/beosound5c"  # for webhook mode
MQTT_BROKER="homeassistant.local"   # for mqtt mode
MQTT_PORT="1883"
MQTT_USER=""
MQTT_PASSWORD=""

# Optional
HA_SECURITY_DASHBOARD="dashboard-cameras/home"  # HA dashboard for SECURITY page
BEOREMOTE_MAC="00:00:00:00:00:00"   # BeoRemote One Bluetooth MAC
SPOTIFY_USER_ID=""                   # For playlist fetching
```

To reconfigure: `sudo nano /etc/beosound5c/config.env` then restart services.

## Services

| Service | File | Description |
|---------|------|-------------|
| `beo-input` | [`services/input.py`](services/input.py) | USB HID driver for BS5 rotary encoder, buttons, and laser pointer |
| `beo-router` | [`services/router.py`](services/router.py) | Event router: dispatches remote events to HA or the active source, controls volume |
| `beo-sonos` | [`services/players/sonos.py`](services/players/sonos.py) | Sonos player monitor: artwork, metadata, volume reporting |
| `beo-cd` | [`services/sources/cd.py`](services/sources/cd.py) | CD player: disc detection, MusicBrainz metadata, mpv playback |
| `beo-masterlink` | [`services/masterlink.py`](services/masterlink.py) | USB sniffer for B&O IR and MasterLink bus commands |
| `beo-bluetooth` | [`services/bluetooth.py`](services/bluetooth.py) | HID service for BeoRemote One wireless control |
| `beo-http` | — | Simple HTTP server for static files |
| `beo-ui` | [`services/ui.sh`](services/ui.sh) | Chromium in kiosk mode (1024×768) |

Service definitions: [`services/system/`](services/system/)

### Sources, players, and volume adapters

The backend separates three concerns that can combine independently:

- **Sources** ([`services/sources/`](services/sources/)) own content and playback. A source registers with the router, gets a menu item, and receives remote control events when active. CD playback is a source; Spotify and USB are planned.

- **Players** ([`services/players/`](services/players/)) monitor external playback devices. A player watches what's happening on a device (track info, artwork, volume) and reports it to the UI. It doesn't provide content — the content could come from any source, or from someone's phone.

- **Volume adapters** ([`services/lib/volume_adapters/`](services/lib/volume_adapters/)) control the physical audio output. The router sends volume commands through whichever adapter matches the configured output (BeoLab 5 speakers via ESP32, or Sonos directly).

These three are independent. For example: **cd.py** (source) plays a CD through mpv, sending audio to Sonos via AirPlay. **sonos.py** (player) sees the Sonos playing and shows artwork in the UI. **BeoLab5Volume** (adapter) controls the volume on the BeoLab 5 speakers. Swap any piece — play Spotify instead of CD, use a different speaker — and the others don't change.

## Directory Structure

```
services/                   # Backend services
├── sources/                # Content providers (register with router)
│   ├── cd.py               #   CD player (beo-cd)
│   ├── spotify.py          #   Spotify Connect (stub)
│   └── usb.py              #   USB file playback (stub)
├── players/                # External playback monitors
│   └── sonos.py            #   Sonos monitor (beo-sonos)
├── lib/
│   ├── volume_adapters/    # Pluggable volume output control
│   │   ├── beolab5.py      #   BeoLab 5 via ESP32 REST API
│   │   ├── sonos.py        #   Sonos via SoCo
│   │   ├── powerlink.py    #   B&O PowerLink (stub)
│   │   └── digital_out.py  #   HDMI/S/PDIF (stub)
│   ├── transport.py        # HA communication (webhook/MQTT)
│   └── audio_outputs.py    # PipeWire sink discovery
├── router.py               # Event router (beo-router)
├── input.py                # USB HID input (beo-input)
├── bluetooth.py            # BeoRemote BLE (beo-bluetooth)
├── masterlink.py           # MasterLink IR (beo-masterlink)
└── system/                 # Systemd service files
web/                        # Web UI (HTML, CSS, JavaScript)
├── js/                     # UI logic, hardware emulation
├── json/                   # Scenes, settings, playlists
└── softarc/                # Arc-based navigation subpages
tools/                      # Spotify OAuth, USB debugging, BLE testing
```

## Home Assistant Integration

BeoSound 5c communicates with Home Assistant via **MQTT** (recommended) or **HTTP webhooks**. The transport is configured via `TRANSPORT_MODE` in `config.env`. The installer will prompt you to choose.

### MQTT Setup (recommended)

Requires an MQTT broker — the [Mosquitto add-on](https://github.com/home-assistant/addons/tree/master/mosquitto) works well. Create a user for the BS5c in the add-on config, then set `TRANSPORT_MODE="mqtt"` with your broker credentials.

MQTT topics use the pattern `beosound5c/{device}/out|in|status`:

```
beosound5c/living_room/out      → BS5c sends button events to HA
beosound5c/living_room/in       → HA sends commands to BS5c
beosound5c/living_room/status   → Online/offline (retained)
```

Example HA automation trigger:
```yaml
trigger:
  - platform: mqtt
    topic: "beosound5c/living_room/out"
```

Example HA command to BS5c:
```yaml
action:
  - action: mqtt.publish
    data:
      topic: "beosound5c/living_room/in"
      payload: '{"command": "wake", "params": {"page": "now_playing"}}'
```

### HA Configuration

Add to `configuration.yaml` (needed for the embedded Security page):

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

**Security note**: These settings allow the BeoSound 5c to embed Home Assistant pages without authentication. Only add IPs you trust to `trusted_networks` and `cors_allowed_origins`. This is intended for devices on your local network.

See [`homeassistant/example-automation.yaml`](homeassistant/example-automation.yaml) for complete automation examples covering both MQTT and webhook transports.

## Acknowledgments

Arc geometry in `web/js/arcs.js` derived from [Beolyd5](https://github.com/larsbaunwall/Beolyd5) by Lars Baunwall (Apache 2.0).

This project is not affiliated with Bang & Olufsen. "Bang & Olufsen", "BeoSound", "BeoRemote", and "MasterLink" are trademarks of Bang & Olufsen A/S.
