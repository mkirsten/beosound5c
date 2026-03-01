# Audio Setup Options

Each BeoSound 5c is configured with a **player** (how audio is played) and a **volume adapter** (how volume is controlled). The installer asks you to choose during setup, or you can change it later in `config.json`.

## Which setup is right for me?

- **Sonos speakers?** Use Sonos as your player. The Sonos speaker handles playback natively — the BS5c sends commands and monitors what's playing but does not produce audio itself. All streaming sources (Spotify, Apple Music, TIDAL, Plex) work. Set `player.type` to `"sonos"` and `volume.type` to `"sonos"`.

- **BluOS player?** Use BlueSound as your player. Plex, CD, and USB work. Spotify, Apple Music, and TIDAL do not — they send share links that only Sonos handles via ShareLink. Set `player.type` to `"bluesound"` and `volume.type` to `"bluesound"`.

- **B&O PowerLink speakers?** Use PowerLink for volume. Local sources (CD, USB) play on the Pi and output to PowerLink speakers via the MasterLink bus. Streaming sources need a Sonos or BlueSound player. Set `volume.type` to `"powerlink"`.

- **Other speakers or amplifier?** Connect via HDMI, optical/Toslink, or RCA (with the appropriate HAT). Local sources play directly. Streaming sources need a Sonos or BlueSound player. Pick whichever output matches your cable.

## Player Types

The player service handles network-based playback. Sources send play commands to the player, which talks to the actual speaker.

| Player | Capabilities | How It Plays |
|---|---|---|
| Sonos | `spotify`, `url_stream` | ShareLink (Spotify, Apple Music, TIDAL) or `play_uri` (URLs) |
| BlueSound | `url_stream` | BluOS HTTP API with stream URLs |

Only one player is active — determined by `player.type` in config.json. The type guard in PlayerBase ensures only the matching player service starts.

## Source Compatibility

Sources check the player's capabilities at startup to determine how to play content.

| Source | Sonos | BlueSound | No Player |
|---|---|---|---|
| **Spotify** | Yes — ShareLink queues Spotify URIs natively | No | No |
| **Apple Music** | Yes — ShareLink handles Apple Music share URLs | No | No |
| **TIDAL** | Yes — ShareLink handles TIDAL share URLs | Yes — direct stream URLs | No |
| **Plex** | Yes — `play_uri` with direct stream URLs | Yes — direct stream URLs | No |
| **CD** | Yes — plays on Pi via mpv | Yes — plays on Pi via mpv | Yes |
| **USB** | Yes — streams track URLs to Sonos | Yes — streams track URLs | Yes — falls back to local mpv |

**Key points:**
- Spotify and Apple Music send share links via the `uri` parameter. Only Sonos handles these (via its ShareLink plugin). BlueSound ignores `uri` — it only supports direct stream URLs via `url`.
- TIDAL works with both players: on Sonos it uses ShareLink (player manages queue); on BlueSound it resolves direct stream URLs via tidalapi and manages its own queue (like Plex)
- Plex works with both players because it sends direct stream URLs (via `url`), not share links
- Plex and TIDAL (on BlueSound) manage their own queues (next/prev build new stream URLs) while Spotify and Apple Music let the player handle queue advancement after the initial share link is queued
- CD always plays locally via mpv — it doesn't use the player service
- USB auto-detects: if the player supports `url_stream`, it streams track URLs to the player; otherwise falls back to local mpv

### Sonos

The Sonos speaker handles playback natively. The BS5c sends commands and monitors what's playing (track info, artwork, volume) but does not produce the audio itself. Works with any Sonos speaker — S1 or S2, any generation.

**Config:**
```json
"player": { "type": "sonos", "ip": "192.168.1.100" },
"volume": { "type": "sonos", "host": "192.168.1.100", "max": 70 }
```

### BlueSound

The BluOS player handles playback via its HTTP/XML API. The BS5c sends commands and monitors playback via long-polling. Works with any BluOS device (Node, PowerNode, Vault, etc.).

**Config:**
```json
"player": { "type": "bluesound", "ip": "192.168.1.100" },
"volume": { "type": "bluesound", "host": "192.168.1.100", "max": 70 }
```

### PowerLink

Uses the original B&O PowerLink bus via a PC2/MasterLink USB interface. The BS5c sends volume and power commands through `masterlink.py`, which controls the speakers over the bus. Works with any B&O PowerLink speaker (BeoLab 6000, BeoLab 8000, etc.) or a BeoLink Passive with passive speakers.

**Config:**
```json
"volume": { "type": "powerlink", "max": 70 }
```

### HDMI

Uses the Pi's second micro-HDMI port (HDMI1) as a digital audio output. HDMI0 drives the BS5 display. Volume is controlled via ALSA software mixer (`amixer`). Connect to an amplifier, soundbar, or any device with HDMI audio input.

**Config:**
```json
"volume": { "type": "hdmi", "max": 70 }
```

### Optical / Toslink (S/PDIF)

Requires an S/PDIF HAT such as the HiFiBerry Digi or InnoMaker Digi One. Outputs bit-perfect digital audio via coaxial RCA or optical TOSLINK. Volume is controlled via ALSA software mixer.

**Setup:**
1. Add `dtoverlay=hifiberry-digi` to `/boot/firmware/config.txt`
2. Reboot and verify with `aplay -l`

**Config:**
```json
"volume": { "type": "spdif", "max": 70 }
```

### RCA

Requires a DAC HAT with RCA analog output (e.g. HiFiBerry DAC+, IQaudIO DAC). Volume is controlled via ALSA software mixer.

**Setup:**
1. Add the appropriate dtoverlay to `/boot/firmware/config.txt` (e.g. `dtoverlay=hifiberry-dacplus`)
2. Reboot and verify with `aplay -l`

**Config:**
```json
"volume": { "type": "rca", "max": 70 }
```

### BeoLab 5 (via BeoLab 5 Controller)

A custom option for controlling a pair of BeoLab 5 speakers via their sync port. Requires the BeoLab 5 Controller — a dedicated ESP32 board that sends serial commands to both speakers.

**Config:**
```json
"volume": { "type": "beolab5", "host": "beolab5-controller.local", "max": 70 }
```

## How Playback Works

There are two playback paths depending on the source:

**Remote playback** — The source sends a play command to the player service (port 8766), which forwards it to the Sonos or BlueSound speaker. The speaker fetches and plays the audio. This is how Spotify, Apple Music, TIDAL, and Plex work. USB also uses this path when the player supports `url_stream`.

**Local playback** — The source plays audio directly on the Pi using mpv. For wired outputs (PowerLink, HDMI, Optical, RCA) audio goes directly to the hardware. CD always plays locally. USB falls back to this mode when no player with `url_stream` is available.

## Sources

Sources provide content to the BS5c. Each source registers with the router and appears in the menu. The remote's media keys (play, pause, next, prev) are forwarded to whichever source is currently active.

| Source | Playback Method | Queue Management |
|---|---|---|
| Spotify | Sends Spotify share URLs to player via `player_play(uri=...)`. Sonos uses ShareLink to queue natively. Sonos only. | Player manages queue |
| Apple Music | Sends Apple Music share URLs to player via `player_play(uri=...)`. Sonos uses patched ShareLink. Sonos only. | Player manages queue |
| TIDAL | Sonos: sends TIDAL share URLs via `player_play(uri=...)` (ShareLink). BlueSound: resolves direct stream URLs via tidalapi `track.get_url()`, sends via `player_play(url=...)`. | Sonos: player manages queue. BlueSound: source manages queue (next/prev play new stream URLs) |
| Plex | Builds direct stream URLs from Plex server. Sends to player via `player_play(url=...)`. Works with Sonos and BlueSound. | Source manages queue (next/prev build new URLs) |
| CD | Local mpv playback from USB CD/DVD drive. Metadata from MusicBrainz. No player service needed. | Source manages tracks (mpv chapters) |
| USB | Auto-detects: streams track URLs to player if `url_stream` available, otherwise local mpv. Supports BeoMaster 5 library databases and plain USB drives. Works with both players or standalone. | Source manages queue |

## Volume Adapters

The router sends volume commands through whichever adapter matches the configured output. Each adapter handles debouncing and power management independently.

| Adapter | Debounce | Power On/Off | Balance | Host Required |
|---|---|---|---|---|
| `sonos` | 50ms | No | No | `player.ip` (default) |
| `bluesound` | 50ms | No | No | `player.ip` (default) |
| `beolab5` | 100ms | Yes | Yes | `beolab5-controller.local` (default) |
| `powerlink` | 50ms | Yes | Yes | `localhost:8768` (default) |
| `c4amp` | 50ms | Yes | No | Required (`volume.host`) |
| `hdmi` | 50ms | No | No | N/A (local ALSA) |
| `spdif` | 50ms | No | No | N/A (local ALSA) |
| `rca` | 50ms | No | No | N/A (local ALSA) |

Adapters are pluggable — write a custom one to control your amplifier over HTTP, IR, or anything else. See [`services/lib/volume_adapters/`](../services/lib/volume_adapters/) for all adapters and the base class.

### Config Reference

The `volume` section in `config.json`:

```json
"volume": {
  "type": "sonos",          // "sonos", "bluesound", "beolab5", "powerlink", "c4amp", "hdmi", "spdif", or "rca"
  "host": "192.168.1.100",  // Target IP/hostname (sonos, bluesound, beolab5, c4amp)
  "max": 70,                // Maximum volume percentage
  "step": 3,                // Volume step per wheel click
  "output_name": "Sonos"    // Name shown in the UI
}
```
