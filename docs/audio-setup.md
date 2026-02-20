# Audio Setup Options

Each BeoSound 5c is configured with one audio output. The installer will ask you to choose during setup, or you can change it later in `config.json` under the `volume` section.

## Which setup is right for me?

- **Sonos speakers?** Use Sonos as your output. The Sonos speaker handles playback natively and the BS5c controls volume directly on it. This is the simplest setup — no extra hardware needed. Set `volume.type` to `"sonos"`.

- **B&O PowerLink speakers?** Use PowerLink. The BS5c sends volume and power commands over the MasterLink bus via a PC2 USB interface. Works with BeoLab 6000, 8000, and other PowerLink speakers. Set `volume.type` to `"powerlink"`.

- **Other speakers or amplifier?** Connect them to the Pi via HDMI, optical/Toslink, or RCA (with the appropriate HAT). The BS5c plays audio locally and controls volume via ALSA. Pick whichever output matches your cable.

- **None of the above?** Read on — there's a lot of flexibility. You can mix any output with any volume adapter, use AirPlay speakers, or write a custom adapter for your setup.

## Output Options

| Output | Playback | Volume Control | Requirements |
|---|---|---|---|
| PowerLink | Local | By BS5c | B&O PowerLink speakers or BeoLink Passive + passive speakers |
| HDMI | Local | By BS5c | Amplifier or device with HDMI audio input |
| Optical / Toslink | Local | By BS5c | S/PDIF HAT (e.g. HiFiBerry Digi) |
| RCA | Local | By BS5c | DAC HAT with RCA out |
| Sonos | Sonos (network) | Sonos | Any Sonos speaker (S1 or S2, any generation) |
| AirPlay speaker | Local via AirPlay | AirPlay | Any AirPlay-compatible speaker |

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

### Sonos

The Sonos speaker handles playback natively. The BS5c sends commands and monitors what's playing (track info, artwork, volume) but does not produce the audio itself. Sources that play locally (CD, USB) stream to the Sonos speaker via AirPlay. Works with any Sonos speaker — S1 or S2, any generation.

**Config:**
```json
"volume": { "type": "sonos", "host": "192.168.1.100", "max": 70 }
```

### AirPlay Speaker

The BS5c plays audio locally and streams it wirelessly to an AirPlay-compatible speaker. This works with any AirPlay receiver (HomePod, airport, third-party speakers, etc.).

### BeoLab 5 (via BeoLab 5 Controller)

A custom option for controlling a pair of BeoLab 5 speakers via their sync port. Requires the BeoLab 5 Controller — a dedicated board that sends serial commands to both speakers.

**Config:**
```json
"volume": { "type": "beolab5", "host": "beolab5-controller.local", "max": 70 }
```

## Playback Modes

There are two playback modes. The configured output determines which is used.

**Local playback** — The BS5c plays audio itself and sends it to the connected output. For wired outputs (PowerLink, HDMI, Optical, RCA) audio goes directly to the hardware. For AirPlay speakers, audio is streamed wirelessly.

**Sonos playback** — The Sonos speaker handles playback natively. The BS5c sends commands and monitors what's playing but does not produce the audio itself. Sources that play locally (CD, USB) stream to the Sonos speaker via AirPlay.

## Sources

Sources provide content to the BS5c. How they reach the speakers depends on the configured output.

| Source | How it plays |
|---|---|
| CD | Local playback from USB CD/DVD drive. Metadata from MusicBrainz. When output is Sonos or AirPlay, streams to the speaker wirelessly. |
| USB | Local playback from USB storage. Browses folders, reads album artwork. Supports FLAC, MP3, AAC, WAV, OGG, and more. When output is Sonos or AirPlay, streams to the speaker wirelessly. |
| Spotify | When output is Sonos, uses Spotify Connect for native playback on the speaker (best quality). Otherwise plays locally and routes to the configured output. |
| Demo | Text-to-speech demo clips. Always plays locally. |

Sources register with the router and appear in the menu automatically. The remote's media keys (play, pause, next, prev) are forwarded to whichever source is currently active.

## Volume Adapters

The router sends volume commands through whichever adapter matches the configured output. Each adapter handles debouncing and power management.

Adapters are pluggable — write a custom one to control your amplifier over HTTP, IR, or anything else. See [`services/lib/volume_adapters/`](../services/lib/volume_adapters/) for all adapters and the base class.

### Config Reference

The `volume` section in `config.json`:

```json
"volume": {
  "type": "sonos",          // "sonos", "beolab5", "powerlink", "hdmi", "spdif", or "rca"
  "host": "192.168.1.100",  // Target IP/hostname (sonos, beolab5 only)
  "max": 70,                // Maximum volume percentage
  "step": 3,                // Volume step per wheel click
  "output_name": "Sonos"    // Name shown in the UI
}
```
