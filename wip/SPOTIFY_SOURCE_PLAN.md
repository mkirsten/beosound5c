# Spotify Source: PKCE + Connect Receiver

## Context

Add a new **Spotify SOURCE** alongside the existing MUSIC view (replace MUSIC later once proven):
- Browses playlists identically to today's MUSIC view
- Fetches playlists via PKCE OAuth (no client_secret, ship our client_id)
- Runs a Spotify Connect receiver (librespot) so users can cast to the BS5c
- Controls work for both local browsing and received streams
- Prioritizes playing directly on Sonos (native Spotify Connect) for best UX

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  services/sources/spotify.py (port 8771)            │
│                                                     │
│  ┌───────────────┐  ┌────────────────────────────┐  │
│  │ Spotify Web   │  │ librespot/raspotify        │  │
│  │ API client    │  │ (Spotify Connect receiver) │  │
│  │               │  │                            │  │
│  │ • Playlists   │  │ • Receives cast streams    │  │
│  │ • Start play  │  │ • Audio → PipeWire → out   │  │
│  │ • Now playing │  │ • Zeroconf (no extra auth)  │  │
│  │ • PKCE auth   │  │                            │  │
│  └───────────────┘  └────────────────────────────┘  │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────┼────────────────┐
          ▼            ▼                ▼
     router.py    input.py (WS)    Player
     (controls)   (UI updates)     (Sonos or local)
```

## Audio Output & Control Matrix

| Output Config | Who plays audio? | Transport control | Volume control |
|---------------|-----------------|-------------------|----------------|
| **Sonos** | Sonos directly (Spotify Connect) | SoCo (play/pause/next/prev) | SoCo / SonosVolume adapter |
| **Powerlink** | librespot → PipeWire → local | Spotify Web API | Powerlink adapter |
| **HDMI/coax** | librespot → PipeWire → local | Spotify Web API | Custom adapter |
| **BeoLab 5** | Stacks on Sonos or local | Same as underlying | BL5 adapter (power + volume) |

**Key difference from CD source:** For Sonos output, we tell Sonos to play the Spotify content natively (no audio through Pi). For non-Sonos, librespot plays locally.

**Control rule:** When playing on Sonos, use SoCo for transport (play/pause/next/prev) — proven reliable, no extra latency. Use Spotify Web API only for starting playback on a specific context (playlist URI) and fetching metadata/playlists.

## Three Playback Paths

**Path A: User browses in BS5c → plays on Sonos (preferred)**
1. spotify.py starts playback via Spotify Web API: `PUT /v1/me/player/play` with Sonos device_id + context_uri
2. Sonos plays directly from Spotify cloud — best quality, lowest latency
3. Transport control: SoCo (same as today's Sonos integration)
4. Metadata: poll Web API for track info, artwork

**Path B: User browses in BS5c → plays on librespot (non-Sonos output)**
1. spotify.py starts playback via Web API targeting librespot device_id
2. Audio: librespot → PipeWire → configured output (powerlink/HDMI/coax)
3. Transport control: Spotify Web API
4. Metadata: poll Web API for track info, artwork

**Path C: User casts from phone → librespot receives**
1. Spotify app discovers librespot via zeroconf on local network
2. User taps "Play on BeoSound 5c"
3. Audio: librespot → PipeWire → configured output
4. spotify.py polls Web API → detects playback → updates BS5c UI
5. BS5c controls (BeoRemote, buttons) work via Web API

## Implementation Plan

### Phase 1: PKCE Auth + Playlist Fetch

**New: `tools/spotify/pkce.py`**
- `generate_code_verifier()` / `generate_code_challenge(verifier)`
- `exchange_code(code, client_id, code_verifier, redirect_uri)` → tokens
- `refresh_access_token(client_id, refresh_token)` → (access_token, new_refresh_token?)

**New: `tools/spotify/token_store.py`**
- Read/write `/etc/beosound5c/spotify_tokens.json` (atomic: temp + rename)
- Format: `{"client_id": "...", "refresh_token": "...", "updated_at": "..."}`
- Dev fallback path: `tools/spotify/spotify_tokens.json`

**Rewrite: `tools/spotify/setup_spotify.py`**
- Plain HTTP on `0.0.0.0:8888` (no SSL certs)
- Ship client_id in `config/default.json` under `spotify.client_id`
- Setup page: "Connect Spotify" button → opens Spotify auth with PKCE
- Redirect to `http://127.0.0.1:8888/callback` (loopback, user pastes URL back)
- Exchange code, save to `spotify_tokens.json`, fetch initial playlists
- Remove: SSL cert generation, client_secret field

**Modify: `tools/spotify/fetch_playlists.py`**
- Load tokens from `spotify_tokens.json` (fallback: env vars for backward compat)
- Refresh via PKCE (no client_secret in request)
- Persist rotated refresh_token immediately after each refresh

**Modify: `config/default.json`**
- Add `"spotify": {"client_id": "<your-app-client-id>"}`

**Modify: `config/secrets.env.example`**
- Remove `SPOTIFY_CLIENT_SECRET`
- Note: tokens now in `spotify_tokens.json`

### Phase 2: Spotify Source Service

**Rewrite: `services/sources/spotify.py` (port 8771)**

Follows the CD source pattern (`services/sources/cd.py`):
- aiohttp HTTP server on port 8771
- Registers as source with router via `POST /router/source`
- Registers menu item via `POST /input/menu` (preset: `spotify`)

**Endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/status` | Current state, now-playing, playlist info |
| POST | `/command` | play_playlist, play_track, toggle, next, prev, stop |
| GET | `/playlists` | All user playlists (cached from last fetch) |

**Playback target selection:**
- Read volume config: if `VOLUME_TYPE=sonos` → Sonos is the player
- For Sonos: use SoCo for transport, Web API only for starting context + metadata
- For local: use Web API to target librespot device, control via Web API
- Discover Spotify device IDs via `GET /v1/me/player/devices` on startup

**Transport control (Sonos path):**
- play/pause → `soco.play()` / `soco.pause()`
- next/prev → `soco.next()` / `soco.previous()`
- Starting a playlist/track: Web API `PUT /v1/me/player/play` with context_uri + Sonos device_id
- This mirrors existing Sonos control, proven reliable

**Transport control (librespot path):**
- All via Spotify Web API: `PUT /v1/me/player/play|pause`, `POST /v1/me/player/next|previous`

**Now-playing polling:**
- Poll `GET /v1/me/player/currently-playing` every 2-3s when active
- Broadcast updates via `POST http://localhost:8767/broadcast` (same as CD)
- Message type: `spotify_update` with track, artist, album, artwork, progress

**Playlist management:**
- On startup: load `playlists_with_tracks.json` into memory
- Trigger re-fetch via `/command` endpoint or on schedule
- Reuse existing `fetch_playlists.py` logic (import or call)

**Integration with router.py:**
- Source name: `"spotify"`
- Handles: play/pause/toggle, next, prev, stop
- Volume: routes to configured volume adapter (SoCo, BL5, etc. — same as other sources)

### Phase 3: Spotify Connect Receiver (librespot)

**Install raspotify** (lightest option for Pi, wraps librespot):
```bash
curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
```

**New: `services/system/beo-spotify-connect.service`**
- Runs librespot in zeroconf mode (no stored credentials needed)
- Device name: `"BeoSound 5c Church"` / `"BeoSound 5c Kitchen"` (from config)
- Audio backend: PulseAudio (→ PipeWire)
- Bitrate: 320kbps (Premium) / 160kbps (Free)

**Zeroconf mode:** librespot advertises on local network. When someone casts from Spotify app, the app pushes temporary credentials. No username/password stored on Pi.

**Add to `services/system/install-services.sh`:**
- Install raspotify package
- Configure device name from config.json
- Set PipeWire/PulseAudio as audio backend

### Phase 4: Web UI — Spotify Source View

**New: `web/sources/spotify/view.js`**

Two states (same pattern as CD view):

**A. Playlist browser** (default / when stopped):
- Identical to current MUSIC view: circular arc with playlist covers
- Uses same `ArcList` class from `softarc/script.js`
- Data source: `playlists_with_tracks.json` (same file, same format)
- GO on playlist → show tracks, GO on track → play via spotify.py

**B. Now playing** (when playback active):
- Album artwork, track title, artist, album
- Progress indicator
- Icon bar: shuffle, repeat, heart (save track)
- Nav wheel: show/hide icon bar

**Menu preset registration:**
```javascript
spotify: {
    item: { title: 'SPOTIFY', path: 'menu/spotify' },
    view: { ... },
    onMount() { SpotifyView.init(); },
    onRemove() { SpotifyView.destroy(); }
}
```

**Config-driven menu:** Add `"SPOTIFY": "spotify"` to config menu (keep MUSIC for now). Replace MUSIC with SPOTIFY later once proven.

## Files Summary

| File | Action | Phase |
|------|--------|-------|
| `tools/spotify/pkce.py` | NEW | 1 |
| `tools/spotify/token_store.py` | NEW | 1 |
| `tools/spotify/setup_spotify.py` | REWRITE | 1 |
| `tools/spotify/fetch_playlists.py` | MODIFY | 1 |
| `config/default.json` | MODIFY (add spotify.client_id) | 1 |
| `config/secrets.env.example` | MODIFY (remove client_secret) | 1 |
| `services/sources/spotify.py` | REWRITE (from stub) | 2 |
| `services/router.py` | MODIFY (add spotify source handles) | 2 |
| `services/system/beo-spotify-connect.service` | NEW | 3 |
| `services/system/install-services.sh` | MODIFY (add raspotify) | 3 |
| `web/sources/spotify/view.js` | NEW | 4 |
| `web/js/hardware-input.js` | MODIFY (spotify event routing) | 4 |
| `web/index.html` | MODIFY (load spotify view.js) | 4 |

## Auth Summary

| What | Method | Credentials |
|------|--------|-------------|
| Web API (playlists, start playback, metadata) | PKCE OAuth | Shipped client_id + user's refresh_token |
| librespot (Connect receiver) | Zeroconf | None stored — Spotify app pushes on first cast |

Users need Spotify Premium for Connect playback. Playlist browsing works with Free tier.

## Verification

1. **Phase 1:** Run `setup_spotify.py`, connect account, verify `spotify_tokens.json` created. Run `fetch_playlists.py`, verify playlists fetched without client_secret.
2. **Phase 2:** Start spotify.py, verify it registers with router, responds to play/next/prev. Verify Sonos plays Spotify content directly (no audio through Pi). Verify SoCo controls work.
3. **Phase 3:** Install raspotify on Pi, verify device appears in Spotify app. Cast a song, verify audio output.
4. **Phase 4:** Open BS5c UI, navigate to SPOTIFY, verify playlist browsing. Select a track, verify playback. MUSIC view still works alongside.
