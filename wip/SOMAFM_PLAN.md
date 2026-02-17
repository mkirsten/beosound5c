# SomaFM Radio Service — Implementation Plan

## Context

Add a SomaFM internet radio browsing and playback service to the BeoSound 5c, modeled after the CD player architecture. SomaFM provides ~61 curated internet radio channels with a simple JSON API (no auth needed). Playback goes directly to Sonos via SoCo `play_uri()`. The menu item is always visible (permanent), added by the service on startup using the same menu-preset pattern as CD. Next/prev cycles through a configurable favorites list.

## API Research

### SomaFM API (no auth, free)
- **Channel list:** `GET https://api.somafm.com/channels.json` — returns `{"channels": [...]}` with ~61 channels
- **Song history:** `GET https://somafm.com/songs/{channel_id}.json` — 16 most recent tracks per channel
- **Direct stream URLs:** `https://ice1.somafm.com/{id}-128-mp3` (MP3 128kbps, best for Sonos)
- **Artwork:** 3 sizes per channel: `image` (120px), `largeimage` (256px), `xlimage` (512px)
- **Now playing:** `lastPlaying` field in channel list, or `songs/{id}.json` for full history
- Mirrors: `ice1`, `ice4`, `ice5` (interchangeable)

### Channel JSON structure
```json
{
  "id": "groovesalad",
  "title": "Groove Salad",
  "description": "A nicely chilled plate of...",
  "genre": "ambient|electronica",
  "dj": "Rusty Hodge",
  "image": "https://api.somafm.com/img/groovesalad120.png",
  "largeimage": "https://api.somafm.com/logos/256/groovesalad256.png",
  "xlimage": "https://api.somafm.com/logos/512/groovesalad512.png",
  "listeners": "143",
  "lastPlaying": "Artist - Track Title",
  "playlists": [
    {"url": "https://api.somafm.com/groovesalad.pls", "format": "mp3", "quality": "highest"},
    ...
  ]
}
```

### Sonos playback via SoCo
```python
soco.SoCo('192.168.1.135').play_uri(
    'https://ice1.somafm.com/groovesalad-128-mp3',
    title='Groove Salad',
    force_radio=True  # converts to x-rincon-mp3radio:// prefix
)
```

### Other services considered (for future)

| Service | Auth | Cost | Best for |
|---|---|---|---|
| **Tidal** | OAuth 2.1 (free dev tier) | Free | On-demand music catalog (albums/tracks) |
| **RadioBrowser** | None | Free | 40K+ radio stations, open database |
| **Apple Music** | JWT + $99/yr developer fee | $99/yr | Skip — too expensive for side project |
| **TuneIn** | None (undocumented API) | Free | Skip — fragile, use Sonos native instead |

---

## Files to Create

### 1. `services/somafm.py` (port 8771) — Backend service

Follows `cd.py` pattern: aiohttp HTTP server, communicates outward via HTTP POST to input.py (8767) and router.py (8770).

**Startup flow:**
- Fetch channel list from `https://api.somafm.com/channels.json`, cache in memory
- Load favorites from `web/json/somafm.json`
- Connect to Sonos via SoCo using `SONOS_IP` from config.env
- Send `add_menu_item` (preset=`radio`) to input.py → appears in arc menu
- Check if Sonos is already playing a SomaFM stream → restore state if so

**HTTP endpoints:**
| Method | Path | Purpose |
|---|---|---|
| GET | `/status` | Current state: playing/stopped, channel, track info, favorites |
| POST | `/command` | Commands: `play_channel`, `stop`, `toggle`, `next`, `prev`, `toggle_favorite` |
| GET | `/channels` | Full channel list (cached from API) |
| GET | `/resync` | Re-send menu item + state to input.py (for late-joining WS clients) |

**Playback:**
- `play_channel(channel_id)`: call `soco.play_uri(stream_url, title=channel_title, force_radio=True)`, set router source to `somafm`, broadcast `somafm_update`
- `stop`: call `soco.stop()`, set router source to `sonos`, broadcast `somafm_update`
- `toggle`: if playing → stop, if stopped → play last channel (or first favorite)
- `next/prev`: cycle through favorites list, wrapping around

**Now-playing polling:**
- When a channel is playing, poll `https://somafm.com/songs/{channel_id}.json` every ~30s
- On track change, broadcast `somafm_update` via input.py
- The `somafm_update` broadcast includes: channel info (title, description, genre, artwork) + current track (artist, title, album)

**Broadcast message format** (via input.py `broadcast` command):
```json
{
  "type": "somafm_update",
  "data": {
    "state": "playing",
    "channel_id": "groovesalad",
    "channel_title": "Groove Salad",
    "channel_description": "A nicely chilled plate of...",
    "genre": "ambient|electronica",
    "artwork": "https://api.somafm.com/logos/512/groovesalad512.png",
    "track_title": "Unending Fields",
    "track_artist": "Real meets Unreal",
    "listeners": "143",
    "favorites": ["groovesalad", "dronezone"],
    "is_favorite": true
  }
}
```

**Graceful shutdown:** Send `remove_menu_item` to input.py on SIGTERM.

### 2. `web/json/somafm.json` — Favorites config

```json
{
  "favorites": ["groovesalad", "dronezone", "spacestation", "deepspaceone", "lush", "thistle", "dubstep", "defcon"]
}
```

Deployed to both devices. Editable — the `toggle_favorite` command updates this file.

### 3. `web/js/somafm-view.js` — Frontend controller

IIFE pattern matching `cd-view.js`. Exposes `window.SomaFMView`:

```javascript
window.SomaFMView = (() => {
    return {
        init,            // called by menu-presets.js onMount
        destroy,         // called on onRemove
        handleNavEvent,  // from cursor-handler.js
        handleButton,    // from cursor-handler.js
        updateMetadata,  // from cursor-handler.js on somafm_update
        sendCommand,     // HTTP POST to somafm.py
        get isActive() { return initialized; }
    };
})();
```

**Two UI states:**

**A. Channel browser** (default when entering view, or when stopped):
- Scrollable list of channels with: channel name, genre, listener count
- Favorites shown first (starred), then all channels
- Nav wheel scrolls the list, GO plays the highlighted channel
- Visual style: reuse `.cd-track-list` / `.cd-track-item` patterns

**B. Now playing** (after selecting a channel):
- Channel artwork (512px `xlimage`), channel title, genre
- Current track: artist + title (updated via `somafm_update` broadcasts)
- Nav wheel: show/hide icon bar (same pattern as CD)
- Icon bar icons: Star (toggle favorite), List (back to channel browser), Stop

**Communication:** Direct HTTP fetch to `http://localhost:8771` (same pattern as CD's fetch to 8769).

### 4. `services/system/beo-somafm.service` — Systemd unit

```ini
[Unit]
Description=BeoSound5c SomaFM Radio Service
After=network-online.target beo-input.service
Wants=network-online.target

[Service]
Type=simple
User=kirsten
Group=kirsten
WorkingDirectory=/home/kirsten/beosound5c/services
EnvironmentFile=-/etc/beosound5c/config.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /home/kirsten/beosound5c/services/somafm.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Files to Modify

### 5. `web/js/menu-presets.js` — Add `radio` preset

Add alongside the existing `cd` preset:

```javascript
radio: {
    item: { title: 'RADIO', path: 'menu/radio' },
    after: 'menu/music',
    view: {
        title: 'RADIO',
        content: `<div id="somafm-view" class="media-view">
            <div id="somafm-channels" class="somafm-channel-list">...</div>
            <div id="somafm-playing" class="somafm-playing-state somafm-hidden">
                <div class="media-view-artwork">
                    <img id="somafm-artwork" ...>
                </div>
                <div class="media-view-info">
                    <div id="somafm-channel-title" class="media-view-title">--</div>
                    <div id="somafm-track" class="media-view-artist">--</div>
                    <div id="somafm-genre" class="media-view-album">--</div>
                </div>
            </div>
            <div id="somafm-icon-bar" class="cd-icon-bar somafm-hidden">...</div>
        </div>`
    },
    onAdd() { },
    onMount() { if (window.SomaFMView) window.SomaFMView.init(); },
    onRemove() { if (window.SomaFMView) window.SomaFMView.destroy(); }
}
```

### 6. `web/js/cursor-handler.js` — Route events to SomaFMView

**In `processWebSocketEvent()`** — add case:
```javascript
case 'somafm_update':
    if (window.SomaFMView) window.SomaFMView.updateMetadata(data);
    break;
```

**In `handleNavEvent()`** — add before iframe check:
```javascript
if (currentPage === 'menu/radio' && window.SomaFMView?.isActive) {
    if (window.SomaFMView.handleNavEvent(data)) return;
}
```

**In `handleButtonEvent()`** — add before iframe check:
```javascript
if (currentPage === 'menu/radio' && window.SomaFMView?.isActive) {
    if (window.SomaFMView.handleButton(data.button.toLowerCase())) return;
}
if (currentPage === 'menu/radio') return; // don't send webhooks from radio view
```

### 7. `services/router.py` — Add `somafm` source

- Add `"somafm"` as valid source value alongside `"cd"` and `"sonos"`
- Add `SOMAFM_ACTION_MAP` (same as CD: play→toggle, go→toggle, next→next, prev→prev, stop→stop, left→prev, right→next)
- Add routing: when `active_source == "somafm"` and action in MEDIA_KEYS → forward to `http://localhost:8771/command`
- Add startup sync: check somafm.py status on boot, restore source if playing

### 8. `web/index.html` — Load somafm-view.js

Add `<script src="js/somafm-view.js"></script>` after cd-view.js.

### 9. `web/js/config.js` — Add service URL

Add `somafmServiceUrl: 'http://localhost:8771'`.

### 10. `web/styles.css` — Add SomaFM-specific styles

Reuse `.media-view`, `.media-view-artwork`, `.media-view-info`, `.media-view-title/artist/album` (shared with CD and PLAYING).

Add minimal SomaFM-specific classes:
- `.somafm-channel-list` — scrollable channel list container
- `.somafm-channel-item` — individual channel row (name + genre + listeners)
- `.somafm-channel-selected` — highlight for selected channel
- `.somafm-hidden` — display:none toggle
- `.somafm-favorite-marker` — star/indicator for favorites

### 11. `services/system/install-services.sh` — Add beo-somafm

Add `beo-somafm.service` to the `SERVICES` array. Add `pip3 install soco` to dependencies (if not already present for media.py).

## Architecture Diagram

```
SomaFM API ──fetch──> somafm.py (8771) ──soco.play_uri──> Sonos speaker
                           │
                           ├── POST /webhook ──> input.py (8767) ──WS──> browser
                           │     (broadcast somafm_update)
                           │     (add/remove_menu_item)
                           │
                           └── POST /router/source ──> router.py (8770)
                                                          │
                          bluetooth.py / masterlink.py ───┘
                          (media keys routed to somafm.py when source=somafm)
```

## Implementation Order

1. **somafm.py** — core service with channel fetch, SoCo playback, now-playing polling
2. **somafm.json** — favorites config
3. **router.py** — add somafm source routing
4. **menu-presets.js** — add radio preset HTML template
5. **somafm-view.js** — frontend controller (channel browser + now playing)
6. **cursor-handler.js** — wire up event routing
7. **styles.css** — channel list styling
8. **config.js, index.html** — load new JS
9. **beo-somafm.service + install-services.sh** — systemd integration
10. Test locally in dev mode (browser), then deploy to both devices

## Verification

1. **Local dev testing:** Start `python3 services/somafm.py` + web server. Open browser, verify RADIO appears in menu, channel list loads, selecting a channel plays on Sonos.
2. **Device testing:** Deploy to both devices, restart services. Verify menu item appears, channel browsing works with physical nav wheel, GO plays, next/prev cycles favorites, BeoRemote media keys route correctly.
3. **Edge cases:** Service restart (menu item re-adds), network failure (channel fetch retry), Sonos unavailable (graceful error).
