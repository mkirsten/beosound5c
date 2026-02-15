#!/usr/bin/env python3
"""
BeoSound 5c Event Router (beo-router)

Sits between event producers (bluetooth.py, masterlink.py) and destinations
(Home Assistant, cd.py). Routes media keys to cd.py when a CD is the active
source, and forwards everything else to HA via the transport layer.

Port: 8770
"""

import asyncio
import json
import logging
import os
import signal
import sys

import aiohttp
from aiohttp import web

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.transport import Transport

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("beo-router")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROUTER_PORT = 8770
CD_COMMAND_URL = "http://localhost:8769/command"
CD_STATUS_URL = "http://localhost:8769/status"
BEOLAB5_TURN_ON_URL = "http://beolab5-controller.local/switch/beolab_5/turn_on"
BEOLAB5_VOLUME_URL = "http://beolab5-controller.local/number/beolab_5_volume/set"
BEOLAB5_MAX_VOLUME = 70  # hard cap — never send more than 70% to BeoLab 5s

# Media keys that get intercepted when CD is active
MEDIA_KEYS = {"play", "pause", "next", "prev", "stop", "go", "left", "right"}

# Map from remote action names to cd.py command names
CD_ACTION_MAP = {
    "play": "toggle",
    "pause": "toggle",
    "go": "toggle",
    "next": "next",
    "prev": "prev",
    "right": "next",
    "left": "prev",
    "stop": "stop",
}


BEOLAB5_SWITCH_URL = "http://beolab5-controller.local/switch/beolab_5"
BEOLAB5_VOLUME_READ_URL = "http://beolab5-controller.local/number/beolab_5_volume"


class EventRouter:
    def __init__(self):
        self.transport = Transport()
        self.active_source = "sonos"  # "sonos" or "cd"
        self.volume = 0  # current volume 0-100 (reported by active device)
        self.output_device = "BeoLab 5"  # hardcoded for now
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        await self.transport.start()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=2.0),
        )
        # Fetch current volume from BeoLab 5 controller
        await self._sync_beolab5_volume()
        # Restore active source if CD is playing (background — cd.py may still be starting)
        asyncio.create_task(self._sync_cd_source())
        logger.info("Router started (transport: %s, volume: %.0f%%)",
                     self.transport.mode, self.volume)

    async def _sync_beolab5_volume(self):
        """Read current volume from BeoLab 5 controller on startup."""
        if not self._session:
            return
        try:
            async with self._session.get(
                BEOLAB5_VOLUME_READ_URL,
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                data = await resp.json()
                self.volume = float(data.get("value", 0))
                logger.info("BeoLab 5 current volume: %.0f%%", self.volume)
        except Exception as e:
            logger.warning("Could not read BeoLab 5 volume: %s", e)

    async def _sync_cd_source(self):
        """Check if CD is playing on startup and restore active_source.

        Retries for up to 10 seconds since beo-cd may still be starting.
        """
        for attempt in range(5):
            await asyncio.sleep(2)
            status = await self._get_cd_status()
            if not status:
                continue
            if status.get("playback", {}).get("state") == "playing":
                self.active_source = "cd"
                logger.info("CD is playing — restored active_source to cd")
                return
            if status.get("disc_inserted"):
                # Disc present but not playing yet — keep checking
                continue
            # No disc — no point retrying
            return

    async def _is_beolab5_on(self) -> bool:
        """Check if BeoLab 5 speakers are turned on."""
        if not self._session:
            return False
        try:
            async with self._session.get(
                BEOLAB5_SWITCH_URL,
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                data = await resp.json()
                return data.get("value", False) is True
        except Exception as e:
            logger.warning("Could not check BeoLab 5 power state: %s", e)
            return False

    async def _get_cd_status(self) -> dict | None:
        """Get CD service status (disc presence, playback state, etc.)."""
        if not self._session:
            return None
        try:
            async with self._session.get(
                CD_STATUS_URL,
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.warning("Could not get CD status: %s", e)
            return None

    async def _turn_on_beolab5(self):
        """Turn on BeoLab 5 speakers via ESPHome."""
        if not self._session:
            return
        try:
            async with self._session.post(
                BEOLAB5_TURN_ON_URL,
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("BeoLab 5 turn on: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Could not turn on BeoLab 5: %s", e)

    async def stop(self):
        await self.transport.stop()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Router stopped")

    async def route_event(self, payload: dict):
        """Route an incoming event to the right destination."""
        action = payload.get("action", "")

        # Media keys go to cd.py when CD is active
        if self.active_source == "cd" and action in MEDIA_KEYS:
            cd_command = CD_ACTION_MAP.get(action, action)
            logger.info("-> cd.py: %s (from %s)", cd_command, action)
            await self._send_to_cd(cd_command)
            return

        # INFO button triggers track announce when CD is active
        if self.active_source == "cd" and action == "info":
            logger.info("-> cd.py: announce (from info)")
            await self._send_to_cd("announce")
            return

        # Digit buttons select CD track when CD is active
        digit = payload.get("digit")
        if self.active_source == "cd" and digit is not None:
            logger.info("-> cd.py: play_track %d (from digit)", digit)
            await self._send_to_cd("play_track", track=digit)
            return

        # CD button — start CD playback if disc is present
        if action == "cd":
            await self._handle_cd_button(payload)
            return

        # Everything else goes to HA
        logger.info("-> HA: %s (%s)", action, payload.get("device_type", ""))
        await self.transport.send_event(payload)

    async def set_volume(self, volume: float):
        """Set volume (0-100). Routes to the appropriate output."""
        self.volume = max(0, min(100, volume))
        if not await self._is_beolab5_on():
            logger.info("BeoLab 5 is OFF — skipping volume command (%.0f%%)", self.volume)
            return
        await self._send_volume_to_beolab5(self.volume)

    async def report_volume(self, volume: float):
        """A device reports its current volume (e.g. Sonos says 'I'm at 40%')."""
        self.volume = max(0, min(100, volume))
        logger.info("Volume reported: %.0f%%", self.volume)

    async def _send_volume_to_beolab5(self, volume: float):
        """Send volume to BeoLab 5 ESPHome controller, capped at max."""
        capped = min(volume, BEOLAB5_MAX_VOLUME)
        if volume > BEOLAB5_MAX_VOLUME:
            logger.warning("Volume %.0f%% capped to %d%% for BeoLab 5",
                           volume, BEOLAB5_MAX_VOLUME)
        if not self._session:
            logger.warning("Session not initialized")
            return
        try:
            async with self._session.post(
                BEOLAB5_VOLUME_URL,
                params={"value": str(capped)},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("-> BeoLab 5 volume: %.0f%% (HTTP %d)", capped, resp.status)
        except Exception as e:
            logger.warning("BeoLab 5 controller unreachable: %s", e)

    async def _send_to_cd(self, command: str, **kwargs):
        """Forward a command to cd.py's HTTP API."""
        if not self._session:
            logger.warning("Session not initialized")
            return
        try:
            payload = {"command": command, **kwargs}
            async with self._session.post(
                CD_COMMAND_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                logger.debug("cd.py responded: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("cd.py unreachable: %s", e)

    async def _handle_cd_button(self, payload: dict):
        """Handle CD button press from IR remote."""
        status = await self._get_cd_status()
        if not status:
            logger.info("CD status unavailable — forwarding to HA")
            await self.transport.send_event(payload)
            return

        disc_inserted = status.get("disc_inserted", False)
        if not disc_inserted:
            logger.info("No disc inserted — forwarding to HA")
            await self.transport.send_event(payload)
            return

        playback = status.get("playback", {})
        state = playback.get("state", "stopped")
        if state == "playing":
            logger.info("CD already playing — ignoring")
            return

        # Disc present and not playing — start playback
        logger.info("CD button: disc present, state=%s — starting playback", state)
        if not await self._is_beolab5_on():
            await self._turn_on_beolab5()
        await self._send_to_cd("play")
        self.active_source = "cd"


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------
router_instance = EventRouter()


async def handle_event(request: web.Request) -> web.Response:
    """POST /router/event — receive button events from bluetooth/masterlink."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"error": "invalid json"}, status=400)

    await router_instance.route_event(payload)
    return web.json_response({"status": "ok"})


async def handle_source(request: web.Request) -> web.Response:
    """POST /router/source — cd.py sets active source."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"error": "invalid json"}, status=400)

    source = data.get("source", "")
    if source not in ("cd", "sonos"):
        return web.json_response({"error": "invalid source"}, status=400)

    old = router_instance.active_source
    router_instance.active_source = source
    if old != source:
        logger.info("Source changed: %s -> %s", old, source)
        # Auto-power BeoLab 5 when switching to CD
        if source == "cd":
            if not await router_instance._is_beolab5_on():
                await router_instance._turn_on_beolab5()
    return web.json_response({"status": "ok", "active_source": source})


async def handle_volume_set(request: web.Request) -> web.Response:
    """POST /router/volume — UI sets volume."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"error": "invalid json"}, status=400)

    volume = data.get("volume")
    if volume is None or not isinstance(volume, (int, float)):
        return web.json_response({"error": "missing or invalid 'volume'"}, status=400)

    await router_instance.set_volume(float(volume))
    return web.json_response({"status": "ok", "volume": router_instance.volume})


async def handle_volume_report(request: web.Request) -> web.Response:
    """POST /router/volume/report — device reports its current volume."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"error": "invalid json"}, status=400)

    volume = data.get("volume")
    if volume is None or not isinstance(volume, (int, float)):
        return web.json_response({"error": "missing or invalid 'volume'"}, status=400)

    await router_instance.report_volume(float(volume))
    return web.json_response({"status": "ok", "volume": router_instance.volume})


async def handle_status(request: web.Request) -> web.Response:
    """GET /router/status — return current routing state."""
    return web.json_response({
        "active_source": router_instance.active_source,
        "volume": router_instance.volume,
        "output_device": router_instance.output_device,
        "transport_mode": router_instance.transport.mode,
        "media_keys": sorted(MEDIA_KEYS),
    })


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
async def on_startup(app: web.Application):
    await router_instance.start()


async def on_cleanup(app: web.Application):
    await router_instance.stop()


@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_post("/router/event", handle_event)
    app.router.add_post("/router/source", handle_source)
    app.router.add_post("/router/volume", handle_volume_set)
    app.router.add_post("/router/volume/report", handle_volume_report)
    app.router.add_get("/router/status", handle_status)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=ROUTER_PORT, print=lambda msg: logger.info(msg))
