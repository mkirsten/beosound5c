# Audio Setup Options

Each BeoSound 5c is configured with a **player** (how audio is played) and a **volume adapter** (how volume is controlled). Both are set in the web UI or directly in `config.json`.

## Which setup is right for me?

- **Sonos speakers?** Use Sonos as your player. The Sonos speaker handles playback natively — the BS5c sends commands and monitors what's playing but does not produce audio itself. All streaming sources (Spotify, Apple Music, TIDAL, Plex) work. Set `player.type` to `"sonos"` and `volume.type` to `"sonos"`.

- **BluOS player?** Use BlueSound as your player. Plex, CD, USB, and TIDAL work. Spotify works via Spotify Connect (experimental, requires Premium). Apple Music does not — it sends share links only Sonos handles. Set `player.type` to `"bluesound"` and `volume.type` to `"bluesound"`.

- **Denon HEOS device?** Use HEOS as your player. Same source support as BlueSound: Plex, CD, USB, and TIDAL work; Spotify via Connect (experimental, Premium); Apple Music does not. Works with any HEOS-enabled device (HEOS speakers, Denon/Marantz AVRs and amps). Set `player.type` to `"heos"` and `volume.type` to `"heos"`.

- **WiiM / LinkPlay speaker? (experimental)** Use WiiM as your player. Same source support as BlueSound/HEOS. Set `player.type` to `"wiim"` and `volume.type` to `"wiim"`. Not yet validated on hardware.

- **Modern B&O speaker (Mozart)? (experimental)** Beolab 8/28, Beosound A5/A9-5thgen/Balance/Emerge/Level, Beoconnect Core, etc. Uses B&O's official Mozart Open API and supports Beolink multiroom in the SPEAKERS menu. Set `player.type` to `"mozart"` and `volume.type` to `"mozart"`. Not yet validated on hardware.

- **Older B&O speaker (ASE)? (experimental)** BeoPlay A6/A9-gen2, BeoSound 1/2/35-gen1, BeoSound Core, etc. Uses the BeoZone/BeoNotify product API. Set `player.type` to `"ase"` and `volume.type` to `"ase"`. Standalone player only (no multiroom yet); not yet validated on hardware.

- **B&O PowerLink speakers?** Use PowerLink for volume. Local sources (CD, USB) play on the Pi and output to PowerLink speakers via the MasterLink bus. Streaming sources need a Sonos or BlueSound player. Set `volume.type` to `"powerlink"`.

- **Other speakers or amplifier?** Connect via HDMI, optical/Toslink, or RCA (with the appropriate HAT). Local sources play directly. Streaming sources need a Sonos or BlueSound player. Pick whichever output matches your cable.

## Player Types

The player service handles network-based playback. Sources send play commands to the player, which talks to the actual speaker.

| Player | Capabilities | How It Plays |
|---|---|---|
| Sonos | `spotify`, `url_stream` | ShareLink (Spotify, Apple Music, TIDAL) or `play_uri` (URLs) |
| BlueSound | `url_stream` | BluOS HTTP API with stream URLs |
| HEOS | `url_stream` | HEOS CLI protocol (TCP 1255) with stream URLs |
| WiiM | `url_stream` | LinkPlay HTTP API (`httpapi.asp`) with stream URLs — **experimental** |
| B&O Mozart | `url_stream` | Mozart Open API (`/api/v1`, `POST /playback/uri`), Beolink multiroom — **experimental** |
| B&O ASE | *(none)* | BeoZone/BeoNotify product API — transport/metadata/volume only; can't play external URLs — **experimental**, no grouping |
| Local | `spotify`, `url_stream` | mpv via PipeWire/PulseAudio; Spotify via go-librespot |

Only one player is active — determined by `player.type` in config.json. The type guard in PlayerBase ensures only the matching player service starts.

> **Experimental players not yet validated on hardware:** WiiM/LinkPlay, B&O Mozart, and B&O ASE. Multiroom grouping and "Play here" targeting (see [SPEAKERS](#speakers-multiroom--experimental)) are likewise experimental on BlueSound, HEOS, WiiM, and Mozart; only Sonos grouping is field-proven. ASE ships as a standalone player (no grouping) until its Beolink API is confirmed.

## Source Compatibility

Sources check the player's capabilities at startup to determine how to play content.

| Source | Sonos | BlueSound | HEOS | WiiM* | No Player |
|---|---|---|---|---|---|
| **Spotify** | Yes — ShareLink queues Spotify URIs natively | Connect† | Connect† | Connect† | No |
| **Apple Music** | Yes — ShareLink handles Apple Music share URLs | No | No | No | No |
| **TIDAL** | Yes — ShareLink handles TIDAL share URLs | Yes — direct stream URLs | Yes — direct stream URLs | Yes — direct stream URLs | No |
| **Plex** | Yes — `play_uri` with direct stream URLs | Yes — direct stream URLs | Yes — direct stream URLs | Yes — direct stream URLs | No |
| **CD** | Yes — plays on Pi via mpv | Yes — plays on Pi via mpv | Yes — plays on Pi via mpv | Yes — plays on Pi via mpv | Yes |
| **USB** | Yes — streams track URLs to Sonos | Yes — streams track URLs | Yes — streams track URLs | Yes — streams track URLs | Yes — falls back to local mpv |

*WiiM is experimental — the columns reflect the same capability model as BlueSound/HEOS (`url_stream`), unverified on hardware.

**Key points:**
- Spotify and Apple Music send share links via the `uri` parameter. Only Sonos handles these (via its ShareLink plugin). BlueSound, HEOS, and WiiM ignore `uri` — they only support direct stream URLs via `url`.
- **† Spotify Connect (experimental):** for BlueSound/HEOS/WiiM the BS5c instead drives the speaker's *built-in* Spotify Connect receiver via the Spotify Web API — it starts/transfers playback to the device rather than sending a share link. Requires **Spotify Premium** and the speaker logged into the same Spotify account. Set `spotify.connect_device` to the speaker's Connect name (else it auto-picks by device name / active device). Apple Music has no equivalent transfer API, so it stays Sonos-only.
- TIDAL works with both players: on Sonos it uses ShareLink (player manages queue); on BlueSound it resolves direct stream URLs via tidalapi and manages its own queue (like Plex)
- Plex works with both players because it sends direct stream URLs (via `url`), not share links
- Plex and TIDAL (on BlueSound) manage their own queues (next/prev build new stream URLs) while Spotify and Apple Music let the player handle queue advancement after the initial share link is queued
- CD always plays locally via mpv — it doesn't use the player service
- USB auto-detects: if the player supports `url_stream`, it streams track URLs to the player; otherwise falls back to local mpv
- **B&O ASE** advertises no capabilities — it can't play any BS5c source (no URL-play endpoint). It surfaces transport/metadata/volume for whatever the speaker plays from its *own* sources; TIDAL/Plex/CD/USB/Spotify are all unavailable on it. Use it only if you drive playback from the B&O app.

## SPEAKERS (multiroom) — experimental

When the configured player is a grouping-capable network player (Sonos, BlueSound, HEOS,
or WiiM) and other rooms of the same platform are found, a **SPEAKERS** entry appears in
the menu. It lists the discovered rooms with their now-playing info, the room you're
currently driving pinned at the top. On a highlighted room:

- **GO** — group it with what you're playing (extend your audio to that room, or, if the
  BS5c is idle, join what that room is already playing).
- **RIGHT** — *control this room*: re-point the BS5c so your controls and volume drive that
  room instead. It does **not** move the audio that's already playing elsewhere — the
  previous room keeps going; you're switching which room the BS5c commands. Sticky
  (survives a reboot) until you control another room, or control the **HOME** room (marked
  in the list) which resets to your configured default.
- **LEFT** — back. To ungroup, select the **UNJOIN** row at the top of the list.

Grouping uses each platform's native multiroom (Sonos groups, BluOS `AddSlave`, HEOS
`set_group`, LinkPlay `ConnectMasterAp`). Only same-platform rooms are shown. Local /
PowerLink players never show SPEAKERS — they produce audio on the device itself.

> **Experimental**, except Sonos grouping which is field-proven. BlueSound, HEOS, and WiiM
> grouping, and "Play here" targeting on all platforms, have not yet been validated on
> hardware.

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

### HEOS

The Denon HEOS device handles playback natively. The BS5c talks to it over the HEOS CLI protocol (TCP port 1255, via the pyheos library) and receives push events for track, state, and volume changes. Works with any HEOS-enabled device — HEOS speakers, Denon Home, and Denon/Marantz AVRs and amplifiers.

**Config:**
```json
"player": { "type": "heos", "ip": "192.168.1.100" },
"volume": { "type": "heos", "host": "192.168.1.100", "max": 70 }
```

### WiiM / LinkPlay — experimental

Drives a WiiM (or other LinkPlay) speaker via the LinkPlay HTTP API (`httpapi.asp`). The
BS5c polls `getPlayerStatus`/`getMetaInfo` for state, metadata, and artwork, and sends
transport/volume via `setPlayerCmd`. Newer WiiM firmware serves HTTPS with a self-signed
certificate (verification is disabled); older LinkPlay devices serve plain HTTP. Same
source support as BlueSound/HEOS (`url_stream`: TIDAL, Plex, CD, USB; Spotify via Spotify
Connect — experimental, Premium; not Apple Music). **Experimental — not yet validated on
hardware.**

**Config:**
```json
"player": { "type": "wiim", "ip": "192.168.1.100" },
"volume": { "type": "wiim", "host": "192.168.1.100", "max": 70 }
```

### B&O Mozart — experimental

Drives a modern B&O speaker via the official [Mozart Open API](https://github.com/bang-olufsen/mozart-open-api) (`http://<ip>:8080/api/v1`, no auth on the LAN). The BS5c polls `/playback/state` for now-playing, sends transport via `/playback/command/*`, plays URLs via `POST /playback/uri`, and does multiroom over **Beolink** (`/beolink/peers`, `/beolink/join/{jid}`, `/beolink/leave`) — so the SPEAKERS menu works. Same source support as BlueSound/HEOS. **Not yet validated on hardware.**

**Config:**
```json
"player": { "type": "mozart", "ip": "192.168.1.100" },
"volume": { "type": "mozart", "host": "192.168.1.100", "max": 70 }
```

### B&O ASE — experimental

Drives an older (pre-Mozart) B&O speaker via the BeoZone/BeoNotify product API (`http://<ip>:8080`). Transport via `/BeoZone/Zone/Stream/*`, volume via `/BeoZone/Zone/Sound/Volume/Speaker/Level`, now-playing from the `/BeoNotify/Notifications` stream. There's no published spec to validate against, and Beolink grouping on ASE isn't wired yet — it ships as a **standalone player (no SPEAKERS)**. **Not yet validated on hardware.**

**Config:**
```json
"player": { "type": "ase", "ip": "192.168.1.100" },
"volume": { "type": "ase", "host": "192.168.1.100", "max": 70 }
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

### Chassis 3.5mm line-out (via S/PDIF HAT)

The original BeoSound 5 chassis has a 3.5mm line-out jack on the rear. Audio reaches it via the S/PDIF HAT — install the HAT and wire up its S/PDIF coax output as described in the [S/PDIF](#optical--toslink-spdif) section above, and the 3.5mm jack becomes active in parallel.

**Important:** the 3.5mm output is **line level regardless of the BS5c volume setting**. The BS5c's volume wheel does not attenuate it. Volume must be handled downstream (by your amplifier, active speakers, or pre-amp).

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
| `heos` | 80ms | No | No | `player.ip` (default) |
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
  "type": "sonos",          // "sonos", "bluesound", "heos", "beolab5", "powerlink", "c4amp", "hdmi", "spdif", or "rca"
  "host": "192.168.1.100",  // Target IP/hostname (sonos, bluesound, heos, beolab5, c4amp)
  "max": 70,                // Maximum volume percentage
  "step": 3,                // Volume step per wheel click
  "output_name": "Sonos"    // Name shown in the UI
}
```

## Spotify Setup

1. **Create a Spotify Developer App** (free) at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard):
   - Click "Create App" — name it anything (e.g. "BeoSound 5c")
   - Add this Redirect URI: `https://<device-ip>:8772/callback` (the setup page on the device shows the exact URI)
   - Select "Web API"
   - Copy the Client ID

2. **Configure**: Enter the Client ID in the web UI under Sources → Spotify, or add it to `config.json`:
   ```json
   { "spotify": { "client_id": "your-client-id-here" } }
   ```

3. **Authenticate**: Navigate to SPOTIFY on the BS5 display and scan the QR code with your phone.

**Notes:**
- Spotify apps in "Development" mode allow up to 25 users. Add your Spotify account email under **User Management** in the developer dashboard.
- A self-signed SSL certificate is generated during install (required for Spotify OAuth). Your phone must accept the certificate warning when scanning the QR code.
- **Use a separate Client ID per device.** Spotify caches the granted scope set per `(user, client_id)` pair. If you share one Client ID across multiple BS5c devices, the first device's grant locks in the scope set for all of them — later devices may end up with a narrower grant than they request. One developer app per device avoids this entirely.

### Troubleshooting: only Liked Songs appears (or very few playlists)

Symptom: the Spotify view shows only "Liked Songs" or 1–2 playlists despite having many in your account. The fetch summary in `journalctl -u beo-source-spotify` will show `playlists_from_api=0` or `1`.

Cause: the OAuth grant on Spotify's side is missing `playlist-read-private` and/or `playlist-read-collaborative`. This usually means the grant was issued before BS5c started requesting those scopes. Spotify silently re-issues the *previously granted* scope set on subsequent auth attempts — re-authenticating without revoking first does **not** add the new scopes.

Fix:
1. Revoke the existing grant at [spotify.com/account/apps](https://www.spotify.com/account/apps) — find your BS5c app and click "Remove Access".
2. Re-authenticate via the BS5c `/setup` page (or scan the QR on the SPOTIFY view).
3. The consent screen will now reappear with the full current scope list. Accept it.
4. Verify with `sudo journalctl -u beo-source-spotify --since '5 min ago' | grep -E 'OAuth: Spotify granted|Summary:'` — `playlists_from_api` should now match your actual playlist count.

### Troubleshooting: playlists fetch but tracks fail with HTTP 403

Spotify's Web API migration (Feb–Mar 2026) removed the old `/playlists/{id}/tracks` endpoint for apps in Development Mode. BS5c v0.8.8+ uses the replacement `/playlists/{id}/items` endpoint, which fixes track fetching for all playlists **you own or collaborate on**.

One restriction remains and is Spotify policy, not a BS5c bug: Development Mode apps cannot read tracks from playlists *owned by other users* (followed playlists, a partner's playlists, editorial lists). Those show up in the fetch log as `NOTE: N playlist(s) owned by other users returned 403`. Your options:

- Duplicate the playlist into your own account (in the Spotify app: playlist → ⋯ → *Add to other playlist* → *New playlist*), or
- Apply for **Extended Quota Mode** at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard), which lifts the restriction.

Run `sudo python3 ~/beosound5c/tools/spotify-diag.py` on the device to see exactly which category each failing playlist falls into.

### Spotify re-authentication every 6 months

Since June 2026, Spotify expires refresh tokens **6 months after the original authorization** — token refreshes do not extend this window ([announcement](https://developer.spotify.com/blog/2026-06-18-refresh-token-expiration)). When the token expires, BS5c discards it and the SYSTEM page / logs will ask you to re-authenticate via the `/setup` page. Re-auth takes seconds (Spotify remembers the granted permissions) and the device warns in the journal starting ~1 month before expiry. Devices configured with a `token_master` follow the master automatically — only the master device needs the re-auth.

### Spotify Canvas (Optional)

Canvas shows looping video backgrounds behind tracks in immersive mode — the same videos you see in the Spotify mobile app. Not all tracks have a Canvas.

To enable Canvas, add your `sp_dc` cookie to `/etc/beosound5c/secrets.env`:

1. Log into [open.spotify.com](https://open.spotify.com) in a browser
2. Open DevTools → **Application** → **Cookies** → `open.spotify.com`
3. Copy the value of `sp_dc`
4. On the device: add `SPOTIFY_SP_DC="<your-cookie>"` to `/etc/beosound5c/secrets.env`
5. Restart: `sudo systemctl restart beo-source-spotify`

The cookie is valid for ~1 year.
