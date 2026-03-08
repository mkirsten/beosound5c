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
import time
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
           "up", "down", "info", "track", "menu"} | _DIGITS,
    "spotify": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
                "up", "down"} | _DIGITS,
    "usb": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
            "up", "down"},
    "demo": {"play", "pause", "next", "prev", "stop", "go"},
    "news": {"go", "left", "right", "up", "down"},
    "radio": {"play", "pause", "next", "prev", "stop", "go", "left", "right",
              "up", "down", "red", "blue"} | _DIGITS,
}

# Known source ports — used on startup to probe running sources for re-registration
DEFAULT_SOURCE_PORTS = {
    "cd": 8769,
    "spotify": 8771,
    "usb": 8773,
    "apple_music": 8774,
    "demo": 8775,
    "news": 8776,
    "tidal": 8777,
    "plex": 8778,
    "radio": 8779,
    "join": 8766,  # beo-player-sonos
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
        self.visible = "auto"          # "auto" | "always" | "never"

    def to_menu_item(self) -> dict:
        return {
            "id": self.id,
            "title": self.name,
            "preset": self.menu_preset,
            "dynamic": True,
        }


STATE_FILE = "/tmp/beo-router-state.json"


class SourceRegistry:
    """Manages dynamic sources and their lifecycle."""

    def __init__(self):
        self._sources: dict[str, Source] = {}
        self._active_id: str | None = None
        self._persisted_active_id: str | None = self._load_persisted_active()
        self._resync_in_progress: bool = False  # suppress active stealing during resyncs

    @staticmethod
    def _load_persisted_active() -> str | None:
        """Load the previously-active source ID from disk (survives router restart)."""
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
                active = data.get("active_source_id")
                if active:
                    logger.info("Loaded persisted active source: %s", active)
                return active
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def _persist_active(self):
        """Save the active source ID to disk."""
        try:
            with open(STATE_FILE, "w") as f:
                json.dump({"active_source_id": self._active_id}, f)
        except OSError as e:
            logger.warning("Failed to persist active source: %s", e)

    def consume_persisted_active(self) -> str | None:
        """Return and clear the persisted active source ID (used once at startup)."""
        val = self._persisted_active_id
        self._persisted_active_id = None
        return val

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
            if source.visible == "never":
                pass  # Never shown in menu
            elif source.visible == "always":
                pass  # Already in menu from boot
            else:
                # auto: add to menu dynamically
                broadcast_data = {"action": "add", "preset": source.menu_preset}
                config_title = router._get_config_title(id)
                if config_title:
                    broadcast_data["title"] = config_title
                after_id = router._get_after(id)
                if after_id:
                    broadcast_data["after"] = f"menu/{after_id}"
                await router._broadcast("menu_item", broadcast_data)
                actions.append("add_menu_item")
            logger.info("Source registered: %s (handles: %s)", id, source.handles)

        elif state == "playing":
            # Activate this source
            if self._active_id != id:
                # Reject stale register from a superseded source activation
                action_ts = fields.get("action_ts", 0)
                if action_ts and action_ts < router._latest_action_ts:
                    logger.info("Rejected stale register from %s (ts=%.3f < latest=%.3f)",
                                id, action_ts, router._latest_action_ts)
                    return {"actions": actions, "old_state": old_state, "new_state": state}

                # During a resync, sources re-register their remembered state.
                # Don't let them steal active from the actually-playing source.
                if self._resync_in_progress and self._active_id:
                    current = self._sources.get(self._active_id)
                    if current and current.state in ("playing", "paused"):
                        logger.info("Resync: %s wants active but %s is %s — skipping",
                                    id, self._active_id, current.state)
                        # Accept state update but don't change active source
                        return {"actions": actions, "old_state": old_state, "new_state": state}

                # Cross-player switch: when old and new sources use different
                # player types, stop both the player service AND the old source.
                # Same-player sources don't need this — player_play() replaces
                # whatever was playing and the old source detects via polling.
                old_source = self._sources.get(self._active_id) if self._active_id else None
                if old_source and old_source.player != source.player:
                    logger.info("Player type change (%s→%s) — stopping old playback",
                                old_source.player, source.player)
                    asyncio.ensure_future(router._player_stop())
                    # Also tell the old source to stop (e.g. CD's own mpv)
                    if old_source.command_url:
                        asyncio.ensure_future(
                            router._forward_to_source(old_source, {"action": "stop"}))
                self._active_id = id
                self._persist_active()
                await router._broadcast("source_change", {
                    "active_source": id, "source_name": source.name,
                    "player": source.player,
                })
                actions.append("source_change")
                logger.info("Source activated: %s (player=%s)", id, source.player)

            # Auto-power output + wake screen — only when source explicitly
            # requests it (user-initiated playback, not external detection)
            if fields.get("auto_power"):
                if router._volume and not await router._volume.is_on():
                    await router._volume.power_on()
                await router._wake_screen()

        elif state == "paused":
            # Still active, user can resume — but don't steal active from
            # another source that's already playing (e.g., we were paused
            # during a source switch and are just re-registering)
            if self._active_id != id:
                current = self._sources.get(self._active_id) if self._active_id else None
                if not current or current.state not in ("playing", "paused"):
                    self._active_id = id
                    self._persist_active()
                    await router._broadcast("source_change", {
                        "active_source": id, "source_name": source.name,
                        "player": source.player,
                    })
                    actions.append("source_change")

        elif state == "available" and was_active:
            # Deactivate — return to HA fallback
            self._active_id = None
            self._persist_active()
            await router._broadcast("source_change", {
                "active_source": None, "player": None,
            })
            actions.append("source_change_clear")
            logger.info("Source deactivated: %s", id)

        elif state == "gone":
            if was_active:
                self._active_id = None
                self._persist_active()
                await router._broadcast("source_change", {
                    "active_source": None, "player": None,
                })
                actions.append("source_change_clear")
            if source.visible == "never":
                pass  # Never shown in menu
            elif source.visible == "always":
                pass  # Stays in menu
            else:
                # auto: remove from menu
                await router._broadcast("menu_item", {
                    "action": "remove", "preset": source.menu_preset
                })
                actions.append("remove_menu_item")
            source.state = "gone"
            logger.info("Source unregistered: %s", id)

        # Optional: navigate UI to the source's view
        if fields.get("navigate") and state in ("playing", "available"):
            # Playing → show PLAYING screen; available → show source browse page
            page = "menu/playing" if state == "playing" else f"menu/{id}"
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
        self._persist_active()
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
        self._accept_player_volume = False  # set in start() based on adapter/player match
        self._menu_order: list[dict] = []  # parsed menu from config
        self._local_button_views: set[str] = {"menu/system"}  # views that suppress HA button forwarding
        self._default_source_id: str | None = cfg("remote", "default_source", default=None)
        self._source_buttons: dict[str, str] = {}  # IR source name → source id
        self._handle_audio: bool = True   # handle Audio-tagged commands locally
        self._handle_video: bool = False  # handle Video-tagged commands locally
        self._media_state: dict | None = None  # cached media data from player
        self._media_ws_clients: set[web.WebSocketResponse] = set()  # UI media WS clients
        self._last_activity: float = time.monotonic()  # auto-standby idle tracker
        self._auto_off_task: asyncio.Task | None = None
        self._latest_action_ts: float = 0.0  # monotonic timestamp of last source activation

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
                source.visible = item["config"].get("visible", "auto")

        # Build IR source button map from per-source config sections
        # e.g. "spotify": { "source": "radio" } → pressing RADIO activates spotify
        for item in items:
            sid = item["id"]
            if sid in STATIC_VIEWS:
                continue
            source = cfg(sid, "source", default=None) or item["config"].get("source")
            if source:
                if source in self._source_buttons:
                    logger.warning("Duplicate source button '%s': %s and %s both mapped — %s wins",
                                   source, self._source_buttons[source], sid, sid)
                self._source_buttons[source] = sid

        # Auto-detect which device types to handle locally based on button types
        _AUDIO_BUTTONS = {"radio", "amem", "cd", "n.radio", "n.music", "spotify"}
        _VIDEO_BUTTONS = {"tv", "v.aux", "a.aux", "vmem", "dvd", "dtv", "pc",
                          "youtube", "doorcam", "photo", "usb2"}
        mapped = set(self._source_buttons.keys())
        if cfg("remote", "handle_all", default=False):
            # Explicit override — handle all device types locally (e.g.
            # Kitchen: no video master, BeoRemote defaults to Video mode)
            self._handle_audio = True
            self._handle_video = True
        elif mapped:
            # Auto-detect from mapped source button types:
            # audio button mapped → BS5c is audio master
            # video button mapped → BS5c is video master
            self._handle_audio = bool(mapped & _AUDIO_BUTTONS)
            self._handle_video = bool(mapped & _VIDEO_BUTTONS)
        logger.info("Device type handling: audio=%s, video=%s (sources: %s)",
                    self._handle_audio, self._handle_video,
                    ", ".join(f"{s}->{sid}" for s, sid in self._source_buttons.items()) or "none")

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
        initial_vol = await self._volume.get_volume()
        if initial_vol is not None:
            self.volume = initial_vol
        else:
            logger.warning("Could not read initial volume — defaulting to 0")
            self.volume = 0

        # Determine whether to accept volume reports from the player service.
        # Only accept when the player IS the volume adapter (e.g. sonos+sonos).
        adapter_type = cfg("volume", "type")
        if adapter_type is None:
            # Mirror the inference logic from create_volume_adapter
            player_type = str(cfg("player", "type", default="")).lower()
            if player_type in ("sonos", "bluesound"):
                adapter_type = player_type
            elif player_type in ("local", "powerlink"):
                adapter_type = "powerlink"
            else:
                adapter_type = "beolab5"
        adapter_type = str(adapter_type).lower()
        player_type = str(cfg("player", "type", default="")).lower()
        self._player_type = player_type
        self._accept_player_volume = (adapter_type == player_type)
        if self._accept_player_volume:
            logger.info("Volume reports from player: accepted (%s)", adapter_type)
        else:
            logger.info("Volume reports from player: ignored (adapter=%s, player=%s)",
                         adapter_type, player_type)

        logger.info("Router started (transport: %s, output: %s, volume: %.0f%%)",
                     self.transport.mode, self.output_device, self.volume)

        # Probe running sources so they re-register after a router restart,
        # then recover media state from the player
        asyncio.ensure_future(self._startup_recovery())

        # Auto-standby after inactivity
        self._auto_off_task = asyncio.create_task(self._auto_standby_loop())

    def touch_activity(self):
        """Reset the inactivity timer (called on any user interaction)."""
        self._last_activity = time.monotonic()

    def _is_playing(self) -> bool:
        """Check if the player is currently playing (from cached media state)."""
        if self._media_state and self._media_state.get("state") == "playing":
            return True
        # Also check source registry
        for source in self.registry.all_available():
            if source.state == "playing":
                return True
        return False

    async def _auto_standby_loop(self):
        """Check every 10 minutes whether to auto-standby."""
        AUTO_OFF_IDLE = 30 * 60   # 30 minutes of inactivity
        CHECK_INTERVAL = 10 * 60  # check every 10 minutes
        while True:
            await asyncio.sleep(CHECK_INTERVAL)
            idle = time.monotonic() - self._last_activity
            if idle >= AUTO_OFF_IDLE and not self._is_playing():
                logger.info("Auto-standby: idle %.0f min, nothing playing", idle / 60)
                asyncio.ensure_future(self._player_stop())
                if self._volume:
                    asyncio.ensure_future(self._volume.power_off())
                asyncio.ensure_future(self._screen_off())

    async def _probe_running_sources(self, startup=False):
        """Ask each known source to re-register via its /resync endpoint.

        This handles the case where the router restarts while sources are
        still running — without this, the router would lose track of active
        sources until they happen to change state.

        When startup=True, uses the persisted active source to resolve
        multi-source conflicts: only the previously-active source keeps its
        playing/paused state; others are capped at "available".

        Returns a list of source IDs that successfully resynced.
        """
        await asyncio.sleep(1)  # give the HTTP server a moment to bind
        persisted_id = self.registry.consume_persisted_active() if startup else None
        if persisted_id:
            logger.info("Startup resync — persisted active: %s", persisted_id)

        # For non-startup resyncs, prevent sources from stealing the active
        # source — they re-register their remembered state which can be stale.
        if not startup:
            self.registry._resync_in_progress = True

        resynced = []
        try:
            for source_id, port in DEFAULT_SOURCE_PORTS.items():
                # JOIN lives on the player service — use /player/resync
                path = "/player/resync" if source_id == "join" else "/resync"
                try:
                    async with self._session.get(
                        f"http://localhost:{port}{path}",
                        timeout=aiohttp.ClientTimeout(total=2.0),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("resynced"):
                                resynced.append(source_id)
                                logger.info("Probed %s (port %d) — re-registered", source_id, port)
                            else:
                                logger.debug("Probed %s (port %d) — nothing to resync", source_id, port)
                except Exception:
                    logger.debug("Source %s not running on port %d", source_id, port)
        finally:
            self.registry._resync_in_progress = False

        # Resolve multi-source conflict: only if the persisted source actually
        # resynced successfully — if it's dead, let whoever is playing stay active
        if persisted_id and persisted_id in resynced:
            for source_id in resynced:
                if source_id == persisted_id:
                    continue
                source = self.registry.get(source_id)
                if source and source.state in ("playing", "paused"):
                    source.state = "available"
                    logger.info("Startup resync: demoted %s to available (persisted active: %s)",
                                source_id, persisted_id)
            # Ensure the persisted source is actually active
            persisted = self.registry.get(persisted_id)
            if persisted and persisted.state in ("playing", "paused"):
                if self.registry._active_id != persisted_id:
                    self.registry._active_id = persisted_id
                    self.registry._persist_active()
                    await self._broadcast("source_change", {
                        "active_source": persisted_id,
                        "source_name": persisted.name,
                        "player": persisted.player,
                    })
                    logger.info("Startup resync: restored active source: %s", persisted_id)

        return resynced

    async def _startup_recovery(self):
        """Probe sources and recover media state from the player after restart."""
        await self._probe_running_sources(startup=True)
        # Recover media state from the player — only if no source already set it
        if self._media_state:
            logger.info("Media state already set by source resync, skipping player recovery")
            return
        try:
            async with self._session.get(
                "http://localhost:8766/player/media",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and data.get("title"):
                        self._media_state = data
                        logger.info("Recovered media state: %s — %s",
                                     data.get("artist", ""), data.get("title", ""))
                        # Pre-cache TTS
                        title = data.get("title", "")
                        artist = data.get("artist", "")
                        if title and data.get("state") == "playing" and self._player_type == "local":
                            from lib.tts import tts_precache
                            tts_text = f"{title}, by {artist}" if artist else title
                            asyncio.ensure_future(tts_precache(tts_text))
        except Exception as e:
            logger.debug("Could not recover media state from player: %s", e)

    async def stop(self):
        if self._auto_off_task:
            self._auto_off_task.cancel()
        try:
            await asyncio.wait_for(self.transport.stop(), timeout=3.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Transport stop timeout/error: %s", e)
        # Close media WebSocket clients (don't wait forever)
        for ws in list(self._media_ws_clients):
            try:
                await asyncio.wait_for(ws.close(), timeout=1.0)
            except (asyncio.TimeoutError, Exception):
                pass
        self._media_ws_clients.clear()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Router stopped")

    def get_menu(self) -> dict:
        """Build the current menu state from config menu order.

        The menu order in config.json is top-to-bottom.  Static views are
        always shown.  Source visibility is controlled by the ``visible``
        setting: ``always`` = always in menu, ``auto`` (default) = only
        when service is registered, ``never`` = never shown.
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
                    if source.visible == "never":
                        continue
                    if source.visible == "auto" and source.state == "gone":
                        continue
                    item = source.to_menu_item()
                    item["title"] = entry["title"]  # Use config title
                    items.append(item)

        active = self.registry.active_source
        return {
            "items": items,
            "active_source": self.registry.active_id,
            "active_player": active.player if active else None,
        }

    async def route_event(self, payload: dict):
        """Route an incoming event to the right destination."""
        self.touch_activity()
        action = payload.get("action", "")
        device_type = payload.get("device_type", "")
        active = self.registry.active_source

        # 1. Active source handles this action? → forward if device type is handled locally
        is_local = (device_type == "Audio" and self._handle_audio) or \
                   (device_type == "Video" and self._handle_video)
        if is_local and active and active.state in ("playing", "paused") and action in active.handles:
            # Stamp action timestamp — user interaction should carry a fresh ts
            # so downstream player_play/register calls aren't rejected as stale
            action_ts = time.monotonic()
            self._latest_action_ts = action_ts
            logger.info("-> %s: %s (active source)", active.id, action)
            await self._forward_to_source(active, {**payload, "action_ts": action_ts})
            return

        # 1a. Announce — TTS via player when source doesn't handle info/track/menu
        if is_local and action in ("menu", "info", "track") and self._media_state:
            if self._media_state.get("state") == "playing":
                asyncio.ensure_future(self._player_announce())
                return

        # 1b. No active source, stop → find any source that's actually playing
        #     (sources only register as "playing" when BS5c started the playback)
        if is_local and not active and action == "stop":
            playing = [s for s in self.registry.all_available()
                       if s.state in ("playing", "paused") and s.command_url
                       and "stop" in s.handles]
            if playing:
                for src in playing:
                    logger.info("-> %s: stop (playing, no active source)", src.id)
                    await self._forward_to_source(src, payload)
                return

        # 1b2. No active source → default source (if configured and handles the action)
        if is_local and not active and self._default_source_id:
            default = self.registry.get(self._default_source_id)
            if default and default.state != "gone" and default.command_url and action in default.handles:
                logger.info("-> %s: %s (default source)", default.id, action)
                await self._forward_to_source(default, payload)
                return

        # 1c. No active source, transport action → forward to player directly
        _TRANSPORT_ACTIONS = {
            "go": "toggle", "left": "prev", "right": "next",
            "up": "next", "down": "prev",
            "play": "toggle", "pause": "pause", "next": "next", "prev": "prev",
        }
        if is_local and not active and action in _TRANSPORT_ACTIONS:
            player_action = _TRANSPORT_ACTIONS[action]
            logger.info("-> player direct: %s (no active source)", player_action)
            try:
                async with self._session.post(
                    f"http://localhost:8766/player/{player_action}",
                    timeout=aiohttp.ClientTimeout(total=1.0),
                ) as resp:
                    logger.debug("Player responded: HTTP %d", resp.status)
            except Exception as e:
                logger.warning("Player direct send failed: %s", e)
            return

        # 2. Action matches a registered source id or mapped source button?
        source_id = self._source_buttons.get(action, action)
        source_by_action = self.registry.get(source_id)
        if source_by_action and source_by_action.state != "gone" and source_by_action.command_url:
            # Already active and playing — nothing to do
            if source_by_action == self.registry.active_source and source_by_action.state == "playing":
                logger.info("-> %s: already active, ignoring", source_id)
                return
            logger.info("-> %s: source button%s", source_id,
                        f" (mapped from {action})" if source_id != action else "")
            # Wake screen + power on speakers (may be in standby)
            asyncio.ensure_future(self._wake_screen())
            if self._volume and self._volume.is_on_cached() is False:
                asyncio.ensure_future(self._volume.power_on())
            # Stamp action timestamp — all downstream calls carry this to prevent races
            action_ts = time.monotonic()
            self._latest_action_ts = action_ts
            await self._forward_to_source(
                source_by_action, {**payload, "action": "activate", "action_ts": action_ts})
            return

        # 4. Volume keys — handle locally via adapter
        if action in ("volup", "voldown") and is_local:
            delta = self._volume_step if action == "volup" else -self._volume_step
            new_vol = max(0, min(100, self.volume + delta))
            logger.info("-> volume: %.0f%% -> %.0f%% (%s)", self.volume, new_vol, action)
            # Auto-power output on volume up (cached check only — no network query)
            if action == "volup" and self._volume and self._volume.is_on_cached() is False:
                asyncio.ensure_future(self._volume.power_on())
            # Fire-and-forget — adapter debounces internally, don't block event loop
            asyncio.ensure_future(self.set_volume(new_vol))
            return

        # 4b. Balance keys — handle locally via adapter
        if action in ("chup", "chdown") and is_local:
            delta = self._balance_step if action == "chup" else -self._balance_step
            new_bal = max(-20, min(20, self.balance + delta))
            logger.info("-> balance: %d -> %d (%s)", self.balance, new_bal, action)
            self.balance = new_bal
            if self._volume:
                asyncio.ensure_future(self._volume.set_balance(new_bal))
            return

        # 4c. Off — standby: stop playback, power off output, screen off
        if action == "off" and is_local:
            logger.info("-> standby (off)")
            # Stop player (source stays active so play/GO can resume it)
            asyncio.ensure_future(self._player_stop())
            # Power off speakers
            if self._volume:
                asyncio.ensure_future(self._volume.power_off())
            # Screen off
            asyncio.ensure_future(self._screen_off())
            # Still forward to HA (below) so it can handle automations

        # 4d. BLUE → JOIN default player
        if action == "blue" and is_local:
            join_cfg = cfg("join")
            default_player = join_cfg.get("default_player") if join_cfg else None
            if default_player:
                try:
                    async with self._session.post(
                        "http://localhost:8766/player/join",
                        json={"name": default_player},
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    ) as resp:
                        logger.info("BLUE→JOIN %s: HTTP %d",
                                    default_player, resp.status)
                except Exception as e:
                    logger.warning("BLUE→JOIN failed: %s", e)
                return  # consumed even on failure

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
        old_vol = self.volume
        self.volume = max(0, min(100, volume))
        if broadcast:
            asyncio.ensure_future(self._broadcast_volume())
        # Auto-power on volume increase (same as IR volup path)
        if self.volume > old_vol and self._volume and self._volume.is_on_cached() is False:
            await self._volume.power_on()
        await self._volume.set_volume(self.volume)

    async def report_volume(self, volume: float):
        """A device reports its current volume (e.g. Sonos says 'I'm at 40%').

        Only accepted when the volume adapter matches the player type —
        otherwise the player's volume is irrelevant (e.g. Sonos volume
        doesn't matter when output goes through BeoLab 5).
        """
        if not self._accept_player_volume:
            return
        self.volume = max(0, min(100, volume))
        logger.info("Volume reported: %.0f%%", self.volume)
        await self._broadcast_volume()

    async def _broadcast_volume(self):
        """Push current volume to UI clients so the arc stays in sync."""
        await self._broadcast("volume_update", {"volume": round(self.volume)})

    def _get_config_title(self, source_id: str) -> str | None:
        """Return the display title from config.json for a source, or None."""
        for entry in self._menu_order:
            if entry["id"] == source_id:
                return entry["title"]
        return None

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
                # menu_item events use the dedicated add/remove commands
                action = data.get("action", "")
                if action == "add":
                    payload = {"command": "add_menu_item", "params": data}
                elif action == "remove":
                    payload = {"command": "remove_menu_item", "params": data}
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

    async def _player_stop(self):
        """Stop the player service."""
        try:
            async with self._session.post(
                "http://localhost:8766/player/stop",
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                logger.debug("Player stop: HTTP %d", resp.status)
        except Exception:
            pass

    async def _player_announce(self):
        """TTS announce current track via the player service (handles ducking)."""
        if self._player_type != "local" or not self._media_state:
            return
        try:
            async with self._session.post(
                "http://localhost:8766/player/announce",
                timeout=aiohttp.ClientTimeout(total=10.0),
            ) as resp:
                logger.debug("Player announce: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Player announce failed: %s", e)

    async def _wake_screen(self):
        """Turn the screen on via input service."""
        try:
            async with self._session.post(
                INPUT_WEBHOOK_URL,
                json={"command": "screen_on"},
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                logger.debug("Screen on: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Screen on failed: %s", e)

    async def _screen_off(self):
        """Turn the screen off via input service."""
        try:
            async with self._session.post(
                INPUT_WEBHOOK_URL,
                json={"command": "screen_off"},
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                logger.debug("Screen off: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Screen off failed: %s", e)

    # ── Media routing (player → UI via router) ──

    async def _push_media(self, media_data: dict, reason: str = "update"):
        """Push a media update to all connected media WebSocket clients."""
        if not self._media_ws_clients:
            return
        msg = json.dumps({"type": "media_update", "data": media_data, "reason": reason})
        dead = set()
        for ws in self._media_ws_clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        self._media_ws_clients -= dead

    async def _handle_media_ws(self, request: web.Request) -> web.WebSocketResponse:
        """GET /router/ws — WebSocket endpoint for UI media updates."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._media_ws_clients.add(ws)
        logger.info("Media WS client connected (%d total)", len(self._media_ws_clients))
        try:
            # Send cached media state to new client
            if self._media_state:
                await ws.send_str(json.dumps({
                    "type": "media_update",
                    "data": self._media_state,
                    "reason": "client_connect",
                }))
            # Push-only — keep alive until client disconnects
            async for _msg in ws:
                pass
        finally:
            self._media_ws_clients.discard(ws)
            logger.info("Media WS client disconnected (%d remaining)",
                         len(self._media_ws_clients))
        return ws

    async def _handle_media_post(self, request: web.Request) -> web.Response:
        """POST /router/media — player posts media updates here."""
        try:
            payload = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response({"error": "invalid json"}, status=400)

        reason = payload.pop("_reason", "update")
        source_id = payload.pop("_source_id", None)
        action_ts = payload.pop("_action_ts", 0)
        if action_ts and action_ts < self._latest_action_ts:
            logger.warning("Dropped stale media update (ts=%.3f < latest=%.3f)",
                           action_ts, self._latest_action_ts)
            return web.json_response({"status": "ok", "dropped": True})
        if source_id and source_id != self.registry._active_id:
            logger.warning("Dropped media update from inactive source %s "
                           "(active: %s)", source_id, self.registry._active_id)
            return web.json_response({"status": "ok", "dropped": True})
        self._media_state = payload
        # Pre-cache TTS for instant announce on button press
        title = payload.get("title", "")
        artist = payload.get("artist", "")
        if title and payload.get("state") == "playing" and self._player_type == "local":
            from lib.tts import tts_precache
            tts_text = f"{title}, by {artist}" if artist else title
            asyncio.ensure_future(tts_precache(tts_text))

        await self._push_media(payload, reason)
        logger.info("Media update pushed to %d clients: %s",
                     len(self._media_ws_clients), reason)
        return web.json_response({"status": "ok"})


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
    for key in ("name", "command_url", "menu_preset", "handles", "navigate", "player", "auto_power", "action_ts"):
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

    router_instance.touch_activity()
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
    """POST /router/playback_override — clear active source on external playback.

    Called by the player service when it detects that playback started
    externally (e.g. from the Sonos app) rather than through a BS5c source.
    Clearing the active source lets transport commands (left/right/up/down)
    go directly to the player via _TRANSPORT_ACTIONS.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    force = data.get("force", False)
    action_ts = data.get("action_ts", 0)
    if action_ts:
        router_instance._latest_action_ts = action_ts
    active = router_instance.registry.active_source
    if active and force:
        logger.info("Playback override: clearing active source %s", active.id)
        await router_instance.registry.clear_active_source(router_instance)
        return web.json_response({"status": "ok", "cleared": True})
    reason = "no active source" if not active else "not forced"
    return web.json_response({"status": "ok", "cleared": False, "reason": reason})


_last_resync_time: float = 0.0
RESYNC_COOLDOWN = 5.0  # seconds — skip if last probe was recent


async def handle_resync(request: web.Request) -> web.Response:
    """POST /router/resync — re-probe all known sources for re-registration.

    Called by input.py when a new UI client connects, ensuring all sources
    are visible even if their original registration was missed.
    Debounced: skips if the last probe completed less than RESYNC_COOLDOWN ago.
    """
    global _last_resync_time
    now = time.monotonic()
    if now - _last_resync_time < RESYNC_COOLDOWN:
        logger.debug("Resync debounced (%.1fs since last)", now - _last_resync_time)
        return web.json_response({"status": "ok", "resynced": [], "debounced": True})
    # Set timestamp BEFORE probing to prevent concurrent resyncs from passing
    # the debounce check while the first one is still running.
    _last_resync_time = now
    resynced = await router_instance._probe_running_sources()
    return web.json_response({"status": "ok", "resynced": resynced})


async def handle_status(request: web.Request) -> web.Response:
    """GET /router/status — return current routing state."""
    active = router_instance.registry.active_source
    result = {
        "active_source": router_instance.registry.active_id,
        "active_source_name": active.name if active else None,
        "active_player": active.player if active else None,
        "active_view": router_instance.active_view,
        "volume": router_instance.volume,
        "output_device": router_instance.output_device,
        "transport_mode": router_instance.transport.mode,
        "latest_action_ts": router_instance._latest_action_ts,
        "sources": {
            s.id: {"state": s.state, "name": s.name, "player": s.player}
            for s in router_instance.registry.all_available()
        },
    }
    if router_instance._media_state:
        result["media"] = router_instance._media_state
    return web.json_response(result)


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
    app.router.add_post("/router/resync", handle_resync)
    app.router.add_get("/router/status", handle_status)
    app.router.add_get("/router/ws", router_instance._handle_media_ws)
    app.router.add_post("/router/media", router_instance._handle_media_post)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=ROUTER_PORT,
                shutdown_timeout=5.0,
                print=lambda msg: logger.info(msg))
