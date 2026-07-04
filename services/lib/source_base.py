from __future__ import annotations
# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""
SourceBase — shared plumbing for BeoSound 5c source services.

Subclass contract:

    class MySource(SourceBase):
        id   = "mysource"    # router source ID
        name = "My Source"   # menu display name
        port = 8771          # HTTP port
        action_map = {       # remote action → command name
            "play": "toggle",
            "go":   "toggle",
        }

        async def handle_command(self, cmd, data) -> dict:
            '''Your playback logic.  Return a dict merged into the response.'''

Optional overrides:
    on_start()              — called after HTTP server is up
    on_stop()               — called during shutdown
    handle_status()         — return dict for GET /status
    handle_resync()         — called on GET /resync (re-register + re-broadcast)
    add_routes(app)         — add extra aiohttp routes
    handle_raw_action(a, d) — called *before* action_map lookup;
                              return (cmd, data) to override, or None to fall through
"""

import asyncio
import logging
import os
import signal
import sys
import time
from contextvars import ContextVar

from aiohttp import web, ClientSession

from .background_tasks import BackgroundTaskSet
from .config import cfg
from .correlation import set_id, HEADER as CID_HEADER
from .endpoints import (
    PLAYER_COMMAND,
    ROUTER_BROADCAST,
    ROUTER_MEDIA,
    ROUTER_SOURCE,
    source_url,
)
from .loop_monitor import LoopMonitor
from .http_utils import CORS_HEADERS
from .watchdog import watchdog_loop

log = logging.getLogger()

# Per-coroutine action timestamp — prevents concurrent request handlers
# from corrupting each other's timestamps via the shared self._action_ts.
_action_ts_ctx: ContextVar[float] = ContextVar("action_ts", default=0.0)

# Back-compat aliases for callers that still import these names.
ROUTER_SOURCE_URL = ROUTER_SOURCE
PLAYER_COMMAND_URL = PLAYER_COMMAND


class SourceBase:
    # ── Subclass must set these ──
    id: str = ""
    name: str = ""
    port: int = 0
    player: str = "local"    # "local" or "remote" — set via _detect_player()
    action_map: dict = {}
    manages_queue: bool = False  # True if source manages its own playlist/queue

    def _detect_player(self):
        """Set self.player based on configured player type."""
        player_type = cfg("player", "type", default="local")
        self.player = "local" if player_type == "local" else "remote"

    def __init__(self):
        self._http_session: ClientSession | None = None
        self._runner: web.AppRunner | None = None
        self._last_media: dict | None = None  # cached by post_media_update()
        self._registered_state: str | None = None  # last state sent to register()
        self._action_ts: float = 0.0  # monotonic timestamp from router activation
        self._background_tasks = BackgroundTaskSet(
            log, label=f"{self.id or 'source'}")

    # ── Background task tracking ──

    def _spawn(self, coro, *, name: str | None = None):
        """Launch a fire-and-forget task with lifecycle tracking.

        All tasks are cancelled during shutdown; exceptions are logged
        rather than silently swallowed.
        """
        return self._background_tasks.spawn(coro, name=name)

    # ── Router registration ──

    async def register(self, state, navigate=False, auto_power=False, _retries=5):
        """Register / update source state in the router.
        auto_power: request speaker power-on (only for user-initiated playback)."""
        self._registered_state = state
        payload = {"id": self.id, "state": state}
        if state not in ("gone",):
            payload.update({
                "name": self.name,
                "command_url": source_url(self.port, "/command"),
                "menu_preset": self.id,
                "handles": list(self.action_map.keys()),
                "player": self.player,
                "manages_queue": self.manages_queue,
            })
        if navigate:
            payload["navigate"] = True
        if auto_power:
            payload["auto_power"] = True
        ts = _action_ts_ctx.get() or self._action_ts
        if ts:
            payload["action_ts"] = ts
        for attempt in range(_retries):
            try:
                async with self._http_session.post(
                    ROUTER_SOURCE_URL, json=payload, timeout=5
                ) as resp:
                    log.info("Router source -> %s (HTTP %d)", state, resp.status)
                    return
            except Exception as e:
                if attempt < _retries - 1:
                    delay = 2 * (attempt + 1)
                    log.warning("Router unreachable (attempt %d/%d, retry in %ds): %s",
                                attempt + 1, _retries, delay, e)
                    await asyncio.sleep(delay)
                else:
                    log.warning("Router unreachable after %d attempts: %s", _retries, e)

    # ── UI broadcasting via router WS ──

    ROUTER_BROADCAST_URL = ROUTER_BROADCAST

    async def broadcast(self, event_type, data):
        """Broadcast an event to UI clients via the router's WebSocket."""
        try:
            async with self._http_session.post(
                self.ROUTER_BROADCAST_URL,
                json={"type": event_type, "data": data},
                timeout=5,
            ) as resp:
                log.info("→ router: broadcast %s (HTTP %d)", event_type, resp.status)
        except Exception as e:
            log.error("Failed to broadcast %s: %s", event_type, e)

    # ── Media update (unified path: source → router → UI) ──

    ROUTER_MEDIA_URL = ROUTER_MEDIA

    async def post_media_update(self, title="", artist="", album="",
                                artwork="", state="playing",
                                duration=0, position=0, reason="track_change",
                                back_artwork="", track_number=0,
                                canvas_url="", track_uri=""):
        """Push a media update to the router for unified PLAYING view rendering.
        All sources should use this instead of source-specific _update broadcasts
        for metadata that appears on the PLAYING view.
        Automatically caches the payload for replay on activate.

        ``track_uri`` is forwarded to the router as ``_track_uri`` (any
        format — Spotify URI, Sonos-wrapped, etc.). The router extracts
        a normalized Spotify ``track_id`` from it and stamps the
        outgoing payload, so the UI's canvas-vs-artwork cycle has a
        stable id to render-time match against. Sources that know
        their track URI (e.g. Spotify, after ``_last_track_uri`` is
        set) MUST pass it — without it, post-source-switch broadcasts
        leave ``track_id`` empty and the cycle falls through to "no
        opinion" mode."""
        payload = {
            "title": title,
            "artist": artist,
            "album": album,
            "artwork": artwork,
            "state": state,
            "duration": duration,
            "position": position,
            "_reason": reason,
            "_source_id": self.id,
        }
        if back_artwork:
            payload["back_artwork"] = back_artwork
        if track_number:
            payload["track_number"] = track_number
        if canvas_url:
            payload["canvas_url"] = canvas_url
        if track_uri:
            payload["_track_uri"] = track_uri
        ts = _action_ts_ctx.get() or self._action_ts
        if ts:
            payload["_action_ts"] = ts
        # Cache for instant replay on source button activate. The
        # track_uri is stored alongside so _resync_media can verify
        # the cached canvas still belongs to the track the player is
        # currently on (external track advances invalidate the canvas
        # but not the rest of the metadata shape).
        self._last_media = {
            "title": title, "artist": artist, "album": album,
            "artwork": artwork, "back_artwork": back_artwork,
            "track_uri": track_uri,
        }
        # Always update canvas_url (including clearing it) so stale
        # canvas from a previous track doesn't persist through resyncs.
        if canvas_url:
            self._last_media["canvas_url"] = canvas_url
        else:
            self._last_media.pop("canvas_url", None)
        try:
            async with self._http_session.post(
                self.ROUTER_MEDIA_URL, json=payload, timeout=5,
            ) as resp:
                log.info("Router media -> %s (HTTP %d)", reason, resp.status)
        except Exception as e:
            log.warning("Failed to post media update: %s", e)

    async def _resync_media(self):
        """Re-post current metadata to the router if source is playing/paused.
        Fetches fresh media from the player service to avoid replaying stale
        cached data (e.g. after auto-advance to next track)."""
        if self._registered_state not in ("playing", "paused"):
            return
        # Prefer live player media over cached _last_media
        fresh = await self._player_get("media")
        if fresh and fresh.get("title"):
            # Only reuse the cached canvas if the fresh track is the
            # SAME track we cached for. An external track advance
            # (Sonos app skip, queue auto-next) would otherwise pair
            # the new track's metadata with the previous track's
            # canvas video — which is exactly the Kitchen bug where
            # "Nothing Compares 2 U" was paired with a completely
            # different song's canvas. Match by Spotify track id
            # (extracted from any URI shape — Sonos-wrapped, bare,
            # etc.) so normalization differences between player
            # services and sources don't cause false mismatches.
            cached_canvas = ""
            if self._last_media:
                from lib.spotify_canvas import extract_spotify_track_id
                cached_id = extract_spotify_track_id(
                    self._last_media.get("track_uri", ""))
                fresh_id = extract_spotify_track_id(fresh.get("uri", ""))
                if cached_id and cached_id == fresh_id:
                    cached_canvas = self._last_media.get("canvas_url", "")
            media = {
                "title": fresh.get("title", ""),
                "artist": fresh.get("artist", ""),
                "album": fresh.get("album", ""),
                "artwork": fresh.get("artwork", ""),
                "canvas_url": cached_canvas,
            }
            await self.post_media_update(
                **media, state=self._registered_state, reason="resync")
        elif self._last_media:
            # Strip fields that aren't parameters of post_media_update —
            # track_uri is stored for resync matching but not a kwarg.
            replay = {k: v for k, v in self._last_media.items()
                      if k != "track_uri"}
            await self.post_media_update(
                **replay, state=self._registered_state, reason="resync")

    # ── Player service client helpers ──

    async def _player_post(self, endpoint, json_data=None) -> bool:
        """POST to player service, return True on success."""
        # play can take 5-10s on Sonos (SoCo play_uri is blocking)
        timeout = 15 if endpoint == "play" else 5
        try:
            async with self._http_session.post(
                f"{PLAYER_COMMAND_URL}/{endpoint}",
                json=json_data or {},
                timeout=timeout,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("status") == "ok"
                return False
        except Exception as e:
            log.warning("Player %s failed: %s", endpoint, e)
            return False

    async def _player_get(self, endpoint):
        """GET from player service, return JSON dict or None."""
        try:
            async with self._http_session.get(
                f"{PLAYER_COMMAND_URL}/{endpoint}",
                timeout=5,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            log.warning("Player %s failed: %s", endpoint, e)
            return None

    async def player_play(self, uri=None, url=None, track_uri=None, meta=None,
                          radio=False, track_uris=None, action_ts=None) -> bool:
        """Ask the player service to play a URI or URL.
        track_uri: Spotify track URI to start at within a playlist/album.
        meta: optional dict with display metadata (title, artist, album,
              artwork_url, track_number) — shown on Sonos/BlueSound controllers.
        radio: if True, treat URL as a continuous radio stream (Sonos uses
               x-rincon-mp3radio:// scheme instead of plain HTTP).
        track_uris: list of spotify:track:xxx URIs to queue individually
                    (used for Liked Songs and other non-playlist collections)."""
        body = {}
        if uri:
            body["uri"] = uri
        if url:
            body["url"] = url
        if track_uri:
            body["track_uri"] = track_uri
        if meta:
            body["meta"] = meta
        if radio:
            body["radio"] = True
        if track_uris:
            body["track_uris"] = track_uris
        # Carry this source's authority timestamp so the player can reject
        # stale play commands from sources that were superseded by a newer
        # activation.  _action_ts is kept in sync by player_next/prev/resume.
        # Prefer explicit action_ts (from data["_action_ts"], immune to
        # concurrent overwrites) over self._action_ts (shared mutable field).
        ts = action_ts or _action_ts_ctx.get() or self._action_ts or time.monotonic()
        body["action_ts"] = ts
        return await self._player_post("play", body)

    async def player_play_track_radio(self, track_uri, action_ts=None) -> bool:
        """Ask the player to start a radio station seeded by *track_uri*
        (e.g. Spotify track radio). Returns False if the player doesn't
        support it."""
        ts = action_ts or _action_ts_ctx.get() or self._action_ts or time.monotonic()
        return await self._player_post(
            "play_track_radio",
            {"track_uri": track_uri, "action_ts": ts})

    async def player_set_shuffle(self, enabled: bool) -> bool:
        """Enable/disable shuffle on the player. Returns False if the
        player doesn't support it."""
        return await self._player_post("shuffle", {"enabled": bool(enabled)})

    async def player_pause(self) -> bool:
        return await self._player_post("pause")

    async def player_resume(self) -> bool:
        ts = time.monotonic()
        self._action_ts = ts
        _action_ts_ctx.set(ts)
        return await self._player_post("resume", {"action_ts": ts})

    async def player_next(self) -> bool:
        ts = time.monotonic()
        self._action_ts = ts
        _action_ts_ctx.set(ts)
        return await self._player_post("next", {"action_ts": ts})

    async def player_prev(self) -> bool:
        ts = time.monotonic()
        self._action_ts = ts
        _action_ts_ctx.set(ts)
        return await self._player_post("prev", {"action_ts": ts})

    async def player_stop(self) -> bool:
        ts = _action_ts_ctx.get() or self._action_ts or 0
        return await self._player_post("stop", {"action_ts": ts})

    async def player_state(self) -> str:
        """Get the player's current state ("playing"|"paused"|"stopped"|"unknown")."""
        data = await self._player_get("state")
        if data:
            return data.get("state", "unknown")
        return "unknown"

    async def player_available(self) -> bool:
        """True if the player service is reachable."""
        data = await self._player_get("state")
        return data is not None

    async def player_capabilities(self) -> list:
        """Get the player's supported content types."""
        data = await self._player_get("capabilities")
        if data:
            return data.get("capabilities", [])
        return []

    async def player_spotify_status(self) -> dict:
        """Get Spotify Connect status from the player service."""
        data = await self._player_get("spotify-status")
        return data or {"available": False}

    async def player_track_uri(self) -> str:
        """Get the URI/URL of the track currently playing on the player."""
        data = await self._player_get("track_uri")
        if data:
            return data.get("track_uri", "")
        return ""

    # ── HTTP server ──

    async def start(self):
        """Create the aiohttp app, register routes, start listening."""
        # Menu guard — exit cleanly if this source isn't in config
        menu = cfg("menu") or {}
        menu_ids = set()
        for v in menu.values():
            if isinstance(v, str):
                menu_ids.add(v)
            elif isinstance(v, dict) and "id" in v:
                menu_ids.add(v["id"])
        if menu_ids and self.id not in menu_ids:
            log.info("Source %s not in config menu — exiting", self.id)
            from .watchdog import sd_notify
            sd_notify("READY=1\nSTATUS=Source not in menu, exiting")
            sd_notify("STOPPING=1")
            sys.exit(0)

        app = web.Application()
        app.router.add_get("/status", self._handle_status_route)
        app.router.add_post("/command", self._handle_command_route)
        app.router.add_options("/command", self._handle_cors)
        app.router.add_get("/resync", self._handle_resync_route)
        app.router.add_get("/queue", self._handle_queue_route)

        # Let subclass add extra routes
        self.add_routes(app)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        log.info("HTTP API on port %d", self.port)

        self._http_session = ClientSession()
        # Event-loop lag detector: warns when a sync call blocks the
        # loop for more than the default threshold.
        self._loop_monitor = LoopMonitor().start()

        # Start systemd watchdog heartbeat before on_start — sends READY=1
        # immediately so Type=notify doesn't fail if on_start blocks/crashes
        asyncio.create_task(watchdog_loop())

        await self.on_start()

    async def stop(self):
        """Shutdown hook — override on_stop() for cleanup."""
        await self.on_stop()
        # Cancel any tracked background tasks spawned via _spawn()
        await self._background_tasks.cancel_all()

        if getattr(self, "_loop_monitor", None) is not None:
            await self._loop_monitor.stop()
            self._loop_monitor = None
        # Deregister from router so menu doesn't show a dead source
        try:
            await self.register('gone')
        except Exception:
            pass  # router may already be down
        if self._http_session:
            await self._http_session.close()
            self._http_session = None

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
            await self.stop()

    # ── CORS ──

    def _cors_headers(self):
        return CORS_HEADERS

    async def _handle_cors(self, request):
        return web.Response(headers=self._cors_headers())

    # ── Route handlers (delegate to subclass) ──

    async def _handle_status_route(self, request):
        result = await self.handle_status()
        return web.json_response(result, headers=self._cors_headers())

    async def _handle_resync_route(self, request):
        result = await self.handle_resync()
        return web.json_response(result, headers=self._cors_headers())

    async def _handle_command_route(self, request):
        try:
            # Propagate correlation ID from router
            cid = request.headers.get(CID_HEADER)
            if cid:
                set_id(cid)

            data = await request.json()

            # Raw action from router (forwarded event)
            action = data.get("action")
            if action:
                # Pick up action_ts from router-forwarded events
                ts = data.get("action_ts") or time.monotonic()
                self._action_ts = ts
                _action_ts_ctx.set(ts)
                # Source button activation
                if action == "activate":
                    result = await self.handle_activate(data)
                    resp = {"status": "ok", "command": "activate"}
                    if result:
                        resp.update(result)
                    return web.json_response(resp, headers=self._cors_headers())
                # Let subclass intercept before action_map
                override = await self.handle_raw_action(action, data)
                if override is not None:
                    cmd, data = override
                else:
                    cmd = self.action_map.get(action)
                    if not cmd:
                        return web.json_response(
                            {"status": "error", "message": f"Unmapped action: {action}"},
                            status=400,
                            headers=self._cors_headers(),
                        )
            else:
                # Direct command from UI JS
                ts = time.monotonic()
                self._action_ts = ts
                _action_ts_ctx.set(ts)
                cmd = data.get("command", "")

            # Snapshot for subclasses that pass data["_action_ts"] to player_play
            data["_action_ts"] = _action_ts_ctx.get()

            result = await self.handle_command(cmd, data)
            resp = {"status": "ok", "command": cmd}
            if result:
                resp.update(result)
            return web.json_response(resp, headers=self._cors_headers())

        except Exception as e:
            log.exception("Command error")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500,
                headers=self._cors_headers(),
            )

    # ── Queue support ──

    async def get_queue(self, start=0, max_items=50) -> dict:
        """Return the source's playback queue. Override in subclass."""
        return {"tracks": [], "current_index": -1, "total": 0}

    async def _handle_queue_route(self, request):
        start = int(request.query.get("start", "0"))
        max_items = int(request.query.get("max_items", "50"))
        result = await self.get_queue(start, max_items)
        return web.json_response(result, headers=self._cors_headers())

    # ── Subclass hooks (override as needed) ──

    async def on_start(self):
        """Called after HTTP server is up."""

    async def on_stop(self):
        """Called during shutdown."""

    async def handle_status(self) -> dict:
        """Return status dict for GET /status."""
        return {"source": self.id, "name": self.name}

    async def handle_resync(self) -> dict:
        """Re-register state and metadata. Called by input.py on new client."""
        return {"status": "ok", "resynced": False}

    def add_routes(self, app: web.Application):
        """Add extra aiohttp routes to the app."""

    async def handle_activate(self, data: dict) -> dict | None:
        """Source button pressed — resume or start playback.
        Called only when the source is NOT already active+playing (the router
        skips activate for sources that are already playing).
        IMPORTANT: Must never pause — the shared player may be playing
        another source's content.

        Default: registers as playing (stops old source), pre-broadcasts
        cached metadata, then calls activate_playback() for source-specific
        resume/start logic.  Override activate_playback() instead of this."""
        ts = data.get("action_ts", 0) or 0
        self._action_ts = ts
        _action_ts_ctx.set(ts)
        await self.register("playing", auto_power=True)
        if self._last_media:
            await self.post_media_update(
                **self._last_media, state="playing", reason="activate")
        await self.activate_playback()

    async def activate_playback(self):
        """Source-specific resume/start logic on source button press.
        Called after pre-broadcast and register.  Override this."""

    async def handle_raw_action(self, action: str, data: dict):
        """
        Called before action_map lookup.
        Return (cmd, data) to override, or None to fall through.
        """
        return None

    async def handle_command(self, cmd: str, data: dict) -> dict:
        """Handle a command. Must be implemented by subclass."""
        raise NotImplementedError
