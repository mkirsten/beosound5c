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
        async def get_state(self) -> str: ...         # "playing"|"paused"|"stopped"
        async def get_capabilities(self) -> list: ... # ["spotify", "url_stream", ...]

Optional overrides:
    on_start()   — called after HTTP server is up
    on_stop()    — called during shutdown
"""

import asyncio
import json
import logging
import signal

from aiohttp import web

log = logging.getLogger(__name__)


class PlayerBase:
    # ── Subclass must set these ──
    id: str = ""
    name: str = ""
    port: int = 8766

    def __init__(self):
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._runner: web.AppRunner | None = None

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
        raise NotImplementedError

    async def get_capabilities(self) -> list:
        """Return list of supported content types, e.g. ["spotify", "url_stream"]."""
        raise NotImplementedError

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

        # Let subclass add extra routes
        self.add_routes(app)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        log.info("Player %s: HTTP + WebSocket on port %d", self.name, self.port)

        await self.on_start()

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
        await self.on_stop()
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

    # ── Subclass hooks ──

    async def on_start(self):
        """Called after HTTP server is up."""

    async def on_stop(self):
        """Called during shutdown."""

    async def on_ws_connect(self, ws: web.WebSocketResponse):
        """Called when a new WebSocket client connects. Send initial state."""

    def add_routes(self, app: web.Application):
        """Add extra aiohttp routes to the app."""
