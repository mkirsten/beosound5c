#!/usr/bin/env python3
"""
BeoSound 5c AirPlay Output (beo-airplay-out)

Mirrors what the BeoSound is playing to AirPlay receivers on the network — a
Marantz in another room, a conservatory speaker — *in parallel* with the normal
output, which is left completely untouched.

Audio is tapped from ``beo_tone_sink``'s monitor, so the AirPlay speaker gets
the tone-controlled BeoSound signal while the USB DAC → PC2 → MasterLink path
keeps running exactly as before. Each mirror is one pw-loopback process:

    beo_tone_sink (monitor) ──▶ pw-loopback ──▶ raop_sink.<device>

Speakers are matched by friendly name rather than by node name, because
PipeWire names RAOP sinks after the device's address
(``raop_sink.WinterGarden.local.10.0.41.137.7000``) and that changes with a
DHCP lease. The sinks also come and go as devices sleep and mDNS records
expire, so this service reconciles continuously instead of linking once.

Requires ``libpipewire-module-raop-discover``, which install/modules/
system-packages.sh already enables — that module is what makes AirPlay
receivers appear as sinks in the first place.

Config (config.json):

    "airplay_output": {
        "speakers": ["WinterGarden", "Marantz"],
        "follow_volume": true
    }

Note the AirPlay buffer of roughly two seconds: a mirrored speaker is never in
sync with the BeoSound's own output. That is fine in a different room and
unusable in the same one.

Port: 8780
"""

import asyncio
import json
import logging
import os
import signal
import sys

import aiohttp
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.background_tasks import BackgroundTaskSet
from lib.config import cfg
from lib.endpoints import AIRPLAY_OUT_PORT, ROUTER_STATUS
from lib.watchdog import watchdog_loop

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-airplay-out')

# The virtual sink every source already plays into (see
# install/configs/53-beosound5c-tone.conf). Its monitor is what we mirror.
TONE_SINK = "beo_tone_sink"

# PipeWire names every AirPlay receiver it discovers with this prefix.
RAOP_PREFIX = "raop_sink."

# Devices appear and disappear on a human timescale, so this can be slow.
RECONCILE_INTERVAL = 10.0

# Volume follows the BeoSound's own level; polling localhost is cheap and
# wpctl is only invoked when the value actually changes.
VOLUME_INTERVAL = 2.0

PW_TIMEOUT = 5.0


def _slug(name: str) -> str:
    """A node-name-safe form of a speaker's friendly name."""
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


class Mirror:
    """One running pw-loopback, mirroring the tone sink to one receiver."""

    __slots__ = ("speaker", "node_name", "loopback_name", "proc")

    def __init__(self, speaker: str, node_name: str, proc):
        self.speaker = speaker
        self.node_name = node_name
        self.loopback_name = f"beo-airplay-{_slug(speaker)}"
        self.proc = proc

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None


class AirPlayOutput:
    def __init__(self):
        self._speakers = [s for s in (cfg("airplay_output", "speakers", default=[]) or []) if s]
        self._follow_volume = bool(cfg("airplay_output", "follow_volume", default=True))
        self._mirrors: dict[str, Mirror] = {}
        self._discovered: list[dict] = []
        self._volume: float | None = None
        self._applied_volume: float | None = None
        self._session: aiohttp.ClientSession | None = None
        self._runner: web.AppRunner | None = None
        self._tasks = BackgroundTaskSet(log, label="airplay-out")

    # ── PipeWire helpers ──

    async def _run(self, *args: str) -> str | None:
        """Run a PipeWire CLI tool and return stdout, or None on failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=PW_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("%s timed out", args[0])
            return None
        except (FileNotFoundError, OSError) as e:
            log.warning("%s unavailable: %s", args[0], e)
            return None
        if proc.returncode != 0:
            log.warning("%s failed: %s", args[0], err.decode("utf-8", "replace").strip())
            return None
        return out.decode("utf-8", "replace")

    async def _sinks(self) -> list[dict]:
        """Every Audio/Sink PipeWire currently knows about."""
        raw = await self._run("pw-dump")
        if not raw:
            return []
        try:
            objects = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Could not parse pw-dump output")
            return []
        sinks = []
        for obj in objects:
            props = ((obj.get("info") or {}).get("props") or {})
            if props.get("media.class") != "Audio/Sink":
                continue
            sinks.append({
                "id": obj.get("id"),
                "name": props.get("node.name", ""),
                "description": props.get("node.description", ""),
            })
        return sinks

    @staticmethod
    def _match(speaker: str, sinks: list[dict]) -> dict | None:
        """Find the RAOP sink for a configured speaker name.

        Matched against the friendly description first, since that is what the
        user configures and what survives an address change.
        """
        want = speaker.strip().lower()
        for sink in sinks:
            if not sink["name"].startswith(RAOP_PREFIX):
                continue
            if want in (sink["description"] or "").lower() or want in sink["name"].lower():
                return sink
        return None

    # ── Mirrors ──

    async def _start_mirror(self, speaker: str, sink: dict):
        name = f"beo-airplay-{_slug(speaker)}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "pw-loopback", "-n", name, "-C", TONE_SINK, "-P", sink["name"],
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as e:
            log.error("Cannot start pw-loopback: %s", e)
            return
        self._mirrors[speaker] = Mirror(speaker, sink["name"], proc)
        self._applied_volume = None      # re-apply to the new stream
        log.info("Mirroring to %s (%s)", speaker, sink["name"])

    async def _stop_mirror(self, speaker: str, reason: str):
        mirror = self._mirrors.pop(speaker, None)
        if mirror is None:
            return
        if mirror.alive:
            mirror.proc.terminate()
            try:
                await asyncio.wait_for(mirror.proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                mirror.proc.kill()
        log.info("Stopped mirroring to %s (%s)", speaker, reason)

    async def _reconcile(self):
        """Start mirrors for speakers that are present, stop the rest."""
        sinks = await self._sinks()
        self._discovered = [s for s in sinks if s["name"].startswith(RAOP_PREFIX)]

        for speaker in self._speakers:
            target = self._match(speaker, sinks)
            mirror = self._mirrors.get(speaker)

            if mirror and not mirror.alive:
                await self._stop_mirror(speaker, "loopback exited")
                mirror = None
            # A receiver that changed address comes back under a new node
            # name, so the old loopback is pointing at something that no
            # longer exists.
            if mirror and target and mirror.node_name != target["name"]:
                await self._stop_mirror(speaker, "receiver moved")
                mirror = None

            if target and not mirror:
                await self._start_mirror(speaker, target)
            elif mirror and not target:
                await self._stop_mirror(speaker, "receiver gone")

    # ── Volume ──

    async def _poll_volume(self):
        if not self._session:
            return
        try:
            async with self._session.get(ROUTER_STATUS, timeout=5) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        except Exception:
            return
        volume = data.get("volume")
        if isinstance(volume, (int, float)):
            self._volume = float(volume)

    async def _apply_volume(self):
        """Mirror the BeoSound's level onto each loopback's playback stream.

        Deliberately the loopback stream rather than the sink itself: the sink
        volume is the receiver's own, which someone else may be using.
        """
        if self._volume is None or self._volume == self._applied_volume:
            return
        if not self._mirrors:
            return
        sinks_raw = await self._run("pw-dump")
        if not sinks_raw:
            return
        try:
            objects = json.loads(sinks_raw)
        except json.JSONDecodeError:
            return

        wanted = {m.loopback_name for m in self._mirrors.values()}
        fraction = max(0.0, min(1.0, self._volume / 100.0))
        for obj in objects:
            props = ((obj.get("info") or {}).get("props") or {})
            name = props.get("node.name", "")
            # pw-loopback exposes its playback side as output.<name>
            if not name.startswith("output."):
                continue
            if name[len("output."):] not in wanted:
                continue
            await self._run("wpctl", "set-volume", str(obj.get("id")), f"{fraction:.3f}")
        self._applied_volume = self._volume
        log.info("AirPlay output volume set to %.0f%%", self._volume)

    # ── Loops ──

    async def _reconcile_loop(self):
        while True:
            try:
                await self._reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Reconcile failed: %s", e)
            await asyncio.sleep(RECONCILE_INTERVAL)

    async def _volume_loop(self):
        while True:
            await asyncio.sleep(VOLUME_INTERVAL)
            try:
                await self._poll_volume()
                await self._apply_volume()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("Volume sync failed: %s", e)

    # ── HTTP ──

    async def _handle_status(self, request):
        return web.json_response({
            "service": "airplay_out",
            "configured": self._speakers,
            "follow_volume": self._follow_volume,
            "volume": self._volume,
            "mirroring": [
                {"speaker": m.speaker, "sink": m.node_name, "alive": m.alive}
                for m in self._mirrors.values()
            ],
            "discovered": [
                {"name": s["description"] or s["name"], "sink": s["name"]}
                for s in self._discovered
            ],
        })

    async def start(self):
        if not self._speakers:
            log.info("No airplay_output.speakers configured — nothing to mirror")
            from lib.watchdog import sd_notify
            sd_notify("READY=1\nSTATUS=No speakers configured, exiting")
            sd_notify("STOPPING=1")
            sys.exit(0)

        app = web.Application()
        app.router.add_get("/status", self._handle_status)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", AIRPLAY_OUT_PORT).start()
        log.info("HTTP API on port %d", AIRPLAY_OUT_PORT)

        self._session = aiohttp.ClientSession()
        # Tracked rather than a bare create_task so shutdown cancels it and
        # exceptions are logged (see lib/background_tasks.py).
        self._tasks.spawn(watchdog_loop(), name="watchdog")

        log.info("Mirroring BeoSound audio to: %s", ", ".join(self._speakers))
        self._tasks.spawn(self._reconcile_loop(), name="reconcile")
        if self._follow_volume:
            self._tasks.spawn(self._volume_loop(), name="volume")

    async def stop(self):
        for speaker in list(self._mirrors):
            await self._stop_mirror(speaker, "shutting down")
        await self._tasks.cancel_all()
        if self._session:
            await self._session.close()
        if self._runner:
            await self._runner.cleanup()

    async def run(self):
        await self.start()
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_event.set)
        try:
            await stop_event.wait()
        finally:
            await self.stop()


if __name__ == "__main__":
    asyncio.run(AirPlayOutput().run())
