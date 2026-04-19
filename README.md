# BeoSound 5c

A modern recreation of the Bang & Olufsen BeoSound 5 experience using web technologies and a Raspberry Pi 5.

**Website: [www.beosound5c.com](https://www.beosound5c.com)**

This project replaces the original BeoSound 5 software with a circular arc-based touch UI that integrates with Sonos players, music services (Spotify, Apple Music, TIDAL, Plex), and Home Assistant. It works with the original BS5 hardware (rotary encoder, laser pointer, display) and supports BeoRemote One for wireless control.

## Quick Start

You want a [Raspberry Pi 5 4GB](https://www.raspberrypi.com/products/raspberry-pi-5/)

### Fresh Install

1. Flash **Raspberry Pi OS Bookworm Lite (64-bit)** using [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Click the settings icon (gear) to enable SSH and set your username/password before writing.
2. Clone and run the installer:

```bash
git clone https://github.com/mkirsten/beosound5c.git ~/beosound5c
cd ~/beosound5c
sudo ./install/install.sh
```

The installer handles everything: packages, display config, service installation. It will prompt for a reboot when complete.

3. After rebooting, open the config UI to set up your player, Home Assistant, and sources:

```
http://<device-ip>/config
```

### Updating

```bash
git pull && sudo ./install/install.sh update
```

Updates service files, sudoers, and Python packages. No reboot needed unless system packages changed.

## Remote Support

If you need help troubleshooting, you can open a temporary remote support session. The installer pre-installs [Tailscale](https://tailscale.com/) (disabled by default — no background services run until you start a session).

```bash
bs5c-support          # Start session — prompts for an access key
bs5c-support stop     # End session and disconnect
bs5c-support status   # Check if a session is active
```

Ask the developer for an access key, paste it when prompted, and share the displayed Tailscale IP. When you're done, `bs5c-support stop` disconnects and stops the Tailscale daemon.

## Configuration

After install, open `http://<device-ip>/config` in a browser. The configuration UI lets you set the device name, player, volume adapter, Home Assistant connection, transport, and all sources. Changes are saved and services restart automatically.

Configuration lives in two files on the device:

- **`/etc/beosound5c/config.json`** — all settings (device name, player IP, menu, scenes, volume, transport)
- **`/etc/beosound5c/secrets.env`** — credentials only (HA token, MQTT password)

For the full list of fields and options, see the **[config schema](docs/config.schema.json)**.

To edit scenes (names, icons, HA scripts), edit `/etc/beosound5c/config.json` directly — the `"scenes"` array.

## Telemetry

Honestly, I just find it delightful to see where BeoSound 5cs are showing up in the world. There are already installations in the US, across Europe, here in Stockholm, in Asia, and in Australia — and every time a new one appears on the map it makes my day.

To make that possible, each BS5c sends a small anonymous ping to `beosound5c.com` on startup. Your public IP is used to infer a country (via Cloudflare — never stored beyond the country name). No hostname, device name, MAC address, or credentials are ever sent. Feel free to read exactly what gets posted in [`services/lib/beacon.py`](services/lib/beacon.py).

| Field | Value |
|---|---|
| `device_id` | Random UUID generated at install time — not linked to any personal identifier |
| `version` | Software version string |
| `sources` | Names of enabled sources (e.g. `spotify`, `cd`) — no credentials or config values |
| `player_type` | Player backend: `sonos`, `bluesound`, or `local` |
| `volume_type` | Volume adapter type: `sonos`, `beolab5`, `powerlink`, etc. |

If you'd rather opt out, just create a `NO_TELEMETRY` file in the repo root:

```bash
touch ~/beosound5c/NO_TELEMETRY
```

## Documentation

- [Audio, players & sources](docs/audio-setup.md) — player types, source compatibility, Spotify setup, volume adapters
- [Home Assistant integration](docs/home-assistant.md) — MQTT, webhooks, automation examples
- [Remotes & IR](docs/remotes.md) — BeoRemote One pairing, IR source buttons, Beo6
- [Development & contributing](docs/CONTRIBUTING.md) — local dev setup, repo layout, deploy script

## Acknowledgments

Arc geometry in `web/js/arcs.js` derived from [Beolyd5](https://github.com/larsbaunwall/Beolyd5) by Lars Baunwall (Apache 2.0).

This project is not affiliated with Bang & Olufsen. "Bang & Olufsen", "BeoSound", "BeoRemote", and "MasterLink" are trademarks of Bang & Olufsen A/S.
