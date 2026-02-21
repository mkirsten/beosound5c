# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""
PlayerBase — shared plumbing for BeoSound 5c player services.

A player monitors an external playback device (Sonos, BlueSound, etc.) and
exposes both a WebSocket feed for UI media updates and HTTP endpoints for
playback commands from sources.

Subclass contract:

    class MyPlayer(PlayerBase):
        id   = "sonos"
        name = "Sonos"
        port = 8766

        async def play(self, uri=None, url=None) -> bool: ...
        async def pause(self) -> bool: ...
        async def resume(self) -> bool: ...
        async def next_track(self) -> bool: ...
        async def prev_track(self) -> bool: ...
        async def stop(self) -> bool: ...
        async def get_capabilities(self) -> list: ... # ["spotify", "url_stream", ...]

Built-in (no override needed):
    get_state()                     — returns self._current_playback_state
    on_ws_connect()                 — sends cached media data to new client
    trigger_wake()                  — wake screen via input service
    report_volume_to_router(vol)    — report volume with dedup
    notify_router_playback_override — tell router about external media change

Optional overrides:
    on_start()   — called after HTTP server is up (session + watchdog already running)
    on_stop()    — called during shutdown (before monitor/session cleanup)
"""

import asyncio
import base64
import json
import logging
import signal
import sys
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import aiohttp
from aiohttp import web

from .config import cfg
from .watchdog import watchdog_loop

try:
    from PIL import Image
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False

log = logging.getLogger(__name__)

# Artwork defaults — subclasses can override via class attributes
MAX_ARTWORK_SIZE = 500 * 1024  # 500 KB limit for JPEG output
ARTWORK_CACHE_SIZE = 100       # number of artworks to cache

# Shared thread pool for CPU-bound image processing
_artwork_executor = ThreadPoolExecutor(max_workers=2)

# Common service URLs
INPUT_WAKE_URL = "http://localhost:8767/webhook"
ROUTER_VOLUME_REPORT_URL = "http://localhost:8770/router/volume/report"
ROUTER_PLAYBACK_OVERRIDE_URL = "http://localhost:8770/router/playback_override"


class ArtworkCache:
    """Simple LRU cache for artwork data (URL -> base64 dict)."""

    def __init__(self, max_size=100):
        self.max_size = max_size
        self._cache: OrderedDict[str, dict] = OrderedDict()

    def get(self, url: str):
        if url in self._cache:
            self._cache.move_to_end(url)
            return self._cache[url]
        return None

    def put(self, url: str, data: dict):
        if url in self._cache:
            self._cache.move_to_end(url)
        else:
            if len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[url] = data

    def __contains__(self, url: str):
        return url in self._cache

    def __len__(self):
        return len(self._cache)


def _process_image(image_bytes: bytes) -> dict | None:
    """Convert raw image bytes to a compressed JPEG base64 dict.

    Runs in a thread pool (CPU-bound).  Returns ``{'base64': str, 'size': (w,h)}``
    or None on failure.  Requires Pillow.
    """
    if not _HAS_PILLOW:
        log.warning("Pillow not installed — artwork processing disabled")
        return None
    try:
        image = Image.open(BytesIO(image_bytes))
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGB")

        buf = BytesIO()
        image.save(buf, "JPEG", quality=85)
        if buf.tell() > MAX_ARTWORK_SIZE:
            buf = BytesIO()
            image.save(buf, "JPEG", quality=60)

        buf.seek(0)
        return {
            "base64": base64.b64encode(buf.getvalue()).decode("utf-8"),
            "size": image.size,
        }
    except Exception as e:
        log.warning("Error processing image: %s", e)
        return None


class PlayerBase:
    # ── Subclass must set these ──
    id: str = ""
    name: str = ""
    port: int = 8766

    def __init__(self):
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._runner: web.AppRunner | None = None
        self._artwork_cache = ArtworkCache(max_size=ARTWORK_CACHE_SIZE)
        # Common state — subclasses can add more in their own __init__
        self.running: bool = False
        self._http_session: aiohttp.ClientSession | None = None
        self._monitor_task: asyncio.Task | None = None
        self._current_playback_state: str | None = None
        self._cached_media_data: dict | None = None
        self._last_reported_volume: int | None = None

    # ── Abstract methods (subclass must implement) ──

    async def play(self, uri=None, url=None, track_uri=None) -> bool:
        """Start playback. uri = Spotify/share link, url = generic stream.
        track_uri = Spotify track URI to start at within a playlist/album."""
        raise NotImplementedError

    async def pause(self) -> bool:
        raise NotImplementedError

    async def resume(self) -> bool:
        raise NotImplementedError

    async def next_track(self) -> bool:
        raise NotImplementedError

    async def prev_track(self) -> bool:
        raise NotImplementedError

    async def stop(self) -> bool:
        raise NotImplementedError

    async def get_state(self) -> str:
        """Return "playing", "paused", or "stopped"."""
        return self._current_playback_state or "stopped"

    async def get_capabilities(self) -> list:
        """Return list of supported content types, e.g. ["spotify", "url_stream"]."""
        raise NotImplementedError

    # ── Artwork helpers ──

    async def fetch_artwork(self, url: str, session: aiohttp.ClientSession | None = None):
        """Fetch artwork from *url*, return ``{'base64': ..., 'size': ...}`` or None.

        Results are cached in ``self._artwork_cache``.  If *session* is None a
        temporary one is created (and closed).
        """
        cached = self._artwork_cache.get(url)
        if cached is not None:
            log.debug("Artwork cache hit for %s", url)
            return cached

        log.debug("Artwork cache miss, fetching: %s", url)
        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                image_bytes = await resp.read()

            if not image_bytes:
                log.warning("Artwork URL returned 0 bytes")
                return None

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _artwork_executor, _process_image, image_bytes)

            if result:
                self._artwork_cache.put(url, result)
                log.info("Cached artwork for %s (%d items in cache)",
                         url, len(self._artwork_cache))
            return result

        except aiohttp.ClientError as e:
            log.warning("Error fetching artwork: %s", e)
            return None
        except Exception as e:
            log.warning("Error processing artwork: %s", e)
            return None
        finally:
            if close_session:
                await session.close()

    # ── WebSocket broadcasting ──

    async def broadcast_media_update(self, media_data: dict, reason: str = "update"):
        """Push a media_update to all connected WebSocket clients."""
        if not self._ws_clients:
            return

        message = json.dumps({
            "type": "media_update",
            "reason": reason,
            "data": media_data,
        })

        disconnected = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(message)
            except Exception:
                disconnected.add(ws)

        self._ws_clients -= disconnected

        if self._ws_clients:
            log.info("Broadcast media update to %d clients: %s",
                     len(self._ws_clients), reason)

    async def send_media_update(self, ws: web.WebSocketResponse,
                                media_data: dict, reason: str):
        """Send a media_update to a single client."""
        try:
            await ws.send_json({
                "type": "media_update",
                "reason": reason,
                "data": media_data,
            })
        except Exception as e:
            log.error("Error sending media update: %s", e)

    # ── HTTP + WebSocket server ──

    async def start(self):
        """Create the aiohttp app with routes + WebSocket, start listening."""
        # Type guard — exit cleanly if config selects a different player
        configured = cfg("player", "type", default="")
        if configured and configured != self.id:
            log.info("Config player.type=%s but this is %s — exiting",
                     configured, self.id)
            # Tell systemd we started and are stopping (avoids 'protocol' failure
            # with Type=notify when we exit before sending READY=1)
            from .watchdog import sd_notify
            sd_notify("READY=1\nSTATUS=Wrong player type, exiting")
            sd_notify("STOPPING=1")
            sys.exit(0)

        self.running = True
        self._http_session = aiohttp.ClientSession()

        app = web.Application()

        # WebSocket endpoint for UI media push
        app.router.add_get("/ws", self._handle_ws)

        # Player command endpoints
        app.router.add_post("/player/play", self._handle_play)
        app.router.add_post("/player/pause", self._handle_pause)
        app.router.add_post("/player/resume", self._handle_resume)
        app.router.add_post("/player/next", self._handle_next)
        app.router.add_post("/player/prev", self._handle_prev)
        app.router.add_post("/player/stop", self._handle_stop)
        app.router.add_get("/player/state", self._handle_state)
        app.router.add_get("/player/capabilities", self._handle_capabilities)
        app.router.add_get("/player/status", self._handle_status)

        # Let subclass add extra routes
        self.add_routes(app)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        log.info("Player %s: HTTP + WebSocket on port %d", self.name, self.port)

        await self.on_start()

        # Start systemd watchdog heartbeat (after on_start so subclass is ready)
        asyncio.create_task(watchdog_loop())

    async def run(self):
        """Convenience entry-point: start + wait for signal + stop."""
        await self.start()
        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_event.set)
        try:
            await stop_event.wait()
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Clean up resources."""
        self.running = False
        await self.on_stop()

        # Cancel monitor task
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except (asyncio.CancelledError, Exception):
                pass
            self._monitor_task = None

        # Close HTTP session
        if self._http_session:
            await self._http_session.close()
            self._http_session = None

        # Close all WebSocket connections
        for ws in list(self._ws_clients):
            await ws.close()
        self._ws_clients.clear()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    # ── WebSocket handler ──

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self._ws_clients.add(ws)
        log.info("WebSocket client connected (%d total)", len(self._ws_clients))

        try:
            # Let subclass send initial data to new client
            await self.on_ws_connect(ws)

            # Keep connection alive (push-only — no incoming message handling)
            async for msg in ws:
                pass  # ignore client messages
        finally:
            self._ws_clients.discard(ws)
            log.info("WebSocket client disconnected (%d remaining)",
                     len(self._ws_clients))

        return ws

    # ── HTTP route handlers ──

    def _cors_headers(self):
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

    async def _handle_play(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            data = {}
        ok = await self.play(
            uri=data.get("uri"), url=data.get("url"),
            track_uri=data.get("track_uri"))
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_pause(self, request: web.Request) -> web.Response:
        ok = await self.pause()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_resume(self, request: web.Request) -> web.Response:
        ok = await self.resume()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_next(self, request: web.Request) -> web.Response:
        ok = await self.next_track()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_prev(self, request: web.Request) -> web.Response:
        ok = await self.prev_track()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_stop(self, request: web.Request) -> web.Response:
        ok = await self.stop()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_state(self, request: web.Request) -> web.Response:
        state = await self.get_state()
        return web.json_response(
            {"state": state},
            headers=self._cors_headers())

    async def _handle_capabilities(self, request: web.Request) -> web.Response:
        caps = await self.get_capabilities()
        return web.json_response(
            {"capabilities": caps},
            headers=self._cors_headers())

    async def _handle_status(self, request: web.Request) -> web.Response:
        status = await self.get_status()
        return web.json_response(status, headers=self._cors_headers())

    async def get_status(self) -> dict:
        """Return player status. Override in subclass for richer data."""
        return {
            "player": self.id,
            "name": self.name,
            "ws_clients": len(self._ws_clients),
        }

    # ── Subclass hooks ──

    async def on_start(self):
        """Called after HTTP server is up."""

    async def on_stop(self):
        """Called during shutdown."""

    async def on_ws_connect(self, ws: web.WebSocketResponse):
        """Called when a new WebSocket client connects. Send initial state.

        Default sends cached media data if available.  Override in subclass
        for richer behaviour (e.g. fetch fresh data on connect).
        """
        if self._cached_media_data:
            await self.send_media_update(ws, self._cached_media_data, "client_connect")

    def add_routes(self, app: web.Application):
        """Add extra aiohttp routes to the app."""

    # ── Common helpers (used by subclass monitoring loops) ──

    async def trigger_wake(self):
        """Trigger screen wake via input service webhook."""
        try:
            async with self._http_session.post(
                INPUT_WAKE_URL,
                json={"command": "wake", "params": {"page": "now_playing"}},
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status == 200:
                    log.info("Triggered screen wake")
                else:
                    log.warning("Wake trigger returned status %d", resp.status)
        except Exception as e:
            log.warning("Could not trigger wake: %s", e)

    async def report_volume_to_router(self, volume: int):
        """Report a volume change to the router so the UI arc stays in sync.

        Deduplicates — only sends if volume actually changed.
        """
        if volume == self._last_reported_volume:
            return
        self._last_reported_volume = volume
        try:
            async with self._http_session.post(
                ROUTER_VOLUME_REPORT_URL,
                json={"volume": volume},
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status == 200:
                    log.info("Reported volume %d%% to router", volume)
                else:
                    log.debug("Router volume report returned %d", resp.status)
        except Exception as e:
            log.debug("Could not report volume to router: %s", e)

    async def notify_router_playback_override(self, force: bool = False):
        """Notify the router that media changed externally on the player."""
        try:
            async with self._http_session.post(
                ROUTER_PLAYBACK_OVERRIDE_URL,
                json={"force": force},
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("cleared"):
                        log.info("Router active source cleared (playback override)")
                    else:
                        log.debug("Playback override not applied: %s",
                                  result.get("reason"))
        except Exception as e:
            log.debug("Could not notify router of playback override: %s", e)
