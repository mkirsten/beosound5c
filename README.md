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

# Open http://localhost:8000
```

The UI works fully without any backend services. Hardware input is simulated with keyboard and mouse.

**Controls:**
- Laser pointer: Mouse wheel / trackpad scroll
- Navigation wheel: Arrow Up/Down
- Volume: PageUp/PageDown or +/-
- Buttons: Arrow Left/Right, Enter

**Optional — Sonos artwork and metadata (in a second terminal):**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install soco pillow websockets aiohttp
cd services && python3 players/sonos.py
```

Set your Sonos speaker IP in `config/default.json` under `player.ip` before starting the service.

### Install on Raspberry Pi 5

Tested on [Raspberry Pi 5 8GB](https://www.raspberrypi.com/products/raspberry-pi-5/), but lower RAM versions should work fine.

1. Flash **Raspberry Pi OS Bookworm Lite (64-bit)** using [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Click the settings icon (gear) to enable SSH and set your username/password before writing.
2. Clone and run the installer:

```bash
git clone https://github.com/mkirsten/beosound5c.git ~/beosound5c
cd ~/beosound5c
sudo ./install.sh
```

The installer handles everything: packages, USB permissions, display config, service installation, configuration prompts, and optional BeoRemote One pairing. It will ask if you want to reboot when complete.

## Configuration

Configuration lives in two files on the device:

- **`/etc/beosound5c/config.json`** — all settings (device name, Sonos IP, menu, scenes, volume, transport)
- **`/etc/beosound5c/secrets.env`** — credentials only (HA token, MQTT password)

The installer creates both during setup. To reconfigure: edit `/etc/beosound5c/config.json`, then `sudo systemctl restart beo-*`.

### config.json

The installer creates this interactively during setup. Here's a minimal example:

```json
{
  "device": "Living Room",

  "menu": {
    "PLAYING": "playing",
    "SPOTIFY": "spotify",
    "SCENES": "scenes",
    "SYSTEM": "system"
  },

  "scenes": [
    { "id": "dinner", "name": "Dinner", "icon": "fork-knife", "color": "#fa0" },
    { "id": "all_off", "name": "All off", "icon": "power", "color": "#c55" }
  ],

  "player": { "type": "sonos", "ip": "192.168.1.100" },
  "bluetooth": { "remote_mac": "" },
  "home_assistant": {
    "url": "http://homeassistant.local:8123",
    "webhook_url": "http://homeassistant.local:8123/api/webhook/beosound5c"
  },
  "transport": { "mode": "mqtt", "mqtt_broker": "homeassistant.local" },
  "volume": {
    "type": "sonos",
    "host": "192.168.1.100",
    "max": 70,
    "step": 3,
    "output_name": "Sonos"
  },
  "spotify": { "client_id": "" }
}
```

For the full list of fields, volume adapter types, menu item options, and scene configuration, see the **[config schema](docs/config.schema.json)**.

### secrets.env

Credentials live separately in `/etc/beosound5c/secrets.env` (created by the installer). See [`config/secrets.env.example`](config/secrets.env.example) for the template.

## Services

| Service | File | Description |
|---------|------|-------------|
| `beo-input` | [`services/input.py`](services/input.py) | USB HID driver for BS5 rotary encoder, buttons, and laser pointer |
| `beo-router` | [`services/router.py`](services/router.py) | Event router: dispatches remote events to HA or the active source, controls volume |
| `beo-player-sonos` | [`services/players/sonos.py`](services/players/sonos.py) | Sonos player monitor: artwork, metadata, volume reporting |
| `beo-source-cd` | [`services/sources/cd.py`](services/sources/cd.py) | CD player: disc detection, MusicBrainz metadata, mpv playback |
| `beo-masterlink` | [`services/masterlink.py`](services/masterlink.py) | USB sniffer for B&O IR and MasterLink bus commands |
| `beo-bluetooth` | [`services/bluetooth.py`](services/bluetooth.py) | HID service for BeoRemote One wireless control |
| `beo-http` | — | Simple HTTP server for static files |
| `beo-ui` | [`services/ui.sh`](services/ui.sh) | Chromium in kiosk mode (1024×768) |

Service definitions: [`services/system/`](services/system/)

## Audio

Each BS5c is configured with one audio output. The installer asks you to choose during setup.

| Output | Requirements |
|---|---|
| Sonos | Any Sonos speaker (S1 or S2, any generation) |
| PowerLink | B&O PowerLink speakers + PC2/MasterLink USB interface |
| HDMI | Amplifier or device with HDMI audio input |
| Optical / Toslink | S/PDIF HAT (e.g. HiFiBerry Digi) |
| RCA | DAC HAT with RCA out |
| AirPlay | Any AirPlay-compatible speaker |

Sources (CD, USB, Spotify, Demo) register with the router and appear in the menu automatically. The remote's media keys are forwarded to whichever source is currently active.

For details on each output, playback modes, sources, and volume adapters, see **[Audio Setup Options](docs/audio-setup.md)**.

## Directory Structure

```
config/                     # Per-device configuration
├── default.json            #   Dev fallback
├── secrets.env.example     #   Credentials template
└── <device>.json           #   One per device (deployed to /etc/beosound5c/)
services/                   # Backend services
├── sources/                # Content providers (register with router)
│   ├── cd.py               #   CD player (beo-cd-source)
│   ├── spotify.py          #   Spotify source (beo-spotify)
│   └── usb.py              #   USB file playback (beo-usb-source)
├── players/                # External playback monitors
│   └── sonos.py            #   Sonos monitor (beo-sonos)
├── lib/
│   ├── volume_adapters/    # Pluggable volume output control
│   │   ├── beolab5.py      #   BeoLab 5 via controller REST API
│   │   ├── sonos.py        #   Sonos via SoCo
│   │   ├── hdmi.py         #   HDMI (ALSA software volume)
│   │   ├── spdif.py        #   S/PDIF / Optical (ALSA software volume)
│   │   ├── rca.py          #   RCA DAC (ALSA software volume)
│   │   └── powerlink.py    #   B&O PowerLink via masterlink.py
│   ├── transport.py        # HA communication (webhook/MQTT)
│   ├── config.py           # Shared JSON config loader
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

BeoSound 5c communicates with Home Assistant via **MQTT** (recommended) or **HTTP webhooks**. The transport is configured via `transport.mode` in `config.json`. The installer will prompt you to choose.

### MQTT Setup (recommended)

Requires an MQTT broker — the [Mosquitto add-on](https://github.com/home-assistant/addons/tree/master/mosquitto) works well. Create a user for the BS5c in the add-on config, then set `transport.mode` to `"mqtt"` in `config.json` with your broker hostname. MQTT credentials go in `secrets.env`.

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

## Development

### Repo Layout

```
config/
├── default.json          # Fallback for local development
├── <device>.json         # Per-device config (gitignored)
└── secrets.env.example   # Credentials template
```

For multi-device setups, create a JSON file per device in `config/` (e.g. `config/living-room.json`). The deploy script copies the matching config to `/etc/beosound5c/config.json` on the target.

### Deploying Updates

```bash
# Sync files and restart services
./deploy.sh                    # default: beo-http + beo-ui
./deploy.sh beo-player-sonos   # restart a specific service
./deploy.sh beo-*              # restart all beo services
./deploy.sh --no-restart       # sync files only

# Target a specific device
BEOSOUND5C_HOSTS="my-device.local" ./deploy.sh
```

## Acknowledgments

Arc geometry in `web/js/arcs.js` derived from [Beolyd5](https://github.com/larsbaunwall/Beolyd5) by Lars Baunwall (Apache 2.0).

This project is not affiliated with Bang & Olufsen. "Bang & Olufsen", "BeoSound", "BeoRemote", and "MasterLink" are trademarks of Bang & Olufsen A/S.
