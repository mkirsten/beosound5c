# The PLAYING backdrop

What fills the screen behind the now-playing text. The device has always
decided this for you: the artwork, then a Spotify canvas or a music video if
the track had one. This makes it a choice you can turn to.

```
Auto · Artwork · Canvas · Music video · Tracks · VU · Lake · Lake blue · MilkDrop
```

**Auto is first and is the default**, so a device nobody touches behaves
exactly as it did before.

## Changing it

**The wheel** that picks albums and radio stations. It does nothing on PLAYING
otherwise, so it steps the backdrop instead. Turning is continuous and the
list is not, so a step is taken on accumulated travel: a nudge does nothing, a
deliberate turn moves one.

**FORMAT on the remote**, which `masterlink.py` has decoded all along without
anything using it. It reads as "change how the picture is presented", which is
what this does, and unlike LIST or MENU nobody expects to navigate with it.

## The list is built per track

A track with no canvas has no Canvas entry. A device with `beo-visualizer`
stopped has no meters. A source that is not a CD has no Tracks. Stepping
therefore never lands on something that would draw nothing.

The choice is held **by name, not by position**. Keyed on position, a track
without a canvas would shift you one along on the way past: pick MilkDrop, get
Lake, with nothing touched. Pick MilkDrop and you keep MilkDrop; if a track
cannot show it, that track falls back to Auto and the next one that can has it
back.

## Where the choice lives

In `beo-router`, not in the browser, and broadcast to every client.

That matters because there can be more than one browser. The same UI is also
shown on a television over Google Cast, and two screens disagreeing about what
they are drawing would be worse than either of them being wrong. The router
holds nothing but the name; which backdrops exist depends on the track and on
which services are up, and the UI is where that is known, so it resolves the
next one and posts it back.

| | |
| --- | --- |
| `GET /router/backdrop` | the chosen name |
| `POST /router/backdrop` `{"backdrop": "..."}` | set it, and tell every client |

It is kept in `/etc/beosound5c/backdrop-state.json`, so it survives a reboot.

## The visualizer

Four of the backdrops come from `beo-visualizer`, which measures what is
playing and streams the numbers to the UI, which draws them.

```
beo_tone_sink (monitor) ──▶ pw-record ──▶ RMS + FFT ──▶ WebSocket ──▶ canvas
```

Everything audible passes through `beo_tone_sink` on its way to the DAC, so
one tap on that sink's monitor covers every source with no per-source work.

The service produces measurements only: per-channel RMS and peak in dBFS plus
24 log-spaced bands. All the smoothing — VU ballistics, peak hold, decay —
happens in the browser at display refresh. Doing it in the service would tie
the look to the 23 ms analysis block and make it stutter.

The tap sits *ahead* of the device's own volume, which is applied downstream
in the PowerLink/MasterLink domain, so the meters show programme level rather
than listening level. Turning the volume down does not drop the needles, which
is what a VU meter should do.

```bash
sudo systemctl enable --now beo-visualizer
```

Needs `pw-record` (`pipewire-bin`) and NumPy (`python3-numpy`); the installer
pulls both in. Without it the four modes simply are not in the list.

### Tuning

Optional, in `config.json`:

```json
"visualizer": { "bands": 24, "vu_reference_dbfs": -14 }
```

`vu_reference_dbfs` is where 0 VU sits. Programme level varies enough between
masters that this is a taste setting: make it more negative to send the
needles higher.

### Cost

The canvas is held to a pixel budget rather than a device-pixel-ratio cap.
Ratio is a property of the screen; what costs time is how many pixels have to
be filled each frame. The device's own 1024x768 panel fills 0.79 megapixels
and never comes near the budget. A 1280x720 television reporting ratio 2 would
ask for 3.69, nearly five times as many, on far weaker silicon: measured at 20
fps over Cast, and 27 to 34 once budgeted.

## Things that will bite

**A silent socket.** The feed can die without the page hearing about it: the
server drops the client, no close event arrives, and the page redraws its last
measurement forever. Seen twice on a television. Measurements arrive about 43
times a second, so three seconds of silence is taken to mean the feed is gone
whatever the socket claims.

**A dead feed looks dead.** The synthetic signal exists so the emulator can
show all four modes with no audio to measure. On a device it is off: needles
at rest, rather than inventing a light show for music that is not playing.

## In the emulator

All of it works. The backdrop choice is held in the demo backend in place of
the router, the four visualizer modes draw a synthesised signal, and Canvas,
Video and Tracks are absent because the demo has no canvas, no video and no
CD — which is itself the behaviour worth seeing.
