# Web-Based Setup Interface for BeoSound 5c

> **Status: implemented.** The CLI wizard (`install.sh configure` and `install/configure/`) has been removed. `install.sh` handles OS/system setup only; after reboot the device shows a QR code and all configuration happens in the web UI at `http://<device-ip>/config`. This document is kept as design reference — file paths under `install/configure/` no longer exist.

## Context

The BS5c originally required SSH + CLI wizard (`install.sh configure`) to set up and reconfigure. This was friction for initial setup and ongoing changes. A web-based setup page lets users configure from a phone or laptop by pointing a browser at the BS5c's IP — both for first-time setup and later changes (source visibility, button mappings, HA connection, etc.).

## Architecture

### New service: `beo-setup` (port 8780)

A standalone aiohttp service, following the same patterns as `router.py`. Separate from `http_server.py` (which is a 28-line static file server) and `router.py` (which is already 1270+ lines of event routing).

**Why separate service:**
- Needs write access to `/etc/beosound5c/config.json` (root 644) and `secrets.env` (root 600)
- Needs to restart systemd services
- Self-contained: serves its own frontend, has its own API
- Follows existing pattern (Spotify source serves its own `/setup` on port 8771)

**Permissions:** Sudoers drop-in grants the service user passwordless access to specific commands only:
```
kirsten ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/beosound5c/config.json
kirsten ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/beosound5c/secrets.env
kirsten ALL=(ALL) NOPASSWD: /bin/chmod 600 /etc/beosound5c/secrets.env
kirsten ALL=(ALL) NOPASSWD: /bin/systemctl restart beo-*
kirsten ALL=(ALL) NOPASSWD: /bin/systemctl stop beo-*
kirsten ALL=(ALL) NOPASSWD: /bin/systemctl start beo-*
kirsten ALL=(ALL) NOPASSWD: /bin/systemctl is-active beo-*
```

**Security:** Local network appliance, same threat model as the BS5c display itself. No auth needed.

## Files to Create

| File | Purpose |
|------|---------|
| `services/setup.py` | Backend: aiohttp on port 8780, config CRUD, validation, discovery, service control |
| `services/lib/discovery.py` | Async network discovery (Sonos via port 1400, BlueSound via 11000, HA via 8123) |
| `web/setup/index.html` | Frontend: self-contained SPA (HTML+CSS+JS, no build step) |
| `web/setup-required.html` | BS5c display page shown during first-time setup (QR code + IP) |
| `services/system/beo-setup.service` | systemd unit (same pattern as `beo-router.service`) |

## Files to Modify

| File | Change |
|------|--------|
| `install/install.sh` | Add sudoers drop-in installation step; `configure` step now prints URL to web setup instead of running CLI wizard |
| `install/lib/service-registry.sh` | Register `beo-setup` service |

## Backend API (`services/setup.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/setup/` | Serve frontend HTML |
| `GET` | `/setup/config` | Return current config.json |
| `PUT` | `/setup/config` | Validate + save config.json (via `sudo tee`) |
| `GET` | `/setup/secrets` | Return secret keys with masked values |
| `PUT` | `/setup/secrets` | Write secrets.env (via `sudo tee` + `chmod 600`) |
| `POST` | `/setup/validate` | Validate config without saving |
| `POST` | `/setup/restart` | Restart specified services, return status |
| `GET` | `/setup/services` | Return `systemctl is-active` for all beo-* |
| `GET` | `/setup/discover/sonos` | Scan subnet for Sonos speakers |
| `GET` | `/setup/discover/bluesound` | Scan subnet for BlueSound players |
| `GET` | `/setup/discover/ha` | Scan subnet for Home Assistant |
| `POST` | `/setup/test/mqtt` | Test MQTT broker connectivity |
| `POST` | `/setup/test/ha` | Test HA URL + token validity |

**Config save flow:**
1. Frontend sends `PUT /setup/config` with full JSON
2. Backend validates (required fields, known types, no conflicting button mappings)
3. Writes via `sudo tee`, returns `{saved: true, changed_sections: [...]}`
4. Frontend shows which services need restart, user clicks restart button
5. Backend restarts only affected services (smart diff)

**Smart restart mapping:**

| Changed section | Services to restart |
|----------------|-------------------|
| `player` | `beo-player-*`, `beo-router` |
| `volume` | `beo-router` |
| `transport` | `beo-router`, `beo-bluetooth`, `beo-masterlink` |
| `bluetooth` | `beo-bluetooth` |
| `menu` | `beo-router` + affected source services |
| `home_assistant` | `beo-router` |
| `spotify`/`plex`/etc. | respective `beo-source-*` |

## Frontend (`web/setup/index.html`)

Single self-contained HTML file with embedded CSS+JS. Responsive (works on phone 375px+ to desktop). Light theme, clean cards — this is an admin tool, not the BS5c kiosk UI.

### Layout: Tabbed sections

Each tab independently editable (not a forced wizard flow). Status bar at top shows device name + connection indicators + "unsaved changes" badge.

**Tabs:**

1. **Device** — Name input, status overview
2. **Player** — Type selector (sonos/bluesound/local), IP input with "Discover" button, test connection
3. **Home Assistant** — URL with auto-discover, webhook URL, token input (masked), test button, **one-click blueprint import link** (opens HA with pre-filled blueprint URL that installs the BS5c webhook automation)
4. **Transport** — Mode selector (webhook/mqtt/both), MQTT broker/port/credentials when relevant, test button
5. **Audio Output** — Volume adapter type (context-sensitive options based on player type, matching `audio.sh` logic), per-type fields (host, zone, input, mixer_port), max volume slider, output name
6. **Sources & Menu** — Toggle switches for each source, drag-to-reorder, expandable source-specific config (Spotify client_id, Plex URL, News API key, Showing entity_id, USB mounts, CD device path, button mapping dropdown per source)
7. **Remote** — BeoRemote MAC (manual entry), default_source, handle_all toggle
8. **Scenes** — Add/remove/reorder scenes, name/id/icon/color per scene, nested sub-scenes
9. **Services** — Live service status with restart buttons (lightweight version of system.html services tab)

### First-time setup (replaces CLI wizard)

After `install.sh` runs system setup + installs services, `beo-setup` starts automatically. The BS5c display shows a "Setup required" screen with its IP address and QR code pointing to `http://<ip>:8780/setup/`. User scans QR or types URL on phone/laptop.

When `device` is empty/default, the page shows a guided flow:
1. **Device name** (required)
2. **Player** — type + IP with auto-discovery
3. **Home Assistant** — URL with auto-discover, token, blueprint import link
4. **Transport** — webhook/mqtt/both
5. **Audio output** — volume adapter
6. **"Save and start"** button — saves config, restarts all services

After first-time setup completes, the guided flow dismisses to the full tabbed interface for fine-tuning (menu order, scenes, sources, etc.).

### HA Blueprint Import

The HA tab includes a link: "Install BS5c automation in Home Assistant". This opens:
`http://<ha_url>/config/blueprint/import?url=https://beosound5c.com/blueprints/beosound5c-events.yaml`

The blueprint defines a simple webhook automation that receives BS5c events and logs them (or triggers user-defined actions). We host the blueprint YAML on beosound5c.com (the public site). The link is dynamically constructed using the HA URL the user entered.

### Discovery UX

"Discover" buttons next to IP fields. Click → spinner → dropdown of found devices with name+IP. Uses `/setup/discover/*` endpoints. Async subnet scanning in `lib/discovery.py`.

### QR / Setup screen on BS5c display

During first-time setup (no valid config), `beo-ui` shows a simple page (`web/setup-required.html`) with:
- "BeoSound 5c" heading
- "Open setup on your phone or computer:"
- QR code (generated via JS, encoding `http://<device-ip>:8780/setup/`)
- IP address in large text as fallback
- Auto-refreshes: once setup is complete and services restart, this page redirects to the normal UI

## Implementation Phases

### Phase 1: Backend + minimal frontend
1. `services/setup.py` — config read/write/validate, service status/restart
2. `services/system/beo-setup.service`
3. `web/setup/index.html` — skeleton with Device + Player + HA tabs
4. Test on office device

### Phase 2: Full frontend
1. All remaining tabs (Transport, Audio, Sources, Remote, Scenes, Services)
2. Audio output tab with context-sensitive fields (replicating `audio.sh` logic)
3. Menu editor with drag-to-reorder

### Phase 3: Discovery + testing
1. `services/lib/discovery.py` — Sonos/BlueSound/HA subnet scanning
2. Test connection buttons (MQTT, HA)
3. First-time setup overlay

### Phase 4: Install integration
1. Sudoers drop-in in `install.sh`
2. Register in `service-registry.sh`
3. Deploy + test on all devices

## Key Reference Files

- `services/router.py` — aiohttp service pattern, CORS middleware
- `services/lib/config.py` — config loading, validation, `reload_config()`
- `install/configure/audio.sh` — context-sensitive volume type selection logic
- `install/configure/menu.sh` — menu item selection logic
- `install/lib/config-utils.sh` — config/secrets read/write patterns
- `config/default.json` — full config template
- `docs/config.schema.json` — JSON schema for validation

## Verification

1. Start `beo-setup` locally in dev mode (reads `config/default.json`)
2. Open `http://localhost:8780/setup/` in browser
3. Edit config values, save → verify config.json updated correctly
4. Test discovery endpoints on network with Sonos speakers
5. Deploy to office device, verify full flow: edit config → save → restart services → services come up with new config
6. Test on phone (responsive layout)
