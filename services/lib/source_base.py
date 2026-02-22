# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""
SourceBase — shared plumbing for BeoSound 5c source services.

Subclass contract:

    class MySource(SourceBase):
        id   = "demo"        # router source ID
        name = "Demo"        # menu display name
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

from aiohttp import web, ClientSession

log = logging.getLogger()

INPUT_WEBHOOK_URL = "http://localhost:8767/webhook"
ROUTER_SOURCE_URL = "http://localhost:8770/router/source"
PLAYER_COMMAND_URL = "http://localhost:8766/player"


class SourceBase:
    # ── Subclass must set these ──
    id: str = ""
    name: str = ""
    port: int = 0
    player: str = "local"    # "local" or "remote" — who renders the audio
    action_map: dict = {}

    def __init__(self):
        self._http_session: ClientSession | None = None
        self._runner: web.AppRunner | None = None

    # ── Router registration ──

    async def register(self, state, navigate=False, _retries=5):
        """Register / update source state in the router."""
        payload = {"id": self.id, "state": state}
        if state not in ("gone",):
            payload.update({
                "name": self.name,
                "command_url": f"http://localhost:{self.port}/command",
                "menu_preset": self.id,
                "handles": list(self.action_map.keys()),
                "player": self.player,
            })
        if navigate:
            payload["navigate"] = True
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

    # ── UI broadcasting via input.py ──

    async def broadcast(self, event_type, data):
        """Broadcast an event to UI clients via input.py's webhook API."""
        try:
            async with self._http_session.post(
                INPUT_WEBHOOK_URL,
                json={"command": "broadcast", "params": {"type": event_type, "data": data}},
                timeout=5,
            ) as resp:
                log.info("→ input.py: broadcast %s (HTTP %d)", event_type, resp.status)
        except Exception as e:
            log.error("Failed to broadcast %s: %s", event_type, e)

    # ── Player service client helpers ──

    async def _player_post(self, endpoint, json_data=None) -> bool:
        """POST to player service, return True on success."""
        try:
            async with self._http_session.post(
                f"{PLAYER_COMMAND_URL}/{endpoint}",
                json=json_data or {},
                timeout=5,
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

    async def player_play(self, uri=None, url=None, track_uri=None) -> bool:
        """Ask the player service to play a URI or URL.
        track_uri: Spotify track URI to start at within a playlist/album."""
        body = {}
        if uri:
            body["uri"] = uri
        if url:
            body["url"] = url
        if track_uri:
            body["track_uri"] = track_uri
        return await self._player_post("play", body)

    async def player_pause(self) -> bool:
        return await self._player_post("pause")

    async def player_resume(self) -> bool:
        return await self._player_post("resume")

    async def player_next(self) -> bool:
        return await self._player_post("next")

    async def player_prev(self) -> bool:
        return await self._player_post("prev")

    async def player_stop(self) -> bool:
        return await self._player_post("stop")

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

    # ── HTTP server ──

    async def start(self):
        """Create the aiohttp app, register routes, start listening."""
        app = web.Application()
        app.router.add_get("/status", self._handle_status_route)
        app.router.add_post("/command", self._handle_command_route)
        app.router.add_options("/command", self._handle_cors)
        app.router.add_get("/resync", self._handle_resync_route)

        # Let subclass add extra routes
        self.add_routes(app)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        log.info("HTTP API on port %d", self.port)

        self._http_session = ClientSession()

        await self.on_start()

    async def stop(self):
        """Shutdown hook — override on_stop() for cleanup."""
        await self.on_stop()
        if self._http_session:
            await self._http_session.close()
            self._http_session = None

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
            await self.stop()

    # ── CORS ──

    def _cors_headers(self):
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

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
            data = await request.json()

            # Raw action from router (forwarded event)
            action = data.get("action")
            if action:
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
                cmd = data.get("command", "")

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

    async def handle_raw_action(self, action: str, data: dict):
        """
        Called before action_map lookup.
        Return (cmd, data) to override, or None to fall through.
        """
        return None

    async def handle_command(self, cmd: str, data: dict) -> dict:
        """Handle a command. Must be implemented by subclass."""
        raise NotImplementedError
