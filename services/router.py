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
from lib.correlation import (
    install_logging, correlation_middleware as cid_middleware,
    correlation_headers, set_id, new_id,
)
from lib.background_tasks import BackgroundTaskSet
from lib.endpoints import (
    INPUT_WEBHOOK,
    MIXER_ML_STANDBY,
    PLAYER_ANNOUNCE,
    PLAYER_JOIN,
    PLAYER_MEDIA,
    PLAYER_PLAY_FROM_QUEUE,
    PLAYER_STATE,
    PLAYER_STOP,
    PLAYER_TRACK_URI,
    player_url,
    spotify_canvas_url,
)
from lib.loop_monitor import LoopMonitor
from lib.lydbro import LydbroHandler
from lib.media_state import MediaState
from lib.spotify_canvas import extract_spotify_track_id
from lib.source_registry import (
    Source, SourceRegistry, DEFAULT_SOURCE_HANDLES, DEFAULT_SOURCE_PORTS,
)
from lib.transport import Transport
from lib.volume_adapters import create_volume_adapter, infer_volume_type
from lib.watchdog import watchdog_loop

logger = install_logging("beo-router")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROUTER_PORT = 8770
INPUT_WEBHOOK_URL = INPUT_WEBHOOK

# Static menu IDs — these are built-in views (not dynamic sources)
STATIC_VIEWS = {"showing", "system", "scenes", "playing"}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class EventRouter:
    def __init__(self):
        self.transport = Transport()
        self.registry = SourceRegistry()
        self.media = MediaState()
        self._lydbro = LydbroHandler(self)
        self.active_view = None
        self.volume = 0
        self.balance = 0
        self.output_device = cfg("volume", "output_name", default="BeoLab 5")
        self._volume_step = int(cfg("volume", "step", default=3))
        self._balance_step = 1
        self._pre_mute_vol: float = 30.0
        self._session: aiohttp.ClientSession | None = None
        self._volume = None
        self._accept_player_volume = False
        self._menu_order: list[dict] = []
        self._local_button_views: set[str] = {"menu/system"}
        self._default_source_id: str | None = cfg("remote", "default_source", default=None)
        self._source_buttons: dict[str, str] = {}
        self._handle_audio: bool = True
        self._handle_video: bool = False
        self._last_activity: float = time.monotonic()
        self._standby_dispatched: bool = False
        self._background_tasks = BackgroundTaskSet(logger, label="router")
        self._latest_action_ts: float = 0.0
        self._canvas_generation: int = 0
        self._music_video_generation: int = 0
        self._music_video_client = None
        self._music_video_pending_key: str = ""  # "artist||title" currently being looked up
        self._player_type: str = ""
        self._last_local_volume_set: float = 0.0

    # ── Background task tracking ──

    def _spawn(self, coro, *, name: str | None = None) -> asyncio.Task:
        """Launch a background coroutine with automatic lifecycle tracking."""
        return self._background_tasks.spawn(coro, name=name)

    # ── Menu parsing ──

    def _parse_menu(self):
        menu_cfg = cfg("menu")
        if not menu_cfg:
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

        player_type = str(cfg("player", "type", default="")).lower()
        if player_type == "sonos" and not any(i["id"] == "join" for i in items):
            join_entry = {"id": "join", "title": "JOIN", "config": {}}
            playing_idx = next((i for i, e in enumerate(items) if e["id"] == "playing"), -1)
            items.insert(playing_idx + 1, join_entry)

        for item in items:
            if "url" in item["config"]:
                pass
            elif item["id"] not in STATIC_VIEWS:
                handles = DEFAULT_SOURCE_HANDLES.get(item["id"], set())
                source = self.registry.create_from_config(item["id"], handles)
                source.from_config = True
                source.visible = item["config"].get("visible", "auto")

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

        _AUDIO_BUTTONS = {"radio", "amem", "cd", "n.radio", "n.music", "spotify"}
        _VIDEO_BUTTONS = {"tv", "v.aux", "a.aux", "vmem", "dvd", "dtv", "pc",
                          "youtube", "doorcam", "photo", "usb2"}
        mapped = set(self._source_buttons.keys())
        if cfg("remote", "handle_all", default=False):
            self._handle_audio = True
            self._handle_video = True
        elif mapped:
            self._handle_audio = bool(mapped & _AUDIO_BUTTONS)
            self._handle_video = bool(mapped & _VIDEO_BUTTONS)
        logger.info("Device type handling: audio=%s, video=%s (sources: %s)",
                    self._handle_audio, self._handle_video,
                    ", ".join(f"{s}->{sid}" for s, sid in self._source_buttons.items()) or "none")

        self._menu_order = items

    # ── Lifecycle ──

    async def start(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=2.0),
        )
        self._lydbro.setup()
        await self.transport.start()
        self._parse_menu()

        self._volume = create_volume_adapter(self._session)
        default_vol = int(cfg("volume", "default", default=30))
        # Adapter returns the hardware value (0..max_volume). Convert to
        # 0–100 UI scale for self.volume.
        initial_vol_hw = await self._volume.get_volume()
        if initial_vol_hw is not None and initial_vol_hw > 0:
            self.volume = self._hw_to_ui(initial_vol_hw)
        else:
            logger.info("Volume read as %s — using default %d", initial_vol_hw, default_vol)
            self.volume = default_vol

        adapter_type = infer_volume_type()
        player_type = str(cfg("player", "type", default="")).lower()
        self._player_type = player_type
        self._accept_player_volume = (adapter_type == player_type)
        if self._accept_player_volume:
            logger.info("Volume reports from player: accepted (%s)", adapter_type)
        else:
            logger.info("Volume reports from player: ignored (adapter=%s, player=%s)",
                         adapter_type, player_type)

        from lib.music_video import MusicVideoClient
        self._music_video_client = MusicVideoClient()
        logger.info("Music video lookup: enabled (Invidious, no API key required)")

        logger.info("Router started (transport: %s, output: %s, volume: %.0f%%)",
                     self.transport.mode, self.output_device, self.volume)

        self._spawn(self._startup_recovery(), name="startup_recovery")
        self._spawn(self._auto_standby_loop(), name="auto_standby")

    async def stop(self):
        # Cancel all tracked background tasks
        await self._background_tasks.cancel_all()
        try:
            await asyncio.wait_for(self.transport.stop(), timeout=3.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Transport stop timeout/error: %s", e)
        await self.media.close_all()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Router stopped")

    def touch_activity(self):
        self._last_activity = time.monotonic()
        self._standby_dispatched = False

    # ── Player queries ──

    async def _is_player_active(self) -> bool:
        try:
            async with self._session.get(
                PLAYER_STATE,
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("state") == "playing"
        except Exception:
            pass
        return False

    # ── Auto-standby ──

    async def _auto_standby_loop(self):
        timeout_min = int(cfg("auto_standby", "timeout", default=30))
        if timeout_min <= 0:
            return
        while True:
            try:
                await asyncio.sleep(600)  # check every 10 minutes
                idle_min = (time.monotonic() - self._last_activity) / 60
                if idle_min < timeout_min:
                    continue
                if await self._is_player_active():
                    self._standby_dispatched = False
                    continue
                if self._standby_dispatched:
                    continue
                logger.info("Auto-standby: idle %d min, nothing playing", int(idle_min))
                self._spawn(self._player_stop(), name="auto_standby_stop")
                if self._volume:
                    self._spawn(self._volume.power_off(), name="auto_standby_power_off")
                self._spawn(self._screen_off(), name="auto_standby_screen_off")
                self._standby_dispatched = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("Auto-standby loop error: %s", e)
                await asyncio.sleep(60)

    # ── Startup recovery ──

    async def _probe_running_sources(self, startup=False):
        await asyncio.sleep(1)
        persisted_id = self.registry.consume_persisted_active() if startup else None
        if persisted_id:
            logger.info("Startup resync — persisted active: %s", persisted_id)

        self.registry._resync_in_progress = True
        resynced = []
        try:
            for source_id, port in DEFAULT_SOURCE_PORTS.items():
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

        if persisted_id:
            await self.registry.restore_persisted_active(
                persisted_id, resynced, self,
            )

        return resynced

    async def _startup_recovery(self):
        await self._probe_running_sources(startup=True)
        try:
            async with self._session.get(
                PLAYER_MEDIA,
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and data.get("title"):
                        self.media.state = data
                        logger.info("Recovered media state: %s — %s",
                                     data.get("artist", ""), data.get("title", ""))
                        # Clients that connected before recovery finished got
                        # an empty client_connect replay — push to them too.
                        await self.media.push_media(data, "resync")
                        title = data.get("title", "")
                        artist = data.get("artist", "")
                        if title and data.get("state") == "playing" and self._player_type == "local":
                            from lib.tts import tts_precache
                            tts_text = f"{title}, by {artist}" if artist else title
                            self._spawn(tts_precache(tts_text), name="tts_precache")
        except Exception as e:
            logger.debug("Could not recover media state from player: %s", e)

    # ── Menu ──

    def get_menu(self) -> dict:
        items = []
        for entry in self._menu_order:
            entry_id = entry["id"]
            entry_cfg = entry.get("config", {})
            if "url" in entry_cfg:
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
                    item["title"] = entry["title"]
                    items.append(item)

        active = self.registry.active_source
        return {
            "items": items,
            "active_source": self.registry.active_id,
            "active_player": active.player if active else None,
        }

    # ── Event routing ──

    async def route_event(self, payload: dict):
        self.touch_activity()
        action = payload.get("action", "")
        device_type = payload.get("device_type", "")
        active = self.registry.active_source

        is_local = (device_type == "Audio" and self._handle_audio) or \
                   (device_type == "Video" and self._handle_video)

        # Actions that cause a track change — broadcast a skip hint so the UI
        # can immediately stop video panels without waiting for the full
        # track_change round-trip (covers all origins: button, BeoRemote, MQTT).
        _SKIP_ACTIONS = frozenset({"next", "prev", "left", "right"})

        # 0b. Color-button balance shortcuts.
        # GREEN  → R+4    YELLOW → L-4    HOME → centre (0)
        # Runs before any source / HA routing so it always wins on devices
        # where the volume adapter actually exposes balance. Safe no-op on
        # adapters whose set_tone returns None (Sonos / BlueSound).
        # Skipped when the active source explicitly claims the action
        # (e.g. radio with a user-bound GREEN/YELLOW favourite) so the
        # source's binding takes precedence over the global shortcut.
        _BAL_BUTTONS = {"green": 4, "yellow": -4, "home": 0}
        source_claims = (
            is_local and active and active.state in ("playing", "paused")
            and action in active.handles
        )
        if is_local and action in _BAL_BUTTONS and not source_claims and self._volume \
                and hasattr(self._volume, "set_tone"):
            bal = _BAL_BUTTONS[action]
            logger.info("-> balance: %s → %+d", action, bal)
            result = await self._volume.set_tone(balance=bal)
            if result is not None:
                return  # adapter handled it; skip HA / source routing

        # 1. Active source handles this action
        if is_local and active and active.state in ("playing", "paused") and action in active.handles:
            action_ts = self._latest_action_ts or 0
            logger.info("-> %s: %s (active source)", active.id, action)
            if action in _SKIP_ACTIONS:
                await self.media.broadcast("skip_hint", {})
            await self._forward_to_source(active, {**payload, "action_ts": action_ts})
            return

        # 1a. Announce — TTS
        if is_local and action in ("menu", "info", "track") and self.media.state:
            if self.media.state.get("state") in ("playing", "paused"):
                logger.info("-> announce: %s (%s)", action,
                            self.media.state.get("title", "?")[:40])
                self._spawn(self._player_announce(), name="player_announce")
                return

        # 1b. Stop with no active source
        if is_local and not active and action == "stop":
            playing = [s for s in self.registry.all_available()
                       if s.state in ("playing", "paused") and s.command_url
                       and "stop" in s.handles]
            if playing:
                for src in playing:
                    logger.info("-> %s: stop (playing, no active source)", src.id)
                    await self._forward_to_source(src, payload)
                return

        # 1b2. PLAY/GO with no active source — resume the most-recent
        # source instead of jumping to the configured default.  Triggered
        # by Beo4 GO and by the masterlink master role's PLAY-burst
        # detection.  Falls through to the default-source path (1b3) if
        # no last-active is known or it's no longer available.
        if (is_local and not active and action in ("go", "play")
                and self.registry.last_active_id):
            last = self.registry.get(self.registry.last_active_id)
            if last and last.state != "gone" and last.command_url \
                    and action in last.handles:
                action_ts = time.monotonic()
                self._latest_action_ts = action_ts
                logger.info("-> %s: %s (last source)", last.id, action)
                await self._forward_to_source(
                    last, {**payload, "action": "activate",
                           "action_ts": action_ts})
                return

        # 1b3. Default source
        if is_local and not active and self._default_source_id:
            default = self.registry.get(self._default_source_id)
            if default and default.state != "gone" and default.command_url and action in default.handles:
                action_ts = time.monotonic()
                self._latest_action_ts = action_ts
                # PLAY/GO from idle: convert to ``activate`` so the source
                # actually starts playback.  Forwarding ``go`` would map
                # to ``toggle`` on the player — a no-op when nothing is
                # loaded.  Mirrors the last-source fallback (1b2).
                forward_payload = {**payload, "action_ts": action_ts}
                if action in ("go", "play"):
                    forward_payload["action"] = "activate"
                logger.info("-> %s: %s (default source)", default.id,
                            forward_payload["action"])
                await self._forward_to_source(default, forward_payload)
                return

        # 1c. Transport actions direct to player.
        # "play" maps to resume (never toggle); "pause" stays pause; "go"
        # is the one true toggle. Mirrors the per-source action_maps.
        _TRANSPORT_ACTIONS = {
            "go": "toggle", "left": "prev", "right": "next",
            "up": "next", "down": "prev",
            "play": "resume", "pause": "pause", "next": "next", "prev": "prev",
        }
        if is_local and not active and action in _TRANSPORT_ACTIONS:
            player_action = _TRANSPORT_ACTIONS[action]
            logger.info("-> player direct: %s (no active source)", player_action)
            if player_action in ("next", "prev"):
                await self.media.broadcast("skip_hint", {})
            try:
                async with self._session.post(
                    player_url(f"/player/{player_action}"),
                    timeout=aiohttp.ClientTimeout(total=1.0),
                ) as resp:
                    logger.debug("Player responded: HTTP %d", resp.status)
            except Exception as e:
                logger.warning("Player direct send failed: %s", e)
            return

        # 2. Source button press
        source_id = self._source_buttons.get(action, action)
        source_by_action = self.registry.get(source_id)
        if source_by_action and source_by_action.state != "gone" and source_by_action.command_url:
            if source_by_action == self.registry.active_source and source_by_action.state == "playing":
                logger.info("-> %s: already active, waking screen", source_id)
                self._spawn(self._wake_screen(), name="wake_screen")
                return
            logger.info("-> %s: source button%s", source_id,
                        f" (mapped from {action})" if source_id != action else "")
            self._spawn(self._wake_screen(), name="wake_screen")
            if self._volume and self._volume.is_on_cached() is False:
                self._spawn(self._volume.power_on(), name="power_on")
            action_ts = time.monotonic()
            self._latest_action_ts = action_ts
            await self._forward_to_source(
                source_by_action, {**payload, "action": "activate", "action_ts": action_ts})
            return

        # 4. Volume
        if action in ("volup", "voldown") and is_local:
            if action == "volup" and self.volume == 0 and self._pre_mute_vol > 0:
                logger.info("-> unmute via volup: restoring %.0f%%", self._pre_mute_vol)
                if self._volume and self._volume.is_on_cached() is False:
                    self._spawn(self._volume.power_on(), name="vol_power_on")
                self._spawn(self.set_volume(self._pre_mute_vol), name="unmute")
            else:
                delta = self._volume_step if action == "volup" else -self._volume_step
                new_vol = max(0, min(100, self.volume + delta))
                logger.info("-> volume: %.0f%% -> %.0f%% (%s)", self.volume, new_vol, action)
                if action == "volup" and self._volume and self._volume.is_on_cached() is False:
                    self._spawn(self._volume.power_on(), name="vol_power_on")
                self._spawn(self.set_volume(new_vol), name="set_volume")
            return

        # 4a. Mute toggle — zero volume saves pre-mute level, restore on second press
        if action in ("mute", "p.mute") and is_local:
            if self.volume > 0:
                self._pre_mute_vol = self.volume
                logger.info("-> mute: saving %.0f%%, setting volume to 0", self._pre_mute_vol)
                self._spawn(self.set_volume(0), name="mute")
            else:
                logger.info("-> unmute: restoring volume to %.0f%%", self._pre_mute_vol)
                if self._volume and self._volume.is_on_cached() is False:
                    self._spawn(self._volume.power_on(), name="unmute_power_on")
                self._spawn(self.set_volume(self._pre_mute_vol), name="unmute")
            return

        # 4b. Balance
        if action in ("chup", "chdown") and is_local:
            delta = self._balance_step if action == "chup" else -self._balance_step
            new_bal = max(-20, min(20, self.balance + delta))
            logger.info("-> balance: %d -> %d (%s)", self.balance, new_bal, action)
            self.balance = new_bal
            if self._volume:
                self._spawn(self._volume.set_balance(new_bal), name="set_balance")
            return

        # 4c. Off / standby — intentional fallthrough to HA (§6) so HA also
        # receives the off event (e.g. to trigger a scene or power-off routine).
        # "alloff" (Beo4 ALL-off key, or a long-press on the BS5c power
        # button) additionally broadcasts STANDBY on the ML bus so link
        # speakers in other rooms power down with us.
        # device_type "All" is what Beo4 ALL-off and the power long-press
        # carry — is_local only covers Audio/Video, so accept All here.
        if action in ("off", "alloff") and (is_local or device_type == "All"):
            logger.info("-> standby (%s)", action)
            self._spawn(self._player_stop(), name="off_stop")
            if self._volume:
                self._spawn(self._volume.power_off(), name="off_power")
            self._spawn(self._screen_off(), name="off_screen")
            if action == "alloff":
                self._spawn(self._ml_all_standby(), name="alloff_ml")

        # 4d. BLUE → JOIN — returns only if JOIN is configured locally; otherwise
        # intentional fallthrough to HA so HA can handle the BLUE button.
        # The active-source claim check at step 1 has already absorbed BLUE
        # presses when the source binds it (e.g. radio favourite); reaching
        # here means no source claimed it.
        if action == "blue" and is_local:
            join_cfg = cfg("join")
            default_player = join_cfg.get("default_player") if join_cfg else None
            if default_player:
                try:
                    async with self._session.post(
                        PLAYER_JOIN,
                        json={"name": default_player},
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    ) as resp:
                        logger.info("BLUE→JOIN %s: HTTP %d", default_player, resp.status)
                except Exception as e:
                    logger.warning("BLUE→JOIN failed: %s", e)
                return

        # 5. Local button views
        if self.active_view in self._local_button_views and action in (
            "go", "left", "right", "up", "down",
        ):
            logger.info("-> suppressed: %s on %s (handled by UI)", action, self.active_view)
            return

        # 6. Everything else → HA
        logger.info("-> HA: %s (%s)", action, payload.get("device_type", ""))
        await self.transport.send_event(payload)

    async def _forward_to_source(self, source: Source, payload: dict):
        if not source.command_url or not self._session:
            return
        try:
            async with self._session.post(
                source.command_url,
                json=payload,
                headers=correlation_headers(),
                timeout=aiohttp.ClientTimeout(total=5.0),
            ) as resp:
                logger.debug("Source %s responded: HTTP %d", source.id, resp.status)
        except Exception as e:
            logger.warning("Failed to forward to %s: %s", source.id, e)

    # ── Volume ──

    # Cooldown window: how long to ignore player-reported volume after a
    # local (wheel-driven) set_volume. The Sonos monitor polls every 500ms
    # and reads the speaker's hardware volume — mid-transition reads race
    # with the commanded target and would flicker the UI arc between old
    # and new values if fed back in.
    _VOLUME_REPORT_COOLDOWN_S = 1.5

    # ── UI ↔ hardware volume scaling ──
    #
    # ``self.volume`` is always in 0–100 UI scale (what the wheel shows).
    # The adapter's ``_max_volume`` is the hardware value that UI 100
    # maps to — it's a safety ceiling, not a clamp on mid-range values.
    # Office's PowerLink/BeoLab 8000 (max=65 out of 127) is why this
    # matters: pre-scaling, wheel positions above ~UI 65 did nothing.
    # After scaling, the full wheel maps linearly onto 0..max_volume.

    def _ui_to_hw(self, ui: float) -> float:
        """Map a 0–100 UI volume to the adapter's 0–max_volume scale."""
        if self._volume is None or self._volume._max_volume <= 0:
            return 0.0
        return ui * self._volume._max_volume / 100.0

    def _hw_to_ui(self, hw: float) -> float:
        """Map an adapter 0–max_volume reading back to 0–100 UI scale."""
        if self._volume is None or self._volume._max_volume <= 0:
            return 0.0
        return hw * 100.0 / self._volume._max_volume

    async def set_volume(self, volume: float, broadcast: bool = True):
        old_vol = self.volume
        self.volume = max(0, min(100, volume))
        self._last_local_volume_set = time.monotonic()
        if broadcast:
            self._spawn(self._broadcast_volume(), name="broadcast_vol")
        if self._volume is None:
            logger.debug("set_volume: no adapter configured, skipping hardware call")
            return
        if self.volume > old_vol and self._volume.is_on_cached() is False:
            await self._volume.power_on()
        await self._volume.set_volume(self._ui_to_hw(self.volume))

    async def report_volume(self, volume: float):
        if not self._accept_player_volume:
            return
        # ``volume`` arrives in hardware scale (what the player read from
        # the speaker). Convert to UI scale before comparing / storing.
        ui_volume = self._hw_to_ui(volume)
        # Suppress player-reported volume shortly after a local command —
        # the Sonos polled read races with mid-transition speaker state
        # and would flicker the UI arc between old and new targets.
        since = time.monotonic() - self._last_local_volume_set
        if since < self._VOLUME_REPORT_COOLDOWN_S:
            logger.debug("Volume report ignored (%.2fs after local set): %.0f%%",
                         since, ui_volume)
            return
        if round(ui_volume) == round(self.volume):
            return
        self.volume = max(0, min(100, ui_volume))
        logger.info("Volume reported: %.0f%% (hw %.0f)", self.volume, volume)
        await self._broadcast_volume()

    async def _broadcast_volume(self):
        await self.media.broadcast("volume_update", {"volume": round(self.volume)})

    # ── Menu helpers ──

    def _get_config_title(self, source_id: str) -> str | None:
        for entry in self._menu_order:
            if entry["id"] == source_id:
                return entry["title"]
        return None

    def _get_after(self, source_id: str) -> str | None:
        prev_id = None
        for entry in self._menu_order:
            if entry["id"] == source_id:
                return prev_id
            prev_id = entry["id"]
        return None

    # ── Player control ──

    async def _player_stop(self):
        try:
            async with self._session.post(
                PLAYER_STOP,
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                logger.debug("Player stop: HTTP %d", resp.status)
        except Exception:
            pass

    async def _player_announce(self):
        if self._player_type != "local" or not self.media.state:
            return
        try:
            async with self._session.post(
                PLAYER_ANNOUNCE,
                json=self.media.state,
                timeout=aiohttp.ClientTimeout(total=10.0),
            ) as resp:
                logger.debug("Player announce: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Player announce failed: %s", e)

    # ── Screen control ──

    async def _set_backlight(self, on: bool):
        try:
            cmd = "screen_on" if on else "screen_off"
            async with self._session.post(
                INPUT_WEBHOOK_URL,
                json={"command": cmd},
                headers=correlation_headers(),
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                logger.debug("Backlight %s: HTTP %d", cmd, resp.status)
        except Exception as e:
            logger.warning("Backlight control failed: %s", e)

    async def _wake_screen(self):
        await self._set_backlight(True)
        await self.media.broadcast("navigate", {"page": "now_playing"})

    async def _screen_off(self):
        await self._set_backlight(False)

    async def _ml_all_standby(self):
        """Broadcast STANDBY on the ML bus via beo-masterlink.

        Best-effort: devices without a PC2 card don't run beo-masterlink,
        so an unreachable endpoint just means there's no ML bus to tell.
        """
        try:
            async with self._session.post(
                MIXER_ML_STANDBY,
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                if resp.status == 200:
                    logger.info("ML all-standby broadcast requested")
                else:
                    logger.warning("ML all-standby returned HTTP %d", resp.status)
        except Exception as e:
            logger.debug("ML all-standby skipped (no masterlink?): %s", e)

    # ── Canvas injection ──

    def _should_fetch_canvas(self, payload: dict) -> bool:
        if payload.get("canvas_url"):
            return False
        spotify_src = self.registry.get("spotify")
        if not spotify_src or spotify_src.state == "gone":
            return False
        return payload.get("state") == "playing"

    async def _inject_canvas(self, payload: dict, generation: int,
                              hinted_uri: str = ""):
        if hinted_uri:
            raw_uri = hinted_uri
        else:
            try:
                async with self._session.get(
                    PLAYER_TRACK_URI,
                    timeout=aiohttp.ClientTimeout(total=2.0),
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    raw_uri = data.get("track_uri", "")
            except Exception:
                return

        track_id = extract_spotify_track_id(raw_uri)
        if not track_id:
            return

        try:
            async with self._session.get(
                spotify_canvas_url(track_id),
                timeout=aiohttp.ClientTimeout(total=5.0),
            ) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
                canvas_url = data.get("canvas_url", "")
        except Exception:
            return

        if not canvas_url:
            return
        # Staleness guard: generation counter is bumped on every new media
        # update, so any in-flight canvas fetch for a previous track bails out.
        if self._canvas_generation != generation:
            logger.info("Canvas injection stale (gen %d != %d), dropping",
                        generation, self._canvas_generation)
            return
        current = self.media.state
        if not current:
            return
        current["canvas_url"] = canvas_url
        logger.info("Canvas injected: %s", canvas_url[:60])
        await self.media.push_media(current, "canvas_inject")

    # ── Music video injection ──

    async def _inject_music_video(self, payload: dict, generation: int,
                                   artist: str, title: str):
        def _clear_pending():
            # Only clear our own key — a newer track's lookup may already
            # have overwritten it, and blanking that would let
            # _handle_media_post spawn a duplicate concurrent lookup.
            if self._music_video_pending_key == f"{artist}||{title}":
                self._music_video_pending_key = ""

        try:
            url = await self._music_video_client.lookup(artist, title, self._session)
        except Exception as e:
            logger.warning("Music video lookup error for %s – %s: %s", artist, title, e)
            _clear_pending()
            return
        if not url:
            _clear_pending()
            return
        if self._music_video_generation != generation:
            logger.info("Music video injection stale (gen %d != %d), dropping",
                        generation, self._music_video_generation)
            return
        current = self.media.state
        if not current:
            return
        # Verify the same track is still playing
        if (current.get("artist", "").strip() != artist
                or current.get("title", "").strip() != title):
            logger.info("Music video arrived for different track, dropping")
            return
        current["music_video_url"] = url
        _clear_pending()
        logger.info("Music video injected for %s – %s", artist, title)
        await self.media.push_media(current, "music_video_inject")

    # ── Media POST handler ──

    async def _handle_media_post(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        rejection = self.media.validate_update(
            payload, self.registry.active_id, self._latest_action_ts)
        if rejection:
            return web.json_response(rejection)

        reason = payload.pop("_validated_reason", "update")
        # Player-supplied track URI (if any) — used for canvas injection
        # instead of calling back to /player/track_uri, which can observe
        # stale state while this POST is still in flight.
        hinted_uri = payload.pop("_track_uri", "")
        # Stamp a normalized Spotify track_id on the payload (when
        # detectable). The UI uses this for the canvas/artwork cycle's
        # render-time match check — without it, a canvas fetched for
        # the previous track can flash up briefly before the next
        # media_update arrives.
        # Source preference: ``_track_uri`` hint (added by
        # PlayerBase.broadcast_media_update via get_track_uri) is most
        # accurate, but on the very first eager broadcast after an
        # external Sonos start, the monitor loop hasn't committed
        # _current_track_id yet — so fall back to the Sonos-provided
        # ``uri`` field which fetch_media_data populates from
        # track_info directly.
        for source in (hinted_uri, payload.get("uri")):
            tid = extract_spotify_track_id(source) if source else None
            if tid:
                payload["track_id"] = tid
                break
        # Canvas injection for player-originated Spotify tracks
        source_id = payload.get("_validated_source_id")
        # Radio metadata is station/programme names, not artist+title — drop
        # any video URLs so a video carried over from a previous source
        # (Spotify canvas, music video) doesn't keep playing under radio.
        if source_id == "radio":
            payload["canvas_url"] = ""
            payload["music_video_url"] = ""
        # Bump on EVERY accepted update, not only when spawning a fetch —
        # otherwise an in-flight canvas lookup for the previous track passes
        # the staleness guard and injects the old canvas onto the new
        # track's state (e.g. after switching to a source that carries its
        # own canvas_url or posts a non-playing state).
        self._canvas_generation += 1
        if payload.get("canvas_url"):
            logger.info("Media has canvas_url: %s", payload["canvas_url"][:60])
        elif not source_id and self._should_fetch_canvas(payload):
            # Snapshot payload — it's mutated below (pop _validated_*) and
            # passed to media.accept_and_push, which may further mutate it.
            self._spawn(
                self._inject_canvas(dict(payload), self._canvas_generation,
                                    hinted_uri=hinted_uri),
                name="canvas_inject")

        # Music video injection — works for all sources except radio
        # (radio metadata is station/programme names, not artist+title).
        # Use the validated id captured above; ``_source_id`` itself was
        # popped by validate_update and is no longer on the payload.
        mv_artist = payload.get("artist", "").strip()
        mv_title = payload.get("title", "").strip()
        mv_source = source_id or ""
        # Allow lookup on track_change/external_control regardless of state —
        # Sonos briefly reports "stopped" during track transitions even when
        # a new track is about to play. Also trigger on resync (router restart)
        # so the video is recovered for the current track without waiting for
        # the next skip. Gate on state only for plain "update" reason.
        mv_state_ok = (reason in ("track_change", "external_control", "resync")
                       or payload.get("state") == "playing")
        if (self._music_video_client
                and mv_artist and mv_title
                and mv_source != "radio"
                and mv_state_ok
                and not payload.get("music_video_url")):
            cached = self._music_video_client.get_cached(mv_artist, mv_title)
            if cached:
                # Instant cache hit — include in this push
                payload["music_video_url"] = cached
                logger.info("Music video cache hit for %s – %s", mv_artist, mv_title)
            elif cached is None:
                # Not yet looked up — spawn background lookup (once per track)
                pending_key = f"{mv_artist}||{mv_title}"
                if pending_key != self._music_video_pending_key:
                    self._music_video_pending_key = pending_key
                    self._music_video_generation += 1
                    self._spawn(
                        self._inject_music_video(dict(payload), self._music_video_generation,
                                                 mv_artist, mv_title),
                        name="music_video_inject")
            # cached == "" → no video found for this track; skip silently

        # Pre-cache TTS
        title = payload.get("title", "")
        artist = payload.get("artist", "")
        if title and payload.get("state") == "playing" and self._player_type == "local":
            from lib.tts import tts_precache
            tts_text = f"{title}, by {artist}" if artist else title
            self._spawn(tts_precache(tts_text), name="tts_precache")

        # Remove internal fields before caching
        payload.pop("_validated_source_id", None)
        payload.pop("_validated_reason", None)

        await self.media.accept_and_push(payload, reason)
        return web.json_response({"status": "ok"})

    # ── WS handler ──

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        return await self.media.handle_ws(
            request,
            get_source_snapshot=lambda: self.registry.active_source,
            get_volume=lambda: self.volume,
        )

    async def _handle_media_get(self, request: web.Request) -> web.Response:
        """GET /router/media — return cached media state for UI resync.

        The UI calls this when entering the playing view if its in-memory
        mediaInfo looks stale/empty. Belt-and-suspenders for the case
        where a media broadcast was missed (e.g. the WS reconnected after
        the last update, or a Sonos external start raced view entry).
        """
        return web.json_response(self.media.state or {})


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------
router_instance = EventRouter()


async def handle_event(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    await router_instance.route_event(payload)
    return web.json_response({"status": "ok"})


async def handle_source(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    src_id = data.get("id")
    state = data.get("state")
    if not src_id or not state:
        return web.json_response({"error": "id and state required"}, status=400)
    if state not in ("available", "playing", "paused", "gone"):
        return web.json_response({"error": "invalid state"}, status=400)

    fields = {}
    for key in ("name", "command_url", "menu_preset", "handles", "navigate",
                "player", "auto_power", "action_ts", "manages_queue"):
        if key in data:
            fields[key] = data[key]

    result = await router_instance.registry.update(src_id, state, router_instance, **fields)
    return web.json_response({
        "status": "ok", "source": src_id,
        "active_source": router_instance.registry.active_id,
        **result,
    })


async def handle_menu(request: web.Request) -> web.Response:
    return web.json_response(router_instance.get_menu())


async def handle_volume_set(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    volume = data.get("volume")
    if volume is None or not isinstance(volume, (int, float)):
        return web.json_response({"error": "missing or invalid 'volume'"}, status=400)
    router_instance.touch_activity()
    await router_instance.set_volume(float(volume), broadcast=False)
    return web.json_response({"status": "ok", "volume": router_instance.volume})


async def handle_volume_report(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    volume = data.get("volume")
    if volume is None or not isinstance(volume, (int, float)):
        return web.json_response({"error": "missing or invalid 'volume'"}, status=400)
    await router_instance.report_volume(float(volume))
    return web.json_response({"status": "ok", "volume": router_instance.volume})


async def handle_tone(request: web.Request) -> web.Response:
    """GET  /router/tone — current tone state from the volume adapter,
                           or {"supported": false} if the adapter can't
                           do tone (e.g. Sonos / Bluesound / BeoLab 5).
       POST /router/tone  body: any subset of
            {"bass": int -10..10, "treble": int, "balance": int,
             "loudness": bool}
    """
    adapter = router_instance._volume
    if adapter is None or not hasattr(adapter, "set_tone"):
        return web.json_response({"supported": False})

    if request.method == "GET":
        state = await adapter.get_tone()
        if state is None:
            return web.json_response({"supported": False})
        return web.json_response({"supported": True, **state})

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    body: dict = {}
    for key in ("bass", "treble", "balance"):
        if key in data:
            try:
                v = int(data[key])
            except (TypeError, ValueError):
                return web.json_response(
                    {"error": f"'{key}' must be an integer"}, status=400)
            if v < -10 or v > 10:
                return web.json_response(
                    {"error": f"'{key}' must be between -10 and 10"},
                    status=400)
            body[key] = v
    if "loudness" in data:
        body["loudness"] = bool(data["loudness"])
    if not body:
        return web.json_response({"error": "no tone fields"}, status=400)

    result = await adapter.set_tone(**body)
    if result is None:
        return web.json_response({"supported": False, "applied": body})
    return web.json_response({"supported": True, "applied": body, **result})


async def handle_output_off(request: web.Request) -> web.Response:
    if router_instance._volume:
        await router_instance._volume.power_off()
        logger.info("Output powered off via /output/off")
        return web.json_response({"status": "ok", "output": "off"})
    return web.json_response({"status": "ok", "output": "no_adapter"})


async def handle_output_on(request: web.Request) -> web.Response:
    if router_instance._volume:
        await router_instance._volume.power_on()
        logger.info("Output powered on via /output/on")
        return web.json_response({"status": "ok", "output": "on"})
    return web.json_response({"status": "ok", "output": "no_adapter"})


async def handle_view(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    view = data.get("view")
    old = router_instance.active_view
    router_instance.active_view = view
    if old != view:
        logger.info("View changed: %s -> %s", old, view)
    return web.json_response({"status": "ok", "active_view": view})


async def handle_playback_override(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        data = {}
    force = data.get("force", False)
    action_ts = data.get("action_ts", 0)
    # ``push_idle=False`` lets the caller suppress the idle media_update
    # that clear_active_source normally broadcasts — used by the player's
    # eager-broadcast path on external start, where the player is about
    # to push fresh media and the idle push would wipe it.
    push_idle = data.get("push_idle", True)
    if action_ts:
        router_instance._latest_action_ts = action_ts
    active = router_instance.registry.active_source
    if active and force:
        if active.manages_queue:
            return web.json_response({
                "status": "ok", "cleared": False,
                "reason": f"{active.id} manages own playback"})
        logger.info("Playback override: clearing active source %s (push_idle=%s)",
                    active.id, push_idle)
        await router_instance.registry.clear_active_source(
            router_instance, push_idle=push_idle)
        return web.json_response({"status": "ok", "cleared": True})
    reason = "no active source" if not active else "not forced"
    return web.json_response({"status": "ok", "cleared": False, "reason": reason})


_last_resync_time: float = 0.0
RESYNC_COOLDOWN = 5.0


async def handle_resync(request: web.Request) -> web.Response:
    global _last_resync_time
    now = time.monotonic()
    if now - _last_resync_time < RESYNC_COOLDOWN:
        return web.json_response({"status": "ok", "resynced": [], "debounced": True})
    _last_resync_time = now
    resynced = await router_instance._probe_running_sources()
    return web.json_response({"status": "ok", "resynced": resynced})


async def handle_status(request: web.Request) -> web.Response:
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
    if router_instance.media.state:
        result["media"] = router_instance.media.state
    return web.json_response(result)


async def handle_queue(request: web.Request) -> web.Response:
    start = int(request.query.get("start", "0"))
    max_items = int(request.query.get("max_items", "50"))

    source = router_instance.registry.active_source
    source_queue_url = None
    player_queue_url = player_url(
        f"/player/queue?start={start}&max_items={max_items}"
    )

    if source and source.command_url:
        base = source.command_url.rsplit("/command", 1)[0]
        source_queue_url = f"{base}/queue?start={start}&max_items={max_items}"

    if source and (source.manages_queue or source.player == "local"):
        primary, secondary = source_queue_url, player_queue_url
    else:
        primary, secondary = player_queue_url, source_queue_url

    async def _fetch_queue(url):
        if not url:
            return None
        try:
            async with router_instance._session.get(
                url, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("tracks"):
                        return data
        except Exception as e:
            logger.debug("Queue fetch from %s failed: %s", url, e)
        return None

    result = await _fetch_queue(primary)
    if not result:
        result = await _fetch_queue(secondary)
    if not result:
        m = router_instance.media.state
        if m:
            result = {
                "tracks": [{
                    "id": "q:0",
                    "title": m.get("title", ""),
                    "artist": m.get("artist", ""),
                    "album": m.get("album", ""),
                    "artwork": m.get("artwork", ""),
                    "index": 0, "current": True,
                }],
                "current_index": 0, "total": 1,
            }
        else:
            result = {"tracks": [], "current_index": -1, "total": 0}

    return web.json_response(result)


async def handle_queue_play(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    position = data.get("position", 0)
    source = router_instance.registry.active_source

    if source and (source.manages_queue or source.player == "local"):
        if source.command_url:
            try:
                async with router_instance._session.post(
                    source.command_url,
                    json={"command": "play_index", "index": position},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return web.json_response({"status": "ok"})
            except Exception as e:
                logger.warning("Source play_index failed: %s", e)
        return web.json_response({"status": "error"}, status=500)
    else:
        try:
            async with router_instance._session.post(
                PLAYER_PLAY_FROM_QUEUE,
                json={"position": position},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()
                return web.json_response(result)
        except Exception as e:
            logger.warning("Player play_from_queue failed: %s", e)
            return web.json_response({"status": "error"}, status=500)


async def handle_broadcast(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    event_type = payload.get("type", "unknown")
    data = payload.get("data", {})
    await router_instance.media.broadcast(event_type, data)
    return web.json_response({"status": "ok"})


async def handle_touch(request: web.Request) -> web.Response:
    """POST /router/touch — reset the auto-standby idle clock.

    Called by beo-input on `wake` (and similar commands that come from
    HA bypassing the router) so the router's auto-standby loop knows
    the device is in active use. Without this, `_standby_dispatched`
    stays True forever after the first dispatch, because HA's
    wake/screen_on commands never hit `route_event` or `/router/volume`.
    """
    router_instance.touch_activity()
    return web.json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
async def on_startup(app: web.Application):
    await router_instance.start()
    # Event-loop lag detector: warns when a sync call blocks the loop
    # for more than 300ms.  See services/lib/loop_monitor.py.
    app["loop_monitor"] = LoopMonitor().start()
    asyncio.create_task(watchdog_loop())


async def on_cleanup(app: web.Application):
    monitor = app.get("loop_monitor")
    if monitor is not None:
        await monitor.stop()
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
    app = web.Application(middlewares=[cid_middleware, cors_middleware])
    app.router.add_post("/router/event", handle_event)
    app.router.add_post("/router/source", handle_source)
    app.router.add_get("/router/menu", handle_menu)
    app.router.add_post("/router/view", handle_view)
    app.router.add_post("/router/volume", handle_volume_set)
    app.router.add_post("/router/volume/report", handle_volume_report)
    app.router.add_get("/router/tone", handle_tone)
    app.router.add_post("/router/tone", handle_tone)
    app.router.add_post("/router/playback_override", handle_playback_override)
    app.router.add_post("/router/output/off", handle_output_off)
    app.router.add_post("/router/output/on", handle_output_on)
    app.router.add_post("/router/resync", handle_resync)
    app.router.add_get("/router/status", handle_status)
    app.router.add_get("/router/ws", router_instance._handle_ws)
    app.router.add_post("/router/media", router_instance._handle_media_post)
    app.router.add_get("/router/media", router_instance._handle_media_get)
    app.router.add_post("/router/broadcast", handle_broadcast)
    app.router.add_post("/router/touch", handle_touch)
    app.router.add_get("/router/queue", handle_queue)
    app.router.add_post("/router/queue/play", handle_queue_play)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=ROUTER_PORT,
                shutdown_timeout=5.0, access_log=None,
                print=lambda msg: logger.info(msg))
