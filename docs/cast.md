# Screen mirroring (Cast)

Show the BeoSound's own interface on a television: a Chromecast, a Google TV,
or a set with Cast built in. The arc, the menu, the artwork, the views, all of
it, following the wheel as you use it.

## Why it is not screen capture

The obvious way to put a screen on a television is to record it, encode it and
stream it. That is the wrong tool here, for two reasons.

A Raspberry Pi 5 has **no hardware H.264 encoder**. The Pi 4 had one; the 5
dropped it. Encoding 1024x768 at 30 frames would be software x264, roughly a
core of four, on a device already doing audio and a browser. Then delivering
it to a Cast receiver means HLS, which costs seconds of latency.

And the interface is already a web page. So instead of a video of the screen,
the receiver is handed the URL and renders it itself:

```
beo-cast ──(Cast launch)──▶ receiver ──(HTTP)──▶ beo-http ──▶ the UI
```

Nothing is encoded, nothing is delayed, the Pi does no extra work, and the
result is sharp at the television's own resolution rather than an upscale of a
small panel.

## It is a second client, not a mirror

Worth being plain about: the receiver runs its own copy of the interface. It
is the same *state*, not the same pixels.

That is what makes it work. `beo-input` broadcasts wheel and button events to
every connected browser, so the receiver follows the panel as you use it, and
`js/ui-scale.js` fits the 4:3 composition to whatever shape the receiver has.
What you lose is exactness: anything purely local to one client, a transient
toast or an animation phase, can differ for a moment.

## Setup

```bash
sudo systemctl enable --now beo-cast
```

Add the view to the menu in `/etc/beosound5c/config.json`:

```json
"menu": {
  "PLAYING": "playing",
  "CAST": { "url": "softarc/cast.html" }
}
```

Open CAST, and the arc lists the receivers found on the network. GO starts
mirroring to the one under the pointer; GO again on the one already showing
stops it.

Needs `pychromecast`, which the installer pulls in.

## Remembering a receiver

Optional, in `config.json`:

```json
"cast": {
  "receiver": "Living Room TV",
  "auto_start": true
}
```

`auto_start` casts when the service starts. A receiver that is asleep at boot
is retried rather than given up on, so the picture appears when the television
comes on.

`url` shows a page of your choosing instead of the interface — the visualizer
on its own, say, or a dashboard. `app_id` picks a different Cast receiver
application; the default loads an arbitrary URL and needs no developer
registration.

## API

`beo-cast` listens on port 8784, so Home Assistant or `curl` can drive it:

| | |
| --- | --- |
| `GET /status` | what is showing where, and the receivers last seen |
| `GET /receivers` | rescan the network |
| `POST /cast` `{"receiver": "..."}` | start; omit the body to use the configured one |
| `POST /stop` | stop |

## Things that will bite

**Receivers disappear when they sleep.** Not just "no app running" — they drop
off the network entirely, and discovery stops finding them. An empty list
usually means the television is off, not that anything is broken.

**A television switched to another input forgets the cast.** Nothing announces
that, so beo-cast looks every 30 seconds and puts the interface back if it has
gone. That also covers a set waking from standby.

**Launching intermittently lands on the wrong app.** Starting the receiver
application on top of one already running sometimes ends up at the default
receiver instead, and then the URL cannot be delivered. beo-cast quits first
and retries; measured, two of five naive attempts failed and none did
afterwards. It is why casting takes a few seconds.

## In the emulator

CAST appears in the demo menu with two invented receivers. Discovery and the
Cast protocol cannot be faked over HTTP, but the part worth seeing works: the
list, which one is showing, and GO toggling between them.
