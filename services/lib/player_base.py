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
from .active_target import clear_active_target, is_overridden, save_active_target
from .endpoints import (
    INPUT_WEBHOOK,
    ROUTER_MEDIA,
    ROUTER_OUTPUT_ON,
    ROUTER_PLAYBACK_OVERRIDE,
    ROUTER_SOURCE,
    ROUTER_TARGET_RELOAD,
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

    async def set_shuffle(self, enabled: bool) -> bool:
        """Enable/disable shuffle on the player. Override in subclasses
        that support it; default is no-op."""
        return False

    async def play_track_radio(self, track_uri) -> bool:
        """Start a radio station seeded by *track_uri* (e.g. Spotify track
        radio). Override in subclasses that support it; default is no-op."""
        return False

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
        app.router.add_post("/player/play_track_radio", self._handle_play_track_radio)
        app.router.add_post("/player/shuffle", self._handle_shuffle)

        # Let subclass add extra routes
        self.add_routes(app)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        log.info("Player %s: HTTP + WebSocket on port %d", self.name, self.port)

        # Start systemd watchdog heartbeat before on_start — sends READY=1
        # immediately so Type=notify doesn't fail if on_start blocks/crashes.
        # Observe it: a silently-dead heartbeat gets the whole service killed
        # by WatchdogSec 60s later, so at least leave a trace.
        self._watchdog_task = asyncio.create_task(watchdog_loop())
        self._watchdog_task.add_done_callback(
            lambda t: t.cancelled() or t.exception() is None or log.error(
                "Watchdog heartbeat task died — systemd will restart the "
                "service in WatchdogSec: %s", t.exception()))

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
        # Timestamp gating: reject stale play commands.
        # `_latest_action_ts` is per-process and shared across sources, so a
        # strict `<` comparison would drop legitimate plays from a different
        # source whenever another source had run more recently. A 3-second
        # window still deduplicates rapid double-taps within one interaction.
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

    async def _handle_play_track_radio(self, request: web.Request) -> web.Response:
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            data = {}
        action_ts = data.get("action_ts", 0)
        if action_ts and 0 < self._latest_action_ts - action_ts < 3.0:
            log.warning("Dropped stale play_track_radio (ts=%.3f < latest=%.3f)",
                        action_ts, self._latest_action_ts)
            return web.json_response(
                {"status": "dropped", "reason": "stale"},
                headers=self._cors_headers())
        if action_ts:
            self._latest_action_ts = action_ts
        track_uri = data.get("track_uri")
        if not track_uri:
            return web.json_response(
                {"status": "error", "reason": "missing track_uri"},
                headers=self._cors_headers())
        ok = await self.play_track_radio(track_uri=track_uri)
        self._stamp_command()
        return web.json_response(
            {"status": "ok" if ok else "error"},
            headers=self._cors_headers())

    async def _handle_shuffle(self, request: web.Request) -> web.Response:
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            data = {}
        enabled = bool(data.get("enabled", False))
        ok = await self.set_shuffle(enabled)
        return web.json_response(
            {"status": "ok" if ok else "error", "shuffle": enabled},
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
            # Whether "Play here" also moves volume control. False when the
            # volume adapter is a different platform (e.g. beolab5/powerlink):
            # audio follows the target room but the wheel keeps driving the
            # configured output — the UI warns before retargeting.
            "volume_follows_target": self._volume_follows_target(),
            # The configured "home" room, and whether a runtime override is
            # active — the SPEAKERS view marks HOME and offers a reset.
            "configured_ip": str(cfg("player", "ip", default="") or ""),
            "overridden": is_overridden(),
        }

    def _volume_follows_target(self) -> bool:
        try:
            from .volume_adapters import infer_volume_type
            return infer_volume_type() == self.id
        except Exception:
            return True

    # ── Queue support ──

    async def get_queue(self, start=0, max_items=50) -> dict:
        """Return the playback queue. Override in subclass for real queue data."""
        return {"tracks": [], "current_index": -1, "total": 0}

    async def play_from_queue(self, position: int) -> bool:
        """Play a specific position in the queue. Override in subclass."""
        return False

    async def _handle_queue(self, request: web.Request) -> web.Response:
        try:
            start = int(request.query.get("start", "0"))
            max_items = int(request.query.get("max_items", "50"))
        except ValueError:
            return web.json_response(
                {"error": "start and max_items must be integers"},
                status=400, headers=self._cors_headers())
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
        """Add extra aiohttp routes to the app.

        Grouping-capable players (``supports_grouping()`` → True) get the
        shared SPEAKERS endpoints registered here; subclasses that override
        this method must call ``super().add_routes(app)`` to keep them.
        """
        if self.supports_grouping():
            app.router.add_get("/player/network", self._handle_group_network)
            app.router.add_post("/player/join", self._handle_group_join)
            app.router.add_post("/player/unjoin", self._handle_group_unjoin)
            app.router.add_get("/player/resync", self._handle_group_resync)
            app.router.add_post("/player/target", self._handle_group_target)

    # ── SPEAKERS grouping capability ─────────────────────────────────────
    # A player is "grouping-capable" when the speaker platform can link rooms
    # (Sonos groups, B&O Beolink, …).  The HTTP surface, caching, source
    # registration, and join media-broadcast live here; each platform supplies
    # only the primitives below.  The web SPEAKERS view consumes these
    # endpoints by shape, so any backend that fills them works unchanged.

    SPEAKERS_SOURCE_ID = "join"      # router source id (kept as 'join' for
    SPEAKERS_SOURCE_NAME = "Speakers"  # menu-config compatibility; label reads SPEAKERS

    def supports_grouping(self) -> bool:
        """Override → True in a player that can group/target other rooms."""
        return False

    # -- primitives to override in grouping-capable subclasses --

    async def group_list_devices(self) -> list:
        """Return the SPEAKERS device list (dicts with name/ip/state/title/
        artist/album/artwork_url/group).  Push blocking work to an executor."""
        return []

    def group_resolve_name(self, name: str):
        """Map a device display name → ip, or None if unknown."""
        return None

    async def group_join(self, ip: str, media: dict) -> str:
        """Join this player into the group led by ``ip``; return joined name."""
        raise NotImplementedError

    async def group_unjoin(self) -> None:
        """Leave the current group."""
        raise NotImplementedError

    def group_peers_known(self) -> bool:
        """True when peer devices are currently known (drives visibility)."""
        return False

    async def gather_capped(self, items, coro_fn, timeout: float) -> dict:
        """Run ``coro_fn(item)`` for each item in parallel, capped at
        ``timeout`` seconds; return ``{item: result}`` for those that finished.
        Slow/unreachable peers are dropped rather than blocking the whole
        SPEAKERS list (Sonos caps discovery the same way, DEVICE_TIMEOUT)."""
        async def _wrap(item):
            return item, await coro_fn(item)

        tasks = [asyncio.ensure_future(_wrap(i)) for i in items]
        if not tasks:
            return {}
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for t in pending:
            t.cancel()
        out = {}
        for t in done:
            try:
                item, val = t.result()
            except Exception:
                continue
            out[item] = val
        return out

    async def on_target_changed(self, ip: str, name: str) -> None:
        """Re-point the running player at ``ip`` after a "Play here" retarget.

        Same-platform only (the SPEAKERS list is platform-scoped, so any
        device in it is the same type). Override per player to re-point the
        controlled device live; the base no-op suffices for players that read
        the target lazily."""

    # -- shared HTTP handlers --

    async def register_speakers_source(self, state: str = "available") -> bool:
        """Register/update the SPEAKERS source on the router."""
        if not self._session_ready():
            return False
        try:
            async with self._http_session.post(
                ROUTER_SOURCE,
                json={"id": self.SPEAKERS_SOURCE_ID, "state": state,
                      "name": self.SPEAKERS_SOURCE_NAME},
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as resp:
                log.debug("SPEAKERS source %s: HTTP %d", state, resp.status)
                return True
        except Exception as e:
            log.debug("Could not register SPEAKERS source: %s", e)
            return False

    async def _handle_group_network(self, request) -> web.Response:
        """GET /player/network — cached device list, background refresh.

        First call (no cache) blocks; later calls return the cache instantly
        and refresh in the background at most once per cooldown."""
        REFRESH_COOLDOWN = 10
        cache = getattr(self, "_network_cache", None)
        if cache is not None:
            now = time.time()
            last = getattr(self, "_network_refresh_time", 0)
            if now - last >= REFRESH_COOLDOWN:
                self._network_refresh_time = now

                async def _refresh_network():
                    # This handler owns the cache — store the full composite
                    # (current row + others) that group_list_devices builds,
                    # so the pinned current row survives past the first poll.
                    # Anti-flap: keep the last good list if a transient failure
                    # returns []; every backend returns [] on one bad poll, and
                    # an empty cache also breaks retarget validation (§2).
                    fresh = await self.group_list_devices()
                    if fresh:
                        self._network_cache = fresh

                self._spawn(_refresh_network(), name="group_network_refresh")
            return web.json_response(cache, headers=self._cors_headers())

        self._network_refresh_time = time.time()
        devices = await self.group_list_devices()
        if devices:
            self._network_cache = devices
        return web.json_response(devices, headers=self._cors_headers())

    async def _handle_group_join(self, request) -> web.Response:
        """POST /player/join — join this speaker to another group.

        Accepts {"ip": …} or {"name": …}; optional media fields are
        pre-broadcast so PLAYING shows content without waiting for a poll."""
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400,
                                     headers=self._cors_headers())

        target_ip = data.get("ip")
        target_name = data.get("name")
        if not target_ip and target_name:
            target_ip = self.group_resolve_name(target_name)
            if not target_ip:
                return web.json_response(
                    {"error": f"unknown device: {target_name}"}, status=404,
                    headers=self._cors_headers())
        if not target_ip:
            return web.json_response({"error": "ip or name required"}, status=400,
                                     headers=self._cors_headers())

        try:
            joined_name = await self.group_join(target_ip, data)
            log.info("Joined group: %s (%s)", joined_name, target_ip)
            if hasattr(self, "fetch_media_data"):
                media_data = await self.fetch_media_data()
                if media_data:
                    await self.broadcast_media_update(media_data, reason="join")
            return web.json_response({"status": "ok", "joined": joined_name},
                                     headers=self._cors_headers())
        except Exception as e:
            log.error("Join failed: %s", e)
            return web.json_response({"error": str(e)}, status=500,
                                     headers=self._cors_headers())

    async def _handle_group_unjoin(self, request) -> web.Response:
        """POST /player/unjoin — leave the current group."""
        self._stamp_command()
        try:
            await self.group_unjoin()
            log.info("Left group (unjoined)")
            return web.json_response({"status": "ok"}, headers=self._cors_headers())
        except Exception as e:
            log.error("Unjoin failed: %s", e)
            return web.json_response({"error": str(e)}, status=500,
                                     headers=self._cors_headers())

    async def _handle_group_resync(self, request) -> web.Response:
        """GET /player/resync — re-register SPEAKERS if peers are known."""
        if self.group_peers_known():
            ok = await self.register_speakers_source("available")
            return web.json_response({"resynced": ok},
                                     headers=self._cors_headers())
        return web.json_response({"resynced": False},
                                 headers=self._cors_headers())

    async def _handle_group_target(self, request) -> web.Response:
        """POST /player/target {ip,name} — retarget the BS5c to another room
        ("Play here"). Sticky. Same-platform only: the SPEAKERS list is
        platform-scoped, so any device in it is the same player type."""
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400,
                                     headers=self._cors_headers())

        ip = data.get("ip")
        name = data.get("name", "")
        if not ip and name:
            ip = self.group_resolve_name(name)
        if not ip:
            return web.json_response({"error": "ip or name required"}, status=400,
                                     headers=self._cors_headers())

        # Validate against the discovered device map — never persist an
        # arbitrary IP (the override is sticky across reboots). The configured
        # room is always allowed (it's the reset target).
        configured_ip = str(cfg("player", "ip", default="") or "")
        known = {d.get("ip") for d in (getattr(self, "_network_cache", None) or [])}
        known.add(configured_ip)
        if known - {None, ""} and ip not in known:
            return web.json_response(
                {"error": f"unknown target: {ip}"}, status=404,
                headers=self._cors_headers())

        # Targeting the configured room clears the override — the way back home.
        if ip == configured_ip:
            clear_active_target()
        else:
            save_active_target(ip, name)
        try:
            await self.on_target_changed(ip, name)
        except Exception as e:
            log.error("on_target_changed failed: %s", e)
        # Router owns the volume adapter — have it re-point at the new room.
        await self._reload_router_target()
        if hasattr(self, "fetch_media_data"):
            try:
                media_data = await self.fetch_media_data()
                if media_data:
                    await self.broadcast_media_update(media_data, reason="target")
            except Exception as e:
                log.debug("Post-target media broadcast failed: %s", e)
        log.info("Retargeted to %s (%s)", name or "?", ip)
        return web.json_response({"status": "ok", "target": name or ip},
                                 headers=self._cors_headers())

    async def _reload_router_target(self):
        """Nudge the router to re-create its volume adapter from the new target."""
        if not self._session_ready():
            return
        try:
            async with self._http_session.post(
                ROUTER_TARGET_RELOAD,
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as resp:
                log.debug("Router target reload: HTTP %d", resp.status)
        except Exception as e:
            log.debug("Router target reload failed: %s", e)

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
        if not self._session_ready():
            return
        try:
            async with self._http_session.post(
                ROUTER_VOLUME_REPORT_URL,
                json={"volume": volume},
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status == 200:
                    # Mark reported only on success — a failed report must not
                    # dedup away the retry, or the UI arc stays wrong until the
                    # volume changes to a *different* value.
                    self._last_reported_volume = volume
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
