#!/usr/bin/env python3
# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""
BeoSound 5c Event Router (beo-router)

Sits between event producers (bluetooth.py, masterlink.py) and destinations
(Home Assistant, source services like cd.py). Routes events based on the
active source's registered handles, manages the menu via a config file,
and provides a source registry for dynamic sources.

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
from lib.config import cfg
from lib.transport import Transport
from lib.volume_adapters import create_volume_adapter
from lib.watchdog import watchdog_loop

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
INPUT_WEBHOOK_URL = "http://localhost:8767/webhook"

# Static menu IDs — these are built-in views (not dynamic sources)
STATIC_VIEWS = {"showing", "system", "scenes", "playing"}

# Source handles defaults (used when a source registers without specifying handles)
_DIGITS = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}
DEFAULT_SOURCE_HANDLES = {
    "cd": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
           "up", "down", "info"} | _DIGITS,
    "spotify": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
                "up", "down"} | _DIGITS,
    "usb": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
            "up", "down"},
    "demo": {"play", "pause", "next", "prev", "stop", "go"},
    "news": {"go", "left", "right", "up", "down"},
}

# Known source ports — used on startup to probe running sources for re-registration
DEFAULT_SOURCE_PORTS = {
    "cd": 8769,
    "spotify": 8771,
    "usb": 8772,
    "news": 8776,
}


# ---------------------------------------------------------------------------
# Source model & registry
# ---------------------------------------------------------------------------
class Source:
    """A registered source that can receive routed events."""

    def __init__(self, id: str, handles: set):
        self.id = id
        self.name = id.upper()        # display name, overridden on register
        self.command_url = ""          # HTTP endpoint for forwarding events
        self.handles = handles         # set of action names this source handles
        self.menu_preset = id          # SourcePresets key in the UI
        self.player = "local"          # "local" or "remote" — who renders audio
        self.state = "gone"            # gone | available | playing | paused
        self.from_config = False       # True if pre-created from config.json
        self.initial_hidden = False    # True = hidden until service registers (e.g. CD)

    def to_menu_item(self) -> dict:
        return {
            "id": self.id,
            "title": self.name,
            "preset": self.menu_preset,
            "dynamic": True,
        }


class SourceRegistry:
    """Manages dynamic sources and their lifecycle."""

    def __init__(self):
        self._sources: dict[str, Source] = {}
        self._active_id: str | None = None

    @property
    def active_source(self) -> Source | None:
        if self._active_id:
            return self._sources.get(self._active_id)
        return None

    @property
    def active_id(self) -> str | None:
        return self._active_id

    def get(self, id: str) -> Source | None:
        return self._sources.get(id)

    def create_from_config(self, id: str, handles: set) -> Source:
        """Pre-create a Source from config (not yet registered/available)."""
        source = Source(id, handles)
        self._sources[id] = source
        return source

    async def update(self, id: str, state: str, router: "EventRouter", **fields) -> dict:
        """Handle a source state transition. Returns broadcast actions taken."""
        source = self._sources.get(id)
        was_new = source is None or source.state == "gone"
        was_active = self._active_id == id

        # Create source if unknown (not in config)
        if source is None:
            handles = set(fields.get("handles", []))
            source = Source(id, handles)
            self._sources[id] = source

        # Update fields from registration payload
        if "name" in fields:
            source.name = fields["name"]
        if "command_url" in fields:
            source.command_url = fields["command_url"]
        if "menu_preset" in fields:
            source.menu_preset = fields["menu_preset"]
        if "player" in fields:
            source.player = fields["player"]
        # handles from config take precedence; only use registration handles for unknown sources
        if "handles" in fields and not source.handles:
            source.handles = set(fields["handles"])

        old_state = source.state
        source.state = state
        actions = []

        if state == "available" and was_new:
            if source.from_config:
                if source.initial_hidden:
                    # Hidden config source (e.g. CD) → show in menu
                    await router._broadcast("menu_item", {
                        "action": "show", "preset": source.menu_preset,
                        "path": f"menu/{id}",
                    })
                    actions.append("show_menu_item")
                # Non-hidden config sources are already visible — no broadcast
            else:
                # Non-config source → add to menu dynamically
                broadcast_data = {"action": "add", "preset": source.menu_preset}
                after_id = router._get_after(id)
                if after_id:
                    broadcast_data["after"] = f"menu/{after_id}"
                await router._broadcast("menu_item", broadcast_data)
                actions.append("add_menu_item")
            logger.info("Source registered: %s (handles: %s)", id, source.handles)

        elif state == "playing":
            # Activate this source
            if self._active_id != id:
                # Stop the previously active source (if any)
                prev = self._sources.get(self._active_id) if self._active_id else None
                if prev and prev.state in ("playing", "paused") and prev.command_url:
                    logger.info("Stopping previous source: %s", prev.id)
                    await router._forward_to_source(prev, {"action": "stop"})

                self._active_id = id
                await router._broadcast("source_change", {
                    "active_source": id, "source_name": source.name,
                    "player": source.player,
                })
                actions.append("source_change")
                logger.info("Source activated: %s (player=%s)", id, source.player)

            # Auto-power output — only when source explicitly requests it
            # (user-initiated playback, not external detection)
            if fields.get("auto_power") and router._volume and not await router._volume.is_on():
                await router._volume.power_on()

        elif state == "paused":
            # Still active, user can resume
            if self._active_id != id:
                self._active_id = id
                await router._broadcast("source_change", {
                    "active_source": id, "source_name": source.name,
                    "player": source.player,
                })
                actions.append("source_change")

        elif state == "available" and was_active:
            # Deactivate — return to HA fallback
            self._active_id = None
            await router._broadcast("source_change", {
                "active_source": None, "player": None,
            })
            actions.append("source_change_clear")
            logger.info("Source deactivated: %s", id)

        elif state == "gone":
            if was_active:
                self._active_id = None
                await router._broadcast("source_change", {
                    "active_source": None, "player": None,
                })
                actions.append("source_change_clear")
            if source.from_config:
                if source.initial_hidden:
                    # Hidden config source (e.g. CD) → hide from menu
                    await router._broadcast("menu_item", {
                        "action": "hide", "preset": source.menu_preset,
                        "path": f"menu/{id}",
                    })
                    actions.append("hide_menu_item")
                # Non-hidden config sources stay visible — no broadcast
            else:
                # Non-config source → remove from menu
                await router._broadcast("menu_item", {
                    "action": "remove", "preset": source.menu_preset
                })
                actions.append("remove_menu_item")
            source.state = "gone"
            logger.info("Source unregistered: %s", id)

        # Optional: navigate UI to the source's view
        if fields.get("navigate") and state in ("playing", "available"):
            page = f"menu/{id}"
            await router._broadcast("navigate", {"page": page})
            actions.append(f"navigate:{page}")

        return {"actions": actions, "old_state": old_state, "new_state": state}

    def handles_action(self, action: str) -> bool:
        """Check if the active source handles this action."""
        source = self.active_source
        if not source:
            return False
        if action in source.handles:
            return True
        # "digits" handle matches any digit action
        if "digits" in source.handles:
            return False  # digits are checked separately via payload
        return False

    async def clear_active_source(self, router: "EventRouter"):
        """Clear the active source (e.g. when external playback overrides it)."""
        if self._active_id is None:
            return False
        old_id = self._active_id
        self._active_id = None
        await router._broadcast("source_change", {
            "active_source": None, "player": None,
        })
        logger.info("Active source cleared (was: %s)", old_id)
        return True

    def all_available(self) -> list[Source]:
        """Return all sources that are not gone."""
        return [s for s in self._sources.values() if s.state != "gone"]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class EventRouter:
    def __init__(self):
        self.transport = Transport()
        self.registry = SourceRegistry()
        self.active_view = None       # UI view reported by frontend
        self.volume = 0               # current volume 0-100
        self.balance = 0              # current balance -20..+20
        self.output_device = cfg("volume", "output_name", default="BeoLab 5")
        self._volume_step = int(cfg("volume", "step", default=3))
        self._balance_step = 1
        self._session: aiohttp.ClientSession | None = None
        self._volume = None           # VolumeAdapter instance
        self._menu_order: list[dict] = []  # parsed menu from config
        self._local_button_views: set[str] = {"menu/system"}  # views that suppress HA button forwarding

    def _parse_menu(self):
        """Parse the menu section from config.json into an ordered list.

        Menu is defined top-to-bottom as visible on screen.  Each entry is
        either a static view or a source.  String values = component with
        default config; object values = component + config.
        """
        menu_cfg = cfg("menu")
        if not menu_cfg:
            # Fallback menu
            menu_cfg = {
                "PLAYING": "playing", "SPOTIFY": "spotify", "SCENES": "scenes",
                "SYSTEM": "system", "SHOWING": "showing",
            }

        items = []
        for title, value in menu_cfg.items():
            if isinstance(value, str):
                entry_id = value
                entry_cfg = {}
            else:
                entry_id = value.get("id", title.lower().replace(" ", "_"))
                entry_cfg = value
            items.append({"id": entry_id, "title": title, "config": entry_cfg})

        # Pre-create sources from menu entries (non-static-view, non-webpage items)
        for item in items:
            if "url" in item["config"]:
                pass  # Webpage item — buttons fall through to HA (gate/lock etc.)
            elif item["id"] not in STATIC_VIEWS:
                handles = DEFAULT_SOURCE_HANDLES.get(item["id"], set())
                source = self.registry.create_from_config(item["id"], handles)
                source.from_config = True
                source.initial_hidden = item["config"].get("hidden", False)

        self._menu_order = items

    async def start(self):
        await self.transport.start()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=2.0),
        )
        # Parse menu from config and pre-create sources
        self._parse_menu()

        # Create volume adapter from config
        self._volume = create_volume_adapter(self._session)
        # Fetch current volume from output device
        self.volume = await self._volume.get_volume()
        logger.info("Router started (transport: %s, output: %s, volume: %.0f%%)",
                     self.transport.mode, self.output_device, self.volume)

        # Probe running sources so they re-register after a router restart
        asyncio.ensure_future(self._probe_running_sources())

    async def _probe_running_sources(self):
        """Ask each known source to re-register via its /resync endpoint.

        This handles the case where the router restarts while sources are
        still running — without this, the router would lose track of active
        sources until they happen to change state.
        """
        await asyncio.sleep(1)  # give the HTTP server a moment to bind
        for source_id, port in DEFAULT_SOURCE_PORTS.items():
            try:
                async with self._session.get(
                    f"http://localhost:{port}/resync",
                    timeout=aiohttp.ClientTimeout(total=2.0),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("resynced"):
                            logger.info("Probed %s (port %d) — re-registered", source_id, port)
                        else:
                            logger.debug("Probed %s (port %d) — nothing to resync", source_id, port)
            except Exception:
                logger.debug("Source %s not running on port %d", source_id, port)

    async def stop(self):
        await self.transport.stop()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Router stopped")

    def get_menu(self) -> dict:
        """Build the current menu state from config menu order.

        The menu order in config.json is top-to-bottom.  Static views are
        always shown.  Config-driven sources are always included (they are
        visible on every installation that lists them in config.json).
        Sources with ``hidden`` set in config (e.g. CD) start hidden and
        become visible only when their service registers as available.
        """
        items = []
        for entry in self._menu_order:
            entry_id = entry["id"]
            entry_cfg = entry.get("config", {})
            if "url" in entry_cfg:
                # Webpage item — embedded iframe
                items.append({
                    "id": entry_id, "title": entry["title"],
                    "type": "webpage", "url": entry_cfg["url"],
                })
            elif entry_id in STATIC_VIEWS:
                items.append({"id": entry_id, "title": entry["title"]})
            else:
                source = self.registry.get(entry_id)
                if source:
                    item = source.to_menu_item()
                    item["title"] = entry["title"]  # Use config title
                    # Hidden sources (e.g. CD) are hidden until available
                    if source.initial_hidden and source.state == "gone":
                        item["hidden"] = True
                    items.append(item)

        active = self.registry.active_source
        return {
            "items": items,
            "active_source": self.registry.active_id,
            "active_player": active.player if active else None,
        }

    async def route_event(self, payload: dict):
        """Route an incoming event to the right destination."""
        action = payload.get("action", "")
        device_type = payload.get("device_type", "")
        active = self.registry.active_source

        # 1. Active source handles this action? → forward (Audio mode only)
        if device_type == "Audio" and active and active.state in ("playing", "paused") and action in active.handles:
            logger.info("-> %s: %s (active source)", active.id, action)
            await self._forward_to_source(active, payload)
            return

        # 2. Action matches a registered source id? (e.g., "cd" button)
        source_by_action = self.registry.get(action)
        if source_by_action and source_by_action.state != "gone" and source_by_action.command_url:
            logger.info("-> %s: source button", action)
            await self._forward_to_source(source_by_action, payload)
            return

        # 4. Volume keys — handle locally via adapter (Audio mode only)
        if action in ("volup", "voldown") and device_type == "Audio":
            delta = self._volume_step if action == "volup" else -self._volume_step
            new_vol = max(0, min(100, self.volume + delta))
            logger.info("-> volume: %.0f%% -> %.0f%% (%s)", self.volume, new_vol, action)
            # Auto-power output on volume up (cached check only — no network query)
            if action == "volup" and self._volume and self._volume.is_on_cached() is False:
                asyncio.ensure_future(self._volume.power_on())
            # Fire-and-forget — adapter debounces internally, don't block event loop
            asyncio.ensure_future(self.set_volume(new_vol))
            return

        # 4b. Balance keys — handle locally via adapter (Audio mode only)
        if action in ("chup", "chdown") and device_type == "Audio":
            delta = self._balance_step if action == "chup" else -self._balance_step
            new_bal = max(-20, min(20, self.balance + delta))
            logger.info("-> balance: %d -> %d (%s)", self.balance, new_bal, action)
            self.balance = new_bal
            if self._volume:
                asyncio.ensure_future(self._volume.set_balance(new_bal))
            return

        # 4c. Off — power off output (Audio mode only)
        if action == "off" and device_type == "Audio" and self._volume:
            logger.info("-> powering off output")
            asyncio.ensure_future(self._volume.power_off())
            # Still forward to HA (below) so it can handle screen off etc.

        # 5. Views that handle buttons locally (iframes) — suppress HA forwarding
        if self.active_view in self._local_button_views and action in (
            "go", "left", "right", "up", "down",
        ):
            logger.info("-> suppressed: %s on %s (handled by UI)", action, self.active_view)
            return

        # 6. Everything else → HA
        logger.info("-> HA: %s (%s)", action, payload.get("device_type", ""))
        await self.transport.send_event(payload)

    async def _forward_to_source(self, source: Source, payload: dict):
        """Forward a raw event payload to a source's command endpoint."""
        if not source.command_url or not self._session:
            logger.warning("Cannot forward to %s (no url or session)", source.id)
            return
        try:
            async with self._session.post(
                source.command_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                logger.debug("%s responded: HTTP %d", source.id, resp.status)
        except Exception as e:
            logger.warning("%s unreachable: %s", source.id, e)

    async def set_volume(self, volume: float, broadcast: bool = True):
        """Set volume (0-100). Routes to the appropriate output."""
        self.volume = max(0, min(100, volume))
        if broadcast:
            asyncio.ensure_future(self._broadcast_volume())
        await self._volume.set_volume(self.volume)

    async def report_volume(self, volume: float):
        """A device reports its current volume (e.g. Sonos says 'I'm at 40%')."""
        self.volume = max(0, min(100, volume))
        logger.info("Volume reported: %.0f%%", self.volume)
        await self._broadcast_volume()

    async def _broadcast_volume(self):
        """Push current volume to UI clients so the arc stays in sync."""
        await self._broadcast("volume_update", {"volume": round(self.volume)})

    def _get_after(self, source_id: str) -> str | None:
        """Find the menu item that precedes this source in the config order.

        Returns the id of the preceding item, or None if this source is
        first or not found in the menu config.
        """
        prev_id = None
        for entry in self._menu_order:
            if entry["id"] == source_id:
                return prev_id
            prev_id = entry["id"]
        return None

    async def _broadcast(self, event_type: str, data: dict):
        """Broadcast an event to UI clients via input.py's webhook API."""
        if not self._session:
            return
        try:
            if event_type == "menu_item":
                # menu_item events use the dedicated add/remove/hide/show commands
                action = data.get("action", "")
                if action == "add":
                    payload = {"command": "add_menu_item", "params": data}
                elif action == "remove":
                    payload = {"command": "remove_menu_item", "params": data}
                elif action in ("hide", "show"):
                    payload = {"command": f"{action}_menu_item", "params": data}
                else:
                    payload = {"command": "broadcast", "params": {"type": event_type, "data": data}}
            elif event_type == "navigate":
                payload = {"command": "wake", "params": data}
            else:
                payload = {"command": "broadcast", "params": {"type": event_type, "data": data}}

            async with self._session.post(
                INPUT_WEBHOOK_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                logger.debug("Broadcast %s: HTTP %d", event_type, resp.status)
        except Exception as e:
            logger.warning("Broadcast %s failed: %s", event_type, e)


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
    """POST /router/source — source registers/updates its state."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"error": "invalid json"}, status=400)

    src_id = data.get("id")
    state = data.get("state")
    if not src_id or not state:
        return web.json_response({"error": "id and state required"}, status=400)

    if state not in ("available", "playing", "paused", "gone"):
        return web.json_response({"error": "invalid state"}, status=400)

    # Extract optional fields
    fields = {}
    for key in ("name", "command_url", "menu_preset", "handles", "navigate", "player", "auto_power"):
        if key in data:
            fields[key] = data[key]

    result = await router_instance.registry.update(src_id, state, router_instance, **fields)

    return web.json_response({
        "status": "ok",
        "source": src_id,
        "active_source": router_instance.registry.active_id,
        **result,
    })


async def handle_menu(request: web.Request) -> web.Response:
    """GET /router/menu — return current menu state for UI."""
    return web.json_response(router_instance.get_menu())


async def handle_volume_set(request: web.Request) -> web.Response:
    """POST /router/volume — UI sets volume (no broadcast back to UI)."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"error": "invalid json"}, status=400)

    volume = data.get("volume")
    if volume is None or not isinstance(volume, (int, float)):
        return web.json_response({"error": "missing or invalid 'volume'"}, status=400)

    # broadcast=False: the UI already shows the change locally
    await router_instance.set_volume(float(volume), broadcast=False)
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


async def handle_output_off(request: web.Request) -> web.Response:
    """POST /router/output/off — power off the audio output (e.g. BeoLab 5)."""
    if router_instance._volume:
        await router_instance._volume.power_off()
        logger.info("Output powered off via /output/off")
        return web.json_response({"status": "ok", "output": "off"})
    return web.json_response({"status": "ok", "output": "no_adapter"})


async def handle_output_on(request: web.Request) -> web.Response:
    """POST /router/output/on — power on the audio output (e.g. BeoLab 5)."""
    if router_instance._volume:
        await router_instance._volume.power_on()
        logger.info("Output powered on via /output/on")
        return web.json_response({"status": "ok", "output": "on"})
    return web.json_response({"status": "ok", "output": "no_adapter"})


async def handle_view(request: web.Request) -> web.Response:
    """POST /router/view — UI reports which view is active."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"error": "invalid json"}, status=400)

    view = data.get("view")
    old = router_instance.active_view
    router_instance.active_view = view
    if old != view:
        logger.info("View changed: %s -> %s", old, view)
    return web.json_response({"status": "ok", "active_view": view})


async def handle_playback_override(request: web.Request) -> web.Response:
    """POST /router/playback_override — currently a no-op.

    Sources manage their own lifecycle by registering state changes with
    /router/source.  When a source registers as "playing", the router
    automatically stops the previous source.  This endpoint is kept as a
    stub for a future enhancement: detecting when an external device (not
    the BS5c) takes over the Sonos speaker from a local AirPlay stream.
    """
    return web.json_response({"status": "ok", "cleared": False, "reason": "disabled"})


async def handle_status(request: web.Request) -> web.Response:
    """GET /router/status — return current routing state."""
    active = router_instance.registry.active_source
    return web.json_response({
        "active_source": router_instance.registry.active_id,
        "active_source_name": active.name if active else None,
        "active_player": active.player if active else None,
        "active_view": router_instance.active_view,
        "volume": router_instance.volume,
        "output_device": router_instance.output_device,
        "transport_mode": router_instance.transport.mode,
        "sources": {
            s.id: {"state": s.state, "name": s.name, "player": s.player}
            for s in router_instance.registry.all_available()
        },
    })


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
async def on_startup(app: web.Application):
    await router_instance.start()
    asyncio.create_task(watchdog_loop())


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
    app.router.add_get("/router/menu", handle_menu)
    app.router.add_post("/router/view", handle_view)
    app.router.add_post("/router/volume", handle_volume_set)
    app.router.add_post("/router/volume/report", handle_volume_report)
    app.router.add_post("/router/playback_override", handle_playback_override)
    app.router.add_post("/router/output/off", handle_output_off)
    app.router.add_post("/router/output/on", handle_output_on)
    app.router.add_get("/router/status", handle_status)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=ROUTER_PORT, print=lambda msg: logger.info(msg))
