#!/usr/bin/env python3
"""
BeoSound 5c Visualizer feed (beo-visualizer)

Measures what the device is playing and streams the numbers to the UI, which
draws them. Everything audible passes through ``beo_tone_sink`` on its way to
the DAC, so one tap on that sink's monitor covers every source — Spotify,
radio, CD, USB, AirPlay — with no per-source work.

    beo_tone_sink (monitor) ──▶ pw-record ──▶ RMS + FFT ──▶ WebSocket ──▶ canvas

The split is deliberate: this service produces *measurements* only, and all
visual smoothing (VU ballistics, peak hold, decay) happens in the browser at
display refresh rate. Doing the smoothing here would tie the look to the
analysis block rate and make it stutter.

Note the tap sits ahead of the BeoSound's own volume, which is applied
downstream in the PowerLink/MasterLink domain. So the meters show programme
level, not listening level — which is what a VU meter should show.

The selected mode is persisted here rather than in the browser so it survives
a reboot and a cleared browser profile.

Config (config.json):
    "visualizer": { "bands": 24 }

Port: 8783
"""

import asyncio
import json
import math
import os
import signal
import struct

import aiohttp
import numpy as np
from aiohttp import web

# No sys.path juggling: this module sits directly in services/, so Python
# already has its directory on sys.path and lib/ imports as it stands.
from lib.background_tasks import BackgroundTaskSet
from lib.config import cfg
from lib.correlation import install_logging
from lib.endpoints import ROUTER_MEDIA, VISUALIZER_PORT
from lib.watchdog import watchdog_loop

log = install_logging("beo-visualizer")

TONE_SINK = "beo_tone_sink"
RATE = 44100
CHANNELS = 2
# 1024 frames is ~23 ms: fast enough that a VU needle driven from it looks
# continuous, long enough for usable low-frequency resolution (~43 Hz bins).
BLOCK = 1024
BLOCK_BYTES = BLOCK * CHANNELS * 2      # s16

DEFAULT_BANDS = 24
BAND_LOW_HZ = 40.0
BAND_HIGH_HZ = 16000.0

MODES = ("vu", "lake", "lake_blue", "milkdrop")

# Where the now-playing line gets its numbers.
#
# The router and the local player both report a "position" field, but neither
# advances it — both sit at 1 for the whole track (measured). go-librespot's
# own API does report live position in milliseconds, so that is what the
# elapsed time comes from. It follows that the clock only ticks for Spotify;
# other sources still get title and artist, just no elapsed time. Fixing the
# player's stale field is a separate job.
LIBRESPOT_STATUS = "http://localhost:3678/status"
STATE_FILE = os.path.join(
    os.getenv("BS5C_CONFIG_DIR", "/etc/beosound5c"), "visualizer-state.json")

# Silence floor. Below this the UI shows its resting state rather than
# amplifying dither into a light show.
SILENCE_DBFS = -70.0

RESTART_DELAY = 2.0


def _dbfs(x: float) -> float:
    return 20.0 * math.log10(x) if x > 1e-9 else -120.0


class Analyzer:
    """Turns PCM blocks into the numbers the renderers need."""

    def __init__(self, bands: int):
        self.window = np.hanning(BLOCK).astype(np.float32)
        # Log-spaced band edges: linear bins put almost everything in the
        # treble, which makes a spectrum display look dead.
        edges = np.geomspace(BAND_LOW_HZ, BAND_HIGH_HZ, bands + 1)
        bin_hz = RATE / BLOCK
        self.slices = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            a = max(1, int(lo / bin_hz))
            b = max(a + 1, int(hi / bin_hz))
            self.slices.append((a, min(b, BLOCK // 2)))

    def analyse(self, raw: bytes) -> dict:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        left = samples[0::2]
        right = samples[1::2]

        rms_l = float(np.sqrt(np.mean(left * left)))
        rms_r = float(np.sqrt(np.mean(right * right)))
        peak_l = float(np.max(np.abs(left))) if left.size else 0.0
        peak_r = float(np.max(np.abs(right))) if right.size else 0.0

        mono = (left + right) * 0.5
        spectrum = np.abs(np.fft.rfft(mono * self.window))
        # Normalise so a full-scale sine lands near 1.0 regardless of block
        # size, which keeps the renderers free of magic constants.
        spectrum /= (BLOCK * 0.25)

        bands = []
        for a, b in self.slices:
            mag = float(spectrum[a:b].max()) if b > a else 0.0
            # dB, then mapped to 0..1 over a 60 dB window: a linear magnitude
            # bar is nearly always in the bottom tenth and looks broken.
            db = _dbfs(mag)
            bands.append(max(0.0, min(1.0, (db + 60.0) / 60.0)))

        return {
            "rms": [_dbfs(rms_l), _dbfs(rms_r)],
            "peak": [_dbfs(peak_l), _dbfs(peak_r)],
            "bands": bands,
            "silent": max(rms_l, rms_r) < 10 ** (SILENCE_DBFS / 20.0),
        }


class Visualizer:
    def __init__(self):
        self._bands = int(cfg("visualizer", "bands", default=DEFAULT_BANDS) or DEFAULT_BANDS)
        # Where 0 VU sits, in dBFS. Programme level varies enough between
        # masters that this is a taste setting rather than a constant.
        self._vu_ref = float(cfg("visualizer", "vu_reference_dbfs", default=-14.0))
        self._analyzer = Analyzer(self._bands)
        self._clients: set[web.WebSocketResponse] = set()
        self._mode = self._load_mode()
        self._runner: web.AppRunner | None = None
        self._tasks = BackgroundTaskSet(log, label="visualizer")

    # ── Mode persistence ──

    @staticmethod
    def _load_mode() -> str:
        try:
            with open(STATE_FILE) as f:
                mode = json.load(f).get("mode")
                if mode in MODES:
                    return mode
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return MODES[0]

    def _save_mode(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({"mode": self._mode}, f)
        except OSError as e:
            log.warning("Could not persist visualizer mode: %s", e)

    def _set_mode(self, mode: str) -> bool:
        if mode not in MODES:
            return False
        if mode != self._mode:
            self._mode = mode
            self._save_mode()
            log.info("Visualizer mode: %s", mode)
        return True

    def _step_mode(self, delta: int) -> str:
        self._set_mode(MODES[(MODES.index(self._mode) + delta) % len(MODES)])
        return self._mode

    # ── Capture ──

    async def _capture_loop(self):
        """Read the tone sink's monitor forever, restarting pw-record if it dies."""
        while True:
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pw-record", "--target", TONE_SINK, "--rate", str(RATE),
                    "--channels", str(CHANNELS), "--format", "s16", "-",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                log.info("Capturing %s at %d Hz", TONE_SINK, RATE)
                while True:
                    raw = await proc.stdout.readexactly(BLOCK_BYTES)
                    if self._clients:
                        await self._broadcast(self._analyzer.analyse(raw))
            except asyncio.IncompleteReadError:
                log.warning("Capture stream ended — restarting")
            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.terminate()
                raise
            except Exception as e:
                log.warning("Capture error: %s", e)
            finally:
                if proc and proc.returncode is None:
                    proc.terminate()
            await asyncio.sleep(RESTART_DELAY)

    async def _broadcast(self, frame: dict):
        if not self._clients:
            return
        payload = json.dumps(frame, separators=(",", ":"))
        dead = []
        for ws in self._clients:
            try:
                await ws.send_str(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    # ── HTTP / WebSocket ──

    async def _handle_ws(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._clients.add(ws)
        log.info("Visualizer client connected (%d total)", len(self._clients))
        try:
            await ws.send_str(json.dumps({
                "mode": self._mode,
                "bands": self._bands,
                "vu_reference_dbfs": self._vu_ref,
            }))
            async for _ in ws:
                pass          # clients only listen; mode changes go over HTTP
        finally:
            self._clients.discard(ws)
            log.info("Visualizer client gone (%d left)", len(self._clients))
        return ws

    async def _handle_mode(self, request):
        if request.method == "OPTIONS":
            return web.Response(headers=self._cors())
        if request.method == "POST":
            try:
                data = await request.json()
            except Exception:
                data = {}
            if "step" in data:
                self._step_mode(1 if int(data["step"]) >= 0 else -1)
            elif not self._set_mode(str(data.get("mode", ""))):
                return web.json_response(
                    {"error": "unknown mode", "modes": list(MODES)},
                    status=400, headers=self._cors())
        return web.json_response(
            {"mode": self._mode, "modes": list(MODES), "bands": self._bands},
            headers=self._cors())

    async def _handle_nowplaying(self, request):
        """Title and artist from the router, elapsed time from go-librespot."""
        if request.method == "OPTIONS":
            return web.Response(headers=self._cors())

        out = {"title": "", "artist": "", "position_ms": None, "duration_ms": None}
        try:
            async with aiohttp.ClientSession() as s:
                try:
                    async with s.get(ROUTER_MEDIA, timeout=3) as r:
                        if r.status == 200:
                            d = await r.json()
                            out["title"] = d.get("title") or ""
                            out["artist"] = d.get("artist") or ""
                            dur = d.get("duration")
                            if isinstance(dur, (int, float)) and dur > 0:
                                out["duration_ms"] = int(dur)
                except Exception:
                    pass
                try:
                    async with s.get(LIBRESPOT_STATUS, timeout=3) as r:
                        if r.status == 200:
                            d = await r.json()
                            track = d.get("track") or {}
                            pos = track.get("position")
                            if isinstance(pos, (int, float)):
                                out["position_ms"] = int(pos)
                                out["paused"] = bool(d.get("paused"))
                            dur = track.get("duration")
                            if not out["duration_ms"] and isinstance(dur, (int, float)):
                                out["duration_ms"] = int(dur)
                except Exception:
                    pass
        except Exception as e:
            log.debug("now-playing lookup failed: %s", e)

        return web.json_response(out, headers=self._cors())

    @staticmethod
    def _cors():
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

    async def start(self):
        app = web.Application()
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/mode", self._handle_mode)
        app.router.add_post("/mode", self._handle_mode)
        app.router.add_options("/mode", self._handle_mode)
        app.router.add_get("/nowplaying", self._handle_nowplaying)
        app.router.add_options("/nowplaying", self._handle_nowplaying)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", VISUALIZER_PORT).start()
        log.info("HTTP/WS API on port %d (mode=%s, bands=%d)",
                 VISUALIZER_PORT, self._mode, self._bands)

        self._tasks.spawn(watchdog_loop(), name="watchdog")
        self._tasks.spawn(self._capture_loop(), name="capture")

    async def stop(self):
        await self._tasks.cancel_all()
        for ws in list(self._clients):
            await ws.close()
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
    asyncio.run(Visualizer().run())
