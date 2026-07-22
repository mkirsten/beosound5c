#!/usr/bin/env python3
"""
BeoSound 5c HEOS Player (beo-player-heos)

Monitors a Denon HEOS device for track changes, fetches artwork, and broadcasts
updates to the UI via the router. Also reports volume changes to the router so
the volume arc stays in sync.

Uses the pyheos library (HEOS CLI protocol, TCP port 1255, JSON). Unlike the
BluOS long-poll loop, HEOS pushes change events over the persistent CLI
connection, so monitoring is event-driven. One CLI connection reports every
HEOS player on the network (they mesh), so the player matching config
player.ip is selected after connecting.
"""

import asyncio
import logging
import os
import sys
import time

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.config import cfg
from lib.player_base import PlayerBase
from lib.timings import USER_ACTION_HORIZON

from pyheos import (
    ConnectionState,
    Heos,
    HeosError,
    HeosOptions,
    PlayState,
    const,
)

# Configuration
HEOS_IP = cfg("player", "ip", default="")
RESYNC_INTERVAL = 300    # safety resync in case a change event was missed

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-player-heos')


class HeosPlayerService(PlayerBase):
    """HEOS player service using the HEOS CLI protocol via pyheos."""

    id = "heos"
    name = "HEOS"
    port = 8766

    def __init__(self):
        super().__init__()
        self.ip = HEOS_IP
        self._heos = None
        self._player = None
        self._current_track_id = None
        self._event_unsubs = []
        self._player_unsub = None

    # ── PlayerBase abstract methods ──

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None) -> bool:
        if self._player is None:
            logger.error("Play failed: no HEOS player connected")
            return False
        try:
            if uri:
                logger.warning("HEOS does not support Spotify URIs — ignoring uri=%s", uri)
            if url:
                await self._player.play_url(url)
                logger.info("Playing URL: %s", url)
                return True
            # Resume playback
            return await self.resume()
        except Exception as e:
            logger.error("Play failed: %s", e)
            return False

    async def pause(self) -> bool:
        if self._player is None:
            return False
        try:
            await self._player.pause()
            logger.info("Paused")
            return True
        except Exception as e:
            logger.error("Pause failed: %s", e)
            return False

    async def resume(self) -> bool:
        if self._player is None:
            return False
        try:
            await self._player.play()
            logger.info("Resumed")
            return True
        except Exception as e:
            logger.error("Resume failed: %s", e)
            return False

    async def next_track(self) -> bool:
        if self._player is None:
            return False
        try:
            await self._player.play_next()
            logger.info("Next track")
            return True
        except Exception as e:
            logger.error("Next track failed: %s", e)
            return False

    async def prev_track(self) -> bool:
        if self._player is None:
            return False
        try:
            await self._player.play_previous()
            logger.info("Previous track")
            return True
        except Exception as e:
            logger.error("Previous track failed: %s", e)
            return False

    async def stop(self) -> bool:
        if self._player is None:
            return False
        try:
            await self._player.stop()
            logger.info("Stopped")
            return True
        except Exception as e:
            logger.error("Stop failed: %s", e)
            return False

    async def set_shuffle(self, enabled: bool) -> bool:
        if self._player is None:
            return False
        try:
            await self._player.set_play_mode(self._player.repeat, enabled)
            logger.info("Shuffle %s", "on" if enabled else "off")
            return True
        except Exception as e:
            logger.error("Set shuffle failed: %s", e)
            return False

    async def get_capabilities(self) -> list:
        return ["url_stream"]

    async def get_track_uri(self) -> str:
        return self._current_track_id or ""

    async def get_status(self) -> dict:
        base = await super().get_status()
        cached = self._cached_media_data or {}
        base.update({
            "speaker_ip": self.ip,
            "connected": (self._heos is not None
                          and self._heos.connection_state == ConnectionState.CONNECTED
                          and self._player is not None),
            "state": self._current_playback_state or "stopped",
            "volume": cached.get("volume"),
            "current_track": {
                "title": cached.get("title", "—"),
                "artist": cached.get("artist", "—"),
                "album": cached.get("album", "—"),
            } if cached else None,
            "artwork_cache_size": len(self._artwork_cache),
        })
        return base

    # ── PlayerBase hooks ──

    async def on_start(self):
        if not HEOS_IP:
            # Exit 0, not 1 — with Restart=on-failure this keeps the
            # misconfigured service stopped instead of crash-looping
            # (same convention as the player-type guard in
            # PlayerBase.start()).
            logger.error("No HEOS IP configured (set player.ip in "
                         "config) — exiting")
            # Tell systemd we started and are stopping. READY=1 must be
            # sent before exiting: the unit is Type=notify and the normal
            # READY=1 comes from watchdog_loop, which never runs on this
            # path — exiting without it makes systemd record
            # Result=protocol (a failure) and Restart=on-failure
            # crash-loops the service despite the exit code 0.
            from lib.watchdog import sd_notify
            sd_notify("READY=1\nSTATUS=No player.ip configured, exiting")
            sd_notify("STOPPING=1")
            sys.exit(0)
        logger.info("Starting HEOS player for %s", HEOS_IP)
        self._monitor_task = self._spawn(self._connect_loop(), name="heos_connect")

    async def on_stop(self):
        if self._player_unsub:
            self._player_unsub()
            self._player_unsub = None
        for unsub in self._event_unsubs:
            unsub()
        self._event_unsubs = []
        if self._heos is not None:
            try:
                await self._heos.disconnect()
            except Exception as e:
                logger.debug("HEOS disconnect error: %s", e)

    # ── Connection / monitoring (event-driven) ──

    async def _connect_loop(self):
        """Connect to the HEOS device and stay subscribed to change events.

        pyheos's auto_reconnect only covers drops after a successful
        connect — the initial connect must be retried here, or a
        powered-off device would leave the service dead until restart.
        """
        heos = Heos(HeosOptions(
            HEOS_IP,
            auto_reconnect=True,
            auto_reconnect_delay=10.0,
            heart_beat=True,
            all_progress_events=False,
        ))
        self._heos = heos
        self._event_unsubs.append(heos.add_on_connected(self._on_connected))
        self._event_unsubs.append(heos.add_on_disconnected(self._on_disconnected))

        consecutive_failures = 0
        while self.running:
            try:
                await heos.connect()
            except (HeosError, OSError) as e:
                # Back off while the device is unreachable. With a
                # wrong/offline player.ip every attempt fails — log the
                # first failure, back off to 30s, emit a periodic
                # summary, and stop as soon as a connect succeeds
                # (mirrors bluesound.py).
                consecutive_failures += 1
                if consecutive_failures == 1:
                    logger.error("HEOS connect failed (device unreachable?): %s", e)
                elif consecutive_failures % 120 == 0:
                    logger.warning("HEOS still unreachable (%d consecutive "
                                   "failed connects)", consecutive_failures)
                await asyncio.sleep(min(2 ** min(consecutive_failures, 5), 30.0))
                continue
            break
        if not self.running:
            return
        if consecutive_failures:
            logger.info("HEOS reachable after %d failed connects",
                        consecutive_failures)
        logger.info("Connected to HEOS @ %s", HEOS_IP)

        await self._attach_player()
        await self._sync_now_playing()

        # Safety resync — belt-and-braces against missed change events
        while self.running:
            try:
                await asyncio.sleep(RESYNC_INTERVAL)
                if self._player is None:
                    await self._attach_player()
                await self._sync_now_playing()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("HEOS resync error: %s", e)

    async def _attach_player(self):
        """Select our player from the mesh roster and subscribe to its events."""
        if self._heos is None:
            return
        try:
            players = await self._heos.get_players(refresh=True)
        except Exception as e:
            logger.warning("HEOS get_players failed: %s", e)
            return

        player = None
        for p in players.values():
            if p.ip_address == HEOS_IP:
                player = p
                break
        if player is None and len(players) == 1:
            player = next(iter(players.values()))
            logger.info("No HEOS player matches ip %s — using the only "
                        "player on the network: %s", HEOS_IP, player.name)
        if player is None:
            logger.error(
                "No HEOS player matches ip %s. Players on network: %s",
                HEOS_IP,
                ", ".join(f"{p.name} (pid={p.player_id}, ip={p.ip_address})"
                          for p in players.values()) or "none")
            return

        if self._player_unsub:
            self._player_unsub()
        self._player = player
        self._player_unsub = player.add_on_player_event(self._on_player_event)
        logger.info("Selected HEOS player: %s (pid=%d, ip=%s)",
                    player.name, player.player_id, player.ip_address)

    async def _on_connected(self):
        # Fires on (re)connect — pids can change across device
        # power-cycles, so re-select and resync from scratch.
        logger.info("HEOS connection established — resyncing")
        self._player = None
        await self._attach_player()
        await self._sync_now_playing()

    async def _on_disconnected(self):
        logger.warning("HEOS connection lost — pyheos will reconnect")

    async def _on_player_event(self, event: str):
        try:
            if event == const.EVENT_PLAYER_STATE_CHANGED:
                await self._handle_state_change()
            elif event == const.EVENT_PLAYER_NOW_PLAYING_CHANGED:
                await self._sync_now_playing()
            elif event == const.EVENT_PLAYER_NOW_PLAYING_PROGRESS:
                # HEOS pushes progress periodically (position in ms) —
                # update cached position without a full broadcast.
                if self._cached_media_data and self._player is not None:
                    self._cached_media_data["position"] = self._ms_to_time(
                        self._player.now_playing_media.current_position)
            elif event == const.EVENT_PLAYER_VOLUME_CHANGED:
                if self._player is not None:
                    self._spawn(
                        self.report_volume_to_router(int(self._player.volume)),
                        name="report_volume")
        except Exception as e:
            logger.error("Error handling HEOS event %s: %s", event, e)

    # ── State / media mapping ──

    def _map_state(self) -> str:
        state = self._player.state if self._player is not None else None
        if state == PlayState.PLAY:
            return "playing"
        if state == PlayState.PAUSE:
            return "paused"
        return "stopped"

    async def _handle_state_change(self):
        state = self._map_state()
        prev_state = self._current_playback_state

        # Wake trigger on transition to playing
        if state == "playing" and prev_state in ("paused", "stopped", None):
            logger.info("Playback started (was: %s), triggering wake", prev_state)
            self._spawn(self.trigger_wake(), name="trigger_wake")
            if self.seconds_since_command() > USER_ACTION_HORIZON:
                logger.info("External playback detected, clearing active source")
                self._spawn(
                    self.notify_router_playback_override(force=True),
                    name="playback_override")
        elif state == "stopped" and prev_state == "playing":
            if self.seconds_since_command() > USER_ACTION_HORIZON:
                logger.info("External stop detected")
                self._spawn(
                    self.notify_router_playback_override(force=True),
                    name="playback_override")

        self._current_playback_state = state

        # Broadcast on play<->pause/stop transitions with the same track —
        # the track didn't change, so the track_change broadcast in
        # _sync_now_playing won't fire (mirrors bluesound.py). Both
        # directions matter: pause/stop so the UI leaves the "playing"
        # state, and resume so an externally-paused UI doesn't stay stuck
        # on "paused" until the next track.
        state_transition = (
            (prev_state == "playing" and state in ("paused", "stopped"))
            or (prev_state in ("paused", "stopped") and state == "playing")
        )
        if state_transition and self._cached_media_data:
            state_data = dict(self._cached_media_data)
            state_data["state"] = state
            await self.broadcast_media_update(state_data, "state_change")

    async def _sync_now_playing(self):
        if self._player is None:
            return
        media = self._player.now_playing_media

        # URL/radio streams populate station rather than song
        title = media.song or media.station or ""
        artist = media.artist or ""
        album = media.album or ""

        track_id = f"{title}|{artist}|{album}"
        if track_id == self._current_track_id:
            return
        self._current_track_id = track_id

        artwork_base64 = None
        artwork_size = None
        if media.image_url:
            result = await self.fetch_artwork(media.image_url,
                                              session=self._http_session)
            if result:
                artwork_base64 = result["base64"]
                artwork_size = result["size"]

        state = self._map_state()
        self._current_playback_state = state

        media_data = {
            "title": title or "—",
            "artist": artist or "—",
            "album": album or "—",
            "artwork": f"data:image/jpeg;base64,{artwork_base64}" if artwork_base64 else None,
            "artwork_size": artwork_size,
            "position": self._ms_to_time(media.current_position),
            "duration": self._ms_to_time(media.duration),
            "state": state,
            "volume": int(self._player.volume or 0),
            "speaker_ip": self.ip,
            "service": str(media.source_id) if media.source_id else "heos",
            "quality": "",
            "uri": f"heos:{media.media_id}" if media.media_id else "",
            "timestamp": int(time.time()),
        }

        self._cached_media_data = media_data

        await self.broadcast_media_update(media_data, "track_change")
        logger.info("Track changed: %s — %s", artist, title)

        # External track change? Clear active source
        if self.seconds_since_command() > USER_ACTION_HORIZON:
            self._spawn(
                self.notify_router_playback_override(force=True),
                name="playback_override")

    # ── Helpers ──

    @staticmethod
    def _ms_to_time(ms) -> str:
        """Convert milliseconds to M:SS or H:MM:SS."""
        try:
            total = int(ms) // 1000
        except (ValueError, TypeError):
            return "0:00"
        if total >= 3600:
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            return f"{h}:{m:02d}:{s:02d}"
        return f"{total // 60}:{total % 60:02d}"


async def main():
    player = HeosPlayerService()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
