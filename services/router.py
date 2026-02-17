#!/usr/bin/env python3
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
from lib.transport import Transport
from lib.volume_adapters import create_volume_adapter

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
OUTPUT_NAME = os.getenv("OUTPUT_NAME", "BeoLab 5")
VOLUME_STEP = int(os.getenv("VOLUME_STEP", "3"))  # % per volup/voldown press

# Static menu item titles (id -> display title)
MENU_TITLES = {
    "showing": "SHOWING",
    "system": "SYSTEM",
    "security": "SECURITY",
    "scenes": "SCENES",
    "music": "MUSIC",
    "playing": "PLAYING",
}


# ---------------------------------------------------------------------------
# Source model & registry
# ---------------------------------------------------------------------------
class Source:
    """A registered source that can receive routed events."""

    def __init__(self, id: str, handles: set, after: str = "music"):
        self.id = id
        self.name = id.upper()        # display name, overridden on register
        self.command_url = ""          # HTTP endpoint for forwarding events
        self.handles = handles         # set of action names this source handles
        self.menu_preset = id          # SourcePresets key in the UI
        self.after = after             # insert menu item after this id
        self.state = "gone"            # gone | available | playing | paused

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

    def create_from_config(self, id: str, cfg: dict) -> Source:
        """Pre-create a Source from config (not yet registered/available)."""
        handles = set(cfg.get("handles", []))
        after = cfg.get("after", "music")
        source = Source(id, handles, after)
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
            after = fields.get("after", "music")
            source = Source(id, handles, after)
            self._sources[id] = source

        # Update fields from registration payload
        if "name" in fields:
            source.name = fields["name"]
        if "command_url" in fields:
            source.command_url = fields["command_url"]
        if "menu_preset" in fields:
            source.menu_preset = fields["menu_preset"]
        # handles from config take precedence; only use registration handles for unknown sources
        if "handles" in fields and not source.handles:
            source.handles = set(fields["handles"])

        old_state = source.state
        source.state = state
        actions = []

        if state == "available" and was_new:
            # New registration → add menu item
            await router._broadcast("menu_item", {
                "action": "add", "preset": source.menu_preset
            })
            actions.append("add_menu_item")
            logger.info("Source registered: %s (handles: %s)", id, source.handles)

        elif state == "playing":
            # Activate this source
            if self._active_id != id:
                self._active_id = id
                await router._broadcast("source_change", {
                    "active_source": id, "source_name": source.name
                })
                actions.append("source_change")
                logger.info("Source activated: %s", id)
                # Auto-power output
                if router._volume and not await router._volume.is_on():
                    await router._volume.power_on()

        elif state == "paused":
            # Still active, user can resume
            if self._active_id != id:
                self._active_id = id
                await router._broadcast("source_change", {
                    "active_source": id, "source_name": source.name
                })
                actions.append("source_change")

        elif state == "available" and was_active:
            # Deactivate — return to HA fallback
            self._active_id = None
            await router._broadcast("source_change", {"active_source": None})
            actions.append("source_change_clear")
            logger.info("Source deactivated: %s", id)

        elif state == "gone":
            # Unregister
            if was_active:
                self._active_id = None
                await router._broadcast("source_change", {"active_source": None})
                actions.append("source_change_clear")
            await router._broadcast("menu_item", {
                "action": "remove", "preset": source.menu_preset
            })
            source.state = "gone"
            actions.append("remove_menu_item")
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
        self.output_device = OUTPUT_NAME
        self._session: aiohttp.ClientSession | None = None
        self._volume = None           # VolumeAdapter instance
        self._config = {}

    def _load_config(self) -> dict:
        path = os.getenv("ROUTER_CONFIG", "/etc/beosound5c/router.json")
        try:
            with open(path) as f:
                cfg = json.load(f)
                logger.info("Loaded config from %s", path)
                return cfg
        except FileNotFoundError:
            logger.info("No config file at %s — using defaults", path)
            return {
                "menu": ["showing", "system", "security", "scenes", "music", "playing"],
                "sources": {
                    "cd": {
                        "after": "music",
                        "handles": ["play", "pause", "next", "prev", "stop",
                                    "go", "left", "right", "digits", "info"],
                    }
                },
            }

    async def start(self):
        await self.transport.start()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=2.0),
        )
        # Load config and pre-create sources
        self._config = self._load_config()
        for src_id, src_cfg in self._config.get("sources", {}).items():
            self.registry.create_from_config(src_id, src_cfg)

        # Create volume adapter from config
        self._volume = create_volume_adapter(self._session)
        # Fetch current volume from output device
        self.volume = await self._volume.get_volume()
        logger.info("Router started (transport: %s, output: %s, volume: %.0f%%)",
                     self.transport.mode, self.output_device, self.volume)

    async def stop(self):
        await self.transport.stop()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Router stopped")

    def get_menu(self) -> dict:
        """Build the current menu state from config + registered sources."""
        items = []
        config_menu = self._config.get("menu", [])

        for menu_id in config_menu:
            if menu_id in MENU_TITLES:
                items.append({"id": menu_id, "title": MENU_TITLES[menu_id]})

        # Insert dynamic sources at their configured positions
        for source in self.registry.all_available():
            source_item = source.to_menu_item()
            # Find the insert position (after source.after)
            insert_idx = None
            for i, item in enumerate(items):
                if item["id"] == source.after:
                    insert_idx = i + 1
                    break
            if insert_idx is not None:
                items.insert(insert_idx, source_item)
            else:
                # Fallback: insert before "playing"
                playing_idx = next((i for i, item in enumerate(items) if item["id"] == "playing"), len(items))
                items.insert(playing_idx, source_item)

        return {
            "items": items,
            "active_source": self.registry.active_id,
        }

    async def route_event(self, payload: dict):
        """Route an incoming event to the right destination."""
        action = payload.get("action", "")
        active = self.registry.active_source

        # 1. Active source handles this action? → forward raw event
        if active and active.state in ("playing", "paused") and action in active.handles:
            logger.info("-> %s: %s (active source)", active.id, action)
            await self._forward_to_source(active, payload)
            return

        # 2. Digit buttons → forward to active source if it handles "digits"
        digit = payload.get("digit")
        if digit is not None and active and active.state in ("playing", "paused") and "digits" in active.handles:
            logger.info("-> %s: digit %d (active source)", active.id, digit)
            await self._forward_to_source(active, payload)
            return

        # 3. Action matches a registered source id? (e.g., "cd" button)
        source_by_action = self.registry.get(action)
        if source_by_action and source_by_action.state != "gone" and source_by_action.command_url:
            logger.info("-> %s: source button", action)
            await self._forward_to_source(source_by_action, payload)
            return

        # 4. Volume keys — handle locally via adapter
        if action in ("volup", "voldown"):
            delta = VOLUME_STEP if action == "volup" else -VOLUME_STEP
            new_vol = max(0, min(100, self.volume + delta))
            logger.info("-> volume: %.0f%% -> %.0f%% (%s)", self.volume, new_vol, action)
            await self.set_volume(new_vol)
            return

        # 5. Everything else → HA
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

    async def set_volume(self, volume: float):
        """Set volume (0-100). Routes to the appropriate output."""
        self.volume = max(0, min(100, volume))
        if not await self._volume.is_on():
            logger.info("Output is OFF — skipping volume command (%.0f%%)", self.volume)
            return
        await self._volume.set_volume(self.volume)

    async def report_volume(self, volume: float):
        """A device reports its current volume (e.g. Sonos says 'I'm at 40%')."""
        self.volume = max(0, min(100, volume))
        logger.info("Volume reported: %.0f%%", self.volume)

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
    for key in ("name", "command_url", "menu_preset", "handles", "after", "navigate"):
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


async def handle_status(request: web.Request) -> web.Response:
    """GET /router/status — return current routing state."""
    active = router_instance.registry.active_source
    return web.json_response({
        "active_source": router_instance.registry.active_id,
        "active_source_name": active.name if active else None,
        "active_view": router_instance.active_view,
        "volume": router_instance.volume,
        "output_device": router_instance.output_device,
        "transport_mode": router_instance.transport.mode,
        "sources": {
            s.id: {"state": s.state, "name": s.name}
            for s in router_instance.registry.all_available()
        },
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
    app.router.add_get("/router/menu", handle_menu)
    app.router.add_post("/router/view", handle_view)
    app.router.add_post("/router/volume", handle_volume_set)
    app.router.add_post("/router/volume/report", handle_volume_report)
    app.router.add_get("/router/status", handle_status)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=ROUTER_PORT, print=lambda msg: logger.info(msg))
