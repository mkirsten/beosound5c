# Remotes & IR

## BeoRemote One (Bluetooth)

BeoRemote One connects over BLE and is handled by the `beo-bluetooth` service. Pair it on the device: open the **SYSTEM** menu, go to the **Remotes** tab, select **Pair New Remote** (GO), and follow the on-screen instructions (it needs a button press on the remote). The remote MAC is stored in `config.json` under `bluetooth.remote_mac`.

## IR Source Buttons (Beo4 / BeoRemote One)

If a BeoRemote One or Beo4 IR remote is connected, its source buttons (RADIO, CD, TV, etc.) can be mapped to BS5c sources. When pressed, the mapped source activates and starts playback immediately.

Add a `"source"` field to any source in `config.json` (or use the IR trigger field in the web UI):

```json
{
  "spotify": { "client_id": "...", "source": "radio" },
  "usb":     { "source": "amem" },
  "cd":      { "source": "cd" },
  "plex":    { "source": "tv" }
}
```

If no source mappings are configured, the BS5c still handles volume, off, and power for audio commands — sources are just selected from the on-screen menu instead of the remote.

### Audio and Video Master

In B&O systems, each source is classified as Audio or Video. A device that handles a source type is the **audio master** or **video master** for that room. The BS5c auto-detects its role based on your source mappings:

- Only audio sources mapped → BS5c is the **audio master**. Video commands (volume, off) pass through to Home Assistant for an external video master (e.g. a BeoVision TV).
- Any video source mapped → BS5c is also the **video master**. Volume, off, and transport work for all source buttons.

| Audio sources | Video sources | Passthrough |
|---|---|---|
| `radio`, `amem`, `cd`, `n.radio`, `n.music`, `spotify` | `tv`, `dvd`, `dtv`, `v.aux`, `a.aux`, `vmem`, `pc`, `youtube`, `doorcam`, `photo`, `usb2` | `light` (always forwarded to HA) |

**Audio master only** (video handled by a BeoVision):
```json
{ "spotify": { "source": "radio" }, "cd": { "source": "cd" } }
```

**Audio + video master** (BS5c controls everything):
```json
{ "spotify": { "source": "radio" }, "plex": { "source": "tv" } }
```

## Beo6

The BS5c can be controlled by a [Beo6](https://support.bang-olufsen.com/hc/en-us/articles/360041401952-Beo6) remote. Basic control (volume, source selection) works out of the box. For two-way artwork display and playlist browsing on the Beo6 screen, the `beo-beo6` service emulates a BeoMaster 5's XMPP-based BeoNet interface.

### Setup

1. **Add `"beo6": {}` to `config.json`** and restart services. This enables the `beo-beo6` service.
2. **Configure a BeoSound 5 in the Beo6 Configuration Tool** — add a BeoSound 5 device and sync it to the Beo6.
3. **Enable wireless on the Beo6** — hold the power button and press GO to enter settings. Turn on wireless networking.
4. **Set the BS5c IP address** in the Beo6 network settings.

The Beo6 connects to the BS5c over XMPP (port 5222). Cover art is served via HTTP on port 8080.
