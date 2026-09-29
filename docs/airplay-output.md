# AirPlay output

Mirrors what the BeoSound is playing to AirPlay receivers on the network — an
AV receiver in another room, a conservatory speaker — **in parallel** with the
normal output, which is left completely untouched.

This is the sending direction. For the BeoSound as an AirPlay *receiver*, see
[airplay.md](airplay.md); the two are independent.

## How it works

```
sources ──▶ beo_tone_sink ──┬──▶ beo_tone_sink.output ──▶ USB DAC ──▶ PC2 ──▶ MasterLink
                            │                             (untouched)
                            └──(monitor)──▶ pw-loopback ──▶ raop_sink.<device>
```

Audio is tapped from `beo_tone_sink`'s **monitor**, so:

* the receiver gets the tone-controlled BeoSound signal (bass, treble, balance,
  loudness all apply), and
* the DAC → PC2 → MasterLink path is not modified at all, so the B&O speakers
  keep working exactly as before.

Each mirrored receiver is one `pw-loopback` process, supervised by
`beo-airplay-out`.

The RAOP sinks themselves come from `libpipewire-module-raop-discover`, which
`install/modules/system-packages.sh` already enables. That part predates this
service — discovery was there, only the ability to *use* a discovered receiver
was missing.

## Configuration

```json
"airplay_output": {
  "speakers": ["Marantz"],
  "follow_volume": true
}
```

Speakers are matched against the receiver's **friendly name**, not its node
name. PipeWire names RAOP sinks after the device address:

```
raop_sink.WinterGarden.local.10.0.41.137.7000
```

so matching on the node name would break on a new DHCP lease. Matching is a
case-insensitive substring, so `"Marantz"` finds `Marantz-SR8012`.

With no `speakers` configured the service exits cleanly at startup and nothing
is mirrored.

## Which receivers actually work

Not all of them, and the limit is PipeWire's, not this service's.
`module-raop-sink` speaks classic AirPlay — no encryption, or RSA — and does
not implement FairPlay. A receiver that demands FairPlay refuses the
connection, and PipeWire then **destroys the sink node**, which does not come
back until the mDNS record is re-announced.

You can tell which is which before trying, from the receiver's own mDNS record:

```bash
avahi-browse -rt _raop._tcp | grep txt
```

* `et=` lists the encryption types the receiver accepts. `0` is none and `1` is
  RSA, both of which work. `3` and `5` are FairPlay and do not.
* `sf=` with bit `0x200` set means the receiver requires authentication, which
  is likewise unsupported.

Measured on one network:

| Receiver | `et=` | `sf=` | Works |
| --- | --- | --- | --- |
| Marantz SR8012 | `0,4` | `0x404` | yes |
| Apple TV 4K / HD | `0,3,5` | `0x644` | no |
| macOS (MacBook Pro) | `0,3,5` | `0x204` | no |

In short: AirPlay 2-era Apple hardware will not accept a PipeWire RAOP stream,
while AV receivers and older/third-party AirPlay speakers generally will. The
symptom of an unsupported receiver is that it disappears from `/status`'s
`discovered` list moments after a mirror is started against it, while still
being visible to `avahi-browse`.

## The two-second problem

AirPlay buffers roughly two seconds. A mirrored receiver is therefore **never**
in sync with the BeoSound's own output, and nothing here can fix that — it is
inherent to the protocol.

In a different room that does not matter. In the same room it is unusable: the
two outputs echo. So treat this as "also play in the kitchen", not as
multi-room in the AirPlay 2 sense.

## Why a service, and not just a link

The obvious implementation is a single `pw-link` from the tone sink to the RAOP
sink. It does not survive contact with reality:

* **WirePlumber policy owns stream targets.** Adding a second link from
  `beo_tone_sink.output` alongside the DAC link gets undone.
* **RAOP sinks come and go.** Receivers sleep, and mDNS records expire and
  refresh; a sink observed one minute is often absent the next.
* **Node names embed the address**, so a sink that returns after a DHCP lease
  change comes back under a different name.

Hence a reconcile loop (`RECONCILE_INTERVAL`) that matches configured names
against whatever is currently present, starts a loopback when a receiver
appears, and tears it down when it goes away — including when it reappears
under a new name.

## Volume

With `follow_volume` (the default), the BeoSound's own level is applied to each
mirrored stream. The level is read from the router and applied with `wpctl`.

It is applied to the **loopback stream**, not to the receiver's sink: the sink
volume belongs to the receiver, and someone else may be using it.

This is needed because the BeoSound's normal volume happens downstream of the
tap — in the PowerLink/MasterLink domain, on the PC2 card — so the monitor
signal is at full scale and would otherwise ignore the volume wheel entirely.

## Possible next steps

Two things this deliberately does not do yet, recorded so the reasoning is not
lost:

**Choosing receivers from the UI.** Selection is config plus a restart today,
which is a dull way to pick a speaker on a B&O set. The groundwork is already
here: `/status` reports `discovered`, so a SPEAKERS view would need a POST
endpoint to change the selection at runtime and a view to render it. Kept out
of this change to keep it reviewable.

**An AirPlay 2 backend for the receivers PipeWire cannot reach.** The
limitation above is PipeWire's missing HomeKit pairing, not something this
service does wrong — and it is replaceable. This service is already a
supervisor of one streaming subprocess per receiver, so swapping `pw-loopback`
for a sender that does implement pairing, such as
[airplay-cli](https://github.com/music-assistant/airplay-cli), is a change of
transport rather than a redesign:

```
pw-record (beo_tone_sink monitor) | airplay-cli --device <Apple TV>
```

Same service, same config, same volume handling, chosen per receiver. That
would bring Apple TVs, HomePods and modern Macs into range. The real work is
not the library swap but the one-time PIN pairing each device demands, which
needs somewhere to enter a code.

A full media server such as [OwnTone](https://github.com/owntone/owntone-server)
would also solve it, but it wants to own the library, the queue and playback —
which is exactly what beo-router and the sources already do here.

## Checking it works

```bash
systemctl is-active beo-airplay-out
curl -s localhost:8780/status | python3 -m json.tool
```

`discovered` lists every AirPlay receiver PipeWire can currently see, which is
also the quickest way to find the exact name to configure. `mirroring` lists
the loopbacks actually running.

To see the graph:

```bash
XDG_RUNTIME_DIR=/run/user/1000 pw-link -l | grep -i airplay
```
