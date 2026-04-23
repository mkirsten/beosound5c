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
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import aiohttp
from aiohttp import web

from .background_tasks import BackgroundTaskSet
from .config import cfg
from .endpoints import (
    INPUT_WEBHOOK,
    ROUTER_MEDIA,
    ROUTER_OUTPUT_ON,
    ROUTER_PLAYBACK_OVERRIDE,
    ROUTER_VOLUME_REPORT,
)
from .loop_monitor import LoopMonitor
from .http_utils import CORS_HEADERS
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

# Common service URLs — back-compat aliases; new code should use
# services.lib.endpoints directly.
INPUT_WAKE_URL = INPUT_WEBHOOK
ROUTER_MEDIA_URL = ROUTER_MEDIA
ROUTER_VOLUME_REPORT_URL = ROUTER_VOLUME_REPORT
ROUTER_PLAYBACK_OVERRIDE_URL = ROUTER_PLAYBACK_OVERRIDE
ROUTER_OUTPUT_ON_URL = ROUTER_OUTPUT_ON


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
        elif len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        # Always assign — a duplicate put must overwrite, not silently
        # discard the new value.  (Spotify CDN URLs occasionally rotate
        # bytes behind the same URL.)
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
        self._last_internal_command: float = 0.0  # monotonic timestamp
        self._latest_action_ts: float = 0.0  # action timestamp for race prevention
        self._background_tasks = BackgroundTaskSet(log, label=f"{self.id or 'player'}")

    # ── Background task tracking ──

    def _spawn(self, coro, *, name: str | None = None) -> asyncio.Task:
        """Launch a fire-and-forget task with automatic lifecycle tracking."""
        return self._background_tasks.spawn(coro, name=name)

    # ── Abstract methods (subclass must implement) ──

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None) -> bool:
        """Start playback. uri = Spotify/share link, url = generic stream.
        track_uri = Spotify track URI to start at within a playlist/album.
        radio = treat URL as continuous radio stream (affects Sonos URI scheme).
        track_uris = list of individual track URIs to queue (for non-playlist collections)."""
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

    async def get_track_uri(self) -> str:
        """Return the URI/URL of the currently playing track, or empty string."""
        return ""

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

            loop = asyncio.get_running_loop()
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

    # ── Media broadcasting (via router) ──

    async def broadcast_media_update(self, media_data: dict, reason: str = "update"):
        """POST a media update to the router, which pushes to UI clients."""
        self._cached_media_data = media_data
        if not self._session_ready():
            log.debug("Skipping media broadcast — session not available (shutdown?)")
            return
        try:
            payload = dict(media_data)
            payload["_reason"] = reason
            if self._latest_action_ts:
                payload["_action_ts"] = self._latest_action_ts
            # Include the current track URI so the router's canvas
            # injection doesn't race back to /player/track_uri — that
            # callback can observe stale state while this POST is still
            # in flight (see sonos.py monitor loop).
            try:
                track_uri = await self.get_track_uri()
                if track_uri:
                    payload["_track_uri"] = track_uri
            except Exception as e:
                log.debug("get_track_uri failed during broadcast: %s", e)
            async with self._http_session.post(
                ROUTER_MEDIA_URL, json=payload,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    log.info("Posted media update to router: %s", reason)
                else:
                    log.warning("Router media POST returned %d", resp.status)
        except Exception as e:
            log.warning("Could not post media to router: %s", e)

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
        # Event-loop lag detector: warns when a sync call blocks the
        # loop for more than the default threshold.
        self._loop_monitor = LoopMonitor().start()

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

        app = web.Application(middlewares=[cors_middleware])

        # WebSocket endpoint for UI media push
        app.router.add_get("/ws", self._handle_ws)

        # Player command endpoints
        app.router.add_post("/player/play", self._handle_play)
        app.router.add_post("/player/pause", self._handle_pause)
        app.router.add_post("/player/resume", self._handle_resume)
        app.router.add_post("/player/next", self._handle_next)
        app.router.add_post("/player/prev", self._handle_prev)
        app.router.add_post("/player/stop", self._handle_stop)
        app.router.add_post("/player/toggle", self._handle_toggle)
        app.router.add_get("/player/state", self._handle_state)
        app.router.add_get("/player/track_uri", self._handle_track_uri)
        app.router.add_get("/player/capabilities", self._handle_capabilities)
        app.router.add_get("/player/status", self._handle_status)
        app.router.add_get("/player/spotify-status", self._handle_spotify_status)
        app.router.add_post("/player/announce", self._handle_announce)
        app.router.add_get("/player/media", self._handle_media)
        app.router.add_get("/player/queue", self._handle_queue)
        app.router.add_post("/player/play_from_queue", self._handle_play_from_queue)

        # Let subclass add extra routes
        self.add_routes(app)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        log.info("Player %s: HTTP + WebSocket on port %d", self.name, self.port)

        # Start systemd watchdog heartbeat before on_start — sends READY=1
        # immediately so Type=notify doesn't fail if on_start blocks/crashes
        asyncio.create_task(watchdog_loop())

        await self.on_start()

    async def run(self):
        """Convenience entry-point: start + wait for signal + stop."""
        await self.start()
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
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

        # Cancel any tracked background tasks spawned by subclasses via _spawn()
        await self._background_tasks.cancel_all()

        if getattr(self, "_loop_monitor", None) is not None:
            await self._loop_monitor.stop()
            self._loop_monitor = None

        # Close HTTP session
        if self._http_session:
            await self._http_session.close()
            self._http_session = None

        # Close all WebSocket connections
        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception as e:
                log.debug("Error closing WS during shutdown: %s", e)
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
        return CORS_HEADERS

    def _stamp_command(self):
        """Record that a command was received from a BS5c source/router."""
        self._last_internal_command = time.monotonic()

    def _update_action_ts(self, request_data: dict):
        """Update action timestamp if provided (prevents stale media rejections)."""
        action_ts = request_data.get("action_ts", 0)
        if action_ts and action_ts >= self._latest_action_ts:
            self._latest_action_ts = action_ts

    def seconds_since_command(self) -> float:
        """Seconds since the last internal command (play/next/prev/etc.)."""
        if self._last_internal_command == 0.0:
            return float("inf")
        return time.monotonic() - self._last_internal_command

    async def _handle_play(self, request: web.Request) -> web.Response:
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            data = {}
        # Timestamp gating: reject stale play commands
        action_ts = data.get("action_ts", 0)
        if action_ts and 0 < self._latest_action_ts - action_ts < 3.0:
            log.warning("Dropped stale play (ts=%.3f < latest=%.3f)",
                        action_ts, self._latest_action_ts)
            return web.json_response(
                {"status": "dropped", "reason": "stale"},
                headers=self._cors_headers())
        if action_ts:
            self._latest_action_ts = action_ts
        ok = await self.play(
            uri=data.get("uri"), url=data.get("url"),
            track_uri=data.get("track_uri"), meta=data.get("meta"),
            radio=data.get("radio", False),
            track_uris=data.get("track_uris"))
        # Re-stamp after play completes — SoCo calls can take 5+ seconds,
        # and the monitor suppression window starts from the last stamp.
        self._stamp_command()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_pause(self, request: web.Request) -> web.Response:
        self._stamp_command()
        ok = await self.pause()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_resume(self, request: web.Request) -> web.Response:
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            data = {}
        self._update_action_ts(data)
        ok = await self.resume()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_next(self, request: web.Request) -> web.Response:
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            data = {}
        self._update_action_ts(data)
        ok = await self.next_track()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_prev(self, request: web.Request) -> web.Response:
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            data = {}
        self._update_action_ts(data)
        ok = await self.prev_track()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_stop(self, request: web.Request) -> web.Response:
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            data = {}
        # Reject stale stop — prevents a deactivated source from killing
        # playback that a newer source already started.
        action_ts = data.get("action_ts", 0)
        if action_ts and action_ts < self._latest_action_ts:
            log.warning("Dropped stale stop (ts=%.3f < latest=%.3f)",
                        action_ts, self._latest_action_ts)
            return web.json_response(
                {"status": "dropped", "reason": "stale"},
                headers=self._cors_headers())
        ok = await self.stop()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_announce(self, request: web.Request) -> web.Response:
        """TTS announce current track title + artist, with volume ducking."""
        # Prefer media state from request body (router sends its authoritative
        # _media_state), fall back to player's own cache.
        try:
            body = await request.json()
        except Exception:
            body = None
        media = body if body and body.get("title") else self._cached_media_data
        # When the body carries an explicit state (router's authoritative
        # view), trust it — CD plays via mpv directly, so local player's
        # own _current_playback_state stays "stopped" even while CD is
        # playing.
        state = (media.get("state") if media else None) or self._current_playback_state
        if not media or state != "playing":
            return web.json_response(
                {"status": "skipped", "reason": "not playing"},
                headers=self._cors_headers())
        title = media.get("title", "")
        artist = media.get("artist", "")
        if not title:
            return web.json_response(
                {"status": "skipped", "reason": "no title"},
                headers=self._cors_headers())
        text = f"{title}, by {artist}" if artist else title
        self._spawn(self._announce_with_duck(text), name="announce_with_duck")
        return web.json_response({"status": "ok"}, headers=self._cors_headers())

    async def _announce_with_duck(self, text: str):
        """Duck playback volume, play TTS, restore volume."""
        from lib.tts import tts_announce
        await self.fade_volume(60, duration=0.5)
        await tts_announce(text)
        await self.fade_volume(100, duration=0.8)

    async def fade_volume(self, target: float, duration: float = 0.5):
        """Fade player volume to target (0-100). Override in subclass."""

    async def _handle_media(self, request: web.Request) -> web.Response:
        """GET /player/media — return cached media data (for router recovery)."""
        if self._cached_media_data and self._current_playback_state in ("playing", "paused"):
            return web.json_response(self._cached_media_data,
                                     headers=self._cors_headers())
        return web.json_response({}, headers=self._cors_headers())

    async def _handle_toggle(self, request: web.Request) -> web.Response:
        self._stamp_command()
        if self._current_playback_state == "playing":
            ok = await self.pause()
        else:
            ok = await self.resume()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_state(self, request: web.Request) -> web.Response:
        state = await self.get_state()
        return web.json_response(
            {"state": state},
            headers=self._cors_headers())

    async def _handle_track_uri(self, request: web.Request) -> web.Response:
        uri = await self.get_track_uri()
        return web.json_response(
            {"track_uri": uri},
            headers=self._cors_headers())

    async def _handle_capabilities(self, request: web.Request) -> web.Response:
        caps = await self.get_capabilities()
        return web.json_response(
            {"capabilities": caps},
            headers=self._cors_headers())

    async def _handle_status(self, request: web.Request) -> web.Response:
        status = await self.get_status()
        return web.json_response(status, headers=self._cors_headers())

    async def _handle_spotify_status(self, request: web.Request) -> web.Response:
        status = await self.get_spotify_status()
        return web.json_response(status, headers=self._cors_headers())

    async def get_spotify_status(self) -> dict:
        """Return Spotify Connect status. Override in local player."""
        return {"available": False}

    async def get_status(self) -> dict:
        """Return player status. Override in subclass for richer data."""
        return {
            "player": self.id,
            "name": self.name,
            "ws_clients": len(self._ws_clients),
            "latest_action_ts": self._latest_action_ts,
        }

    # ── Queue support ──

    async def get_queue(self, start=0, max_items=50) -> dict:
        """Return the playback queue. Override in subclass for real queue data."""
        return {"tracks": [], "current_index": -1, "total": 0}

    async def play_from_queue(self, position: int) -> bool:
        """Play a specific position in the queue. Override in subclass."""
        return False

    async def _handle_queue(self, request: web.Request) -> web.Response:
        start = int(request.query.get("start", "0"))
        max_items = int(request.query.get("max_items", "50"))
        result = await self.get_queue(start, max_items)
        return web.json_response(result, headers=self._cors_headers())

    async def _handle_play_from_queue(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            data = {}
        position = data.get("position", 0)
        self._stamp_command()
        ok = await self.play_from_queue(position)
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    # ── Subclass hooks ──

    async def on_start(self):
        """Called after HTTP server is up."""

    async def on_stop(self):
        """Called during shutdown."""

    async def on_ws_connect(self, ws: web.WebSocketResponse):
        """Called when a new WebSocket client connects.

        Media updates now flow through the router — this is a no-op by default.
        """

    def add_routes(self, app: web.Application):
        """Add extra aiohttp routes to the app."""

    # ── Common helpers (used by subclass monitoring loops) ──

    def _session_ready(self) -> bool:
        """True if the HTTP session is usable (not None and not closed)."""
        return self._http_session is not None and not self._http_session.closed

    async def trigger_wake(self):
        """Trigger screen wake via input service webhook."""
        if not self._session_ready():
            return
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

    async def trigger_output_on(self):
        """Power on the audio output (speakers) via the router."""
        if not self._session_ready():
            return
        try:
            async with self._http_session.post(
                ROUTER_OUTPUT_ON_URL,
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status == 200:
                    log.info("Triggered output power on")
                else:
                    log.warning("Output power on returned status %d", resp.status)
        except Exception as e:
            log.warning("Could not trigger output power on: %s", e)

    async def report_volume_to_router(self, volume: int):
        """Report a volume change to the router so the UI arc stays in sync.

        Deduplicates — only sends if volume actually changed.
        """
        if volume == self._last_reported_volume:
            return
        self._last_reported_volume = volume
        if not self._session_ready():
            return
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

    async def notify_router_playback_override(self, force: bool = False,
                                                push_idle: bool = True):
        """Notify the router that media changed externally on the player.

        ``push_idle=False`` suppresses the router's idle media broadcast
        when the caller is about to push real media right after (used by
        the external-start eager broadcast path — see
        ``SonosPlayer._on_playback_started``).
        """
        action_ts = time.monotonic()
        self._latest_action_ts = max(self._latest_action_ts, action_ts)
        if not self._session_ready():
            return
        try:
            async with self._http_session.post(
                ROUTER_PLAYBACK_OVERRIDE_URL,
                json={"force": force, "action_ts": action_ts,
                      "push_idle": push_idle},
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
