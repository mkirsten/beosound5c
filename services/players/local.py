#!/usr/bin/env python3
"""
BeoSound 5c Local Player (beo-player-local)

Two backends:
  - mpv: plays URL streams (USB, CD, Plex, News)
  - go-librespot: plays Spotify URIs via the Spotify Connect protocol

Sources use the standard player HTTP API (POST /player/play, etc.) —
no source code changes needed. Dispatch is based on what's passed:
  - play(uri=...) → go-librespot (Spotify share URLs / URIs)
  - play(url=...) → mpv (stream URLs)

For mpv, sources pre-broadcast their own metadata. For go-librespot,
this player monitors the WebSocket event stream and broadcasts metadata
via the router (same pattern as the Sonos player).
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.player_base import PlayerBase
from lib.librespot import LibrespotClient, share_url_to_uri
from lib.timings import USER_ACTION_HORIZON

IPC_SOCKET = '/tmp/beo-player-local.sock'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-player-local')


class LocalPlayer(PlayerBase):
    """Local player service with mpv + go-librespot backends."""

    id = "local"
    name = "Local"
    port = 8766

    def __init__(self):
        super().__init__()
        # mpv backend
        self._process: subprocess.Popen | None = None
        self._watcher_task: asyncio.Task | None = None
        # go-librespot backend
        self._librespot = LibrespotClient(on_event=self._on_librespot_event)
        # Which backend is currently active: "mpv", "librespot", or None
        self._active_backend: str | None = None
        self._stop_time: float = 0  # monotonic time of last explicit stop

    @property
    def _librespot_available(self) -> bool:
        return self._librespot.connected

    # ── PlayerBase abstract methods ──

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None) -> bool:
        if uri:
            # Spotify share URL or native URI → go-librespot
            spotify_uri = share_url_to_uri(uri)
            if spotify_uri:
                if not self._librespot_available:
                    logger.warning("Spotify URI but go-librespot not available: %s", uri)
                    return False
                # Stop mpv if it was playing
                await self._kill_mpv()

                skip_to = track_uri if track_uri else None
                ok = await self._librespot.play(spotify_uri, skip_to_uri=skip_to)
                if ok:
                    self._active_backend = 'librespot'
                    self._current_playback_state = 'playing'
                    logger.info("Playing via go-librespot: %s", spotify_uri)
                    return True
                logger.error("go-librespot play failed for %s", spotify_uri)
                return False

            # Non-Spotify URI: local player has no DRM-capable client for
            # Tidal / Apple Music. Sonos / BlueSound handle those natively.
            service = "Tidal" if "tidal.com" in uri else \
                      "Apple Music" if "music.apple.com" in uri else \
                      "this service"
            logger.warning(
                "Local player cannot play %s (DRM-protected stream). "
                "Only Spotify is supported locally (via go-librespot); "
                "Tidal / Apple Music require a Sonos or BlueSound player. URI: %s",
                service, uri)
            return False

        if url:
            # Stop go-librespot playback if it was active
            if self._active_backend == 'librespot':
                await self._librespot.pause()

            # Kill existing mpv if running
            await self._kill_mpv()

            try:
                env = os.environ.copy()
                env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
                self._process = subprocess.Popen([
                    'mpv', '--ao=pulse', url,
                    '--no-video', '--no-terminal',
                    f'--input-ipc-server={IPC_SOCKET}',
                ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
                self._active_backend = 'mpv'
                self._current_playback_state = 'playing'
                self._watcher_task = asyncio.create_task(self._watch_process())
                logger.info("Playing URL via mpv: %s", url)
                return True
            except Exception as e:
                logger.error("mpv play failed: %s", e)
                self._current_playback_state = 'stopped'
                return False

        # No URI/URL — try resume on active backend
        return await self.resume()

    async def pause(self) -> bool:
        if self._active_backend == 'librespot':
            ok = await self._librespot.pause()
            if ok:
                self._current_playback_state = 'paused'
                logger.info("Paused (librespot)")
            return ok

        if self._process and self._process.poll() is None:
            ok = await self._mpv_ipc('set_property', 'pause', True)
            if ok:
                self._current_playback_state = 'paused'
                logger.info("Paused (mpv)")
            return ok
        return False

    async def resume(self) -> bool:
        if self._active_backend == 'librespot':
            ok = await self._librespot.resume()
            if ok:
                self._current_playback_state = 'playing'
                logger.info("Resumed (librespot)")
            return ok

        if self._process and self._process.poll() is None:
            ok = await self._mpv_ipc('set_property', 'pause', False)
            if ok:
                self._current_playback_state = 'playing'
                logger.info("Resumed (mpv)")
            return ok
        return False

    async def next_track(self) -> bool:
        if self._active_backend == 'librespot':
            return await self._librespot.next_track()
        # mpv: sources manage their own track lists
        return False

    async def prev_track(self) -> bool:
        if self._active_backend == 'librespot':
            return await self._librespot.prev_track()
        return False

    async def stop(self) -> bool:
        self._stop_time = time.monotonic()
        if self._active_backend == 'librespot':
            await self._librespot.stop_playback()
        await self._kill_mpv()
        self._current_playback_state = 'stopped'
        self._active_backend = None
        logger.info("Stopped")
        return True

    async def set_shuffle(self, enabled: bool) -> bool:
        if not self._librespot_available or self._active_backend != 'librespot':
            logger.info("set_shuffle: librespot not active")
            return False
        ok = await self._librespot.set_shuffle(enabled)
        if ok:
            logger.info("Shuffle %s", "on" if enabled else "off")
        return ok

    async def play_track_radio(self, track_uri) -> bool:
        """Start Spotify track radio seeded by *track_uri* via go-librespot.

        Passes the ``spotify:station:track:<id>`` context URI to librespot;
        go-librespot resolves it through Spotify's recommender just like
        any other context.
        """
        if not track_uri or "spotify:track:" not in track_uri:
            logger.warning("play_track_radio: invalid track_uri %r", track_uri)
            return False
        if not self._librespot_available:
            logger.warning("play_track_radio: go-librespot not available")
            return False
        track_id = track_uri.split(":")[-1]
        station_uri = f"spotify:station:track:{track_id}"
        await self._kill_mpv()
        ok = await self._librespot.play(station_uri)
        if ok:
            self._active_backend = 'librespot'
            self._current_playback_state = 'playing'
            logger.info("Playing Spotify track radio seeded by %s", track_id[:12])
            return True
        logger.error("go-librespot rejected station URI %s", station_uri)
        return False

    async def get_capabilities(self) -> list:
        caps = ["url_stream"]
        if self._librespot_available:
            caps.insert(0, "spotify")
            caps.append("spotify_track_radio")
        return caps

    async def get_track_uri(self) -> str:
        if self._active_backend == 'librespot':
            status = await self._librespot.status()
            if status and status.get('track'):
                return status['track'].get('uri', '')
        return ''

    async def get_spotify_status(self) -> dict:
        authenticated = await self._librespot.is_authenticated() if self._librespot_available else False
        return {
            "available": self._librespot_available,
            "authenticated": authenticated,
        }

    async def get_status(self) -> dict:
        base = await super().get_status()
        base["state"] = self._current_playback_state or "stopped"
        base["active_backend"] = self._active_backend
        base["mpv_running"] = self._process is not None and self._process.poll() is None
        base["librespot_available"] = self._librespot_available
        return base

    # ── PlayerBase hooks ──

    async def on_start(self):
        # Kill any orphaned mpv from a previous crash/restart
        try:
            result = subprocess.run(
                ['pkill', '-f', f'--input-ipc-server={IPC_SOCKET}'],
                capture_output=True)
            if result.returncode == 0:
                logger.info("Killed orphaned mpv process(es)")
        except Exception:
            pass

        # Start librespot client — systemd ExecStartPost guarantees
        # go-librespot is listening before this service starts.
        # The event stream handles runtime reconnection if it restarts.
        await self._librespot.start()
        logger.info("Local player ready (mpv + go-librespot backends)")

    async def on_stop(self):
        await self._kill_mpv()
        await self._librespot.stop()

    # ── go-librespot event handling ──

    def _recently_stopped(self, cooldown=5.0) -> bool:
        """True if stop() was called within the last `cooldown` seconds."""
        return self._stop_time and time.monotonic() - self._stop_time < cooldown

    async def _on_librespot_event(self, event_type: str, data: dict):
        """Handle events from go-librespot's WebSocket stream."""

        if event_type == 'metadata':
            # Ignore track events after explicit stop (go-librespot only pauses,
            # so it may auto-advance and fire metadata for the next track) —
            # but accept them when NO backend is active: on an external
            # Spotify Connect start (phone → go-librespot) the 'metadata'
            # event arrives BEFORE 'playing' sets the backend, and dropping
            # it leaves the playing view empty until the next track.
            if self._active_backend not in (None, 'librespot'):
                return
            if self._active_backend is None and self._recently_stopped():
                return  # post-stop auto-advance noise, not an external start
            # New track loaded — broadcast media update
            artists = data.get('artist_names', [])
            media_data = {
                'title': data.get('name', ''),
                'artist': ', '.join(artists) if artists else '',
                'album': data.get('album_name', ''),
                'artwork': data.get('album_cover_url', ''),
                'duration': data.get('duration', 0),
                'position': data.get('position', 0),
                'state': 'playing',
            }
            # Fetch and embed artwork as base64 (same as Sonos player)
            artwork_url = data.get('album_cover_url')
            if artwork_url:
                art = await self.fetch_artwork(artwork_url)
                if art:
                    media_data['artwork'] = f"data:image/jpeg;base64,{art['base64']}"
            await self.broadcast_media_update(media_data, reason='track_change')
            logger.info("Librespot track: %s — %s",
                        media_data['artist'], media_data['title'])

        elif event_type == 'playing':
            # Ignore play events right after explicit stop — go-librespot
            # may briefly resume/advance before the pause takes effect
            if self._recently_stopped():
                return
            was_stopped = self._current_playback_state in ('stopped', None)
            self._current_playback_state = 'playing'
            self._active_backend = 'librespot'
            if was_stopped:
                await self.trigger_wake()
                await self.trigger_output_on()
            # Detect external control (someone started from Spotify app)
            play_origin = data.get('play_origin', '')
            if play_origin and play_origin != 'go-librespot':
                if self.seconds_since_command() > USER_ACTION_HORIZON:
                    logger.info("External Spotify control detected (origin=%s)",
                                play_origin)
                    await self.notify_router_playback_override(force=True)

        elif event_type == 'paused':
            # Only update state if librespot is the active backend —
            # avoids overwriting 'stopped' set by stop() with 'paused'
            # (go-librespot sends 'paused' because stop is implemented as pause)
            if self._active_backend == 'librespot':
                self._current_playback_state = 'paused'

        elif event_type == 'stopped':
            self._current_playback_state = 'stopped'

        elif event_type == 'inactive':
            # Playback transferred away from this device
            self._current_playback_state = 'stopped'
            self._active_backend = None
            logger.info("go-librespot became inactive (playback transferred away)")
            await self.notify_router_playback_override(force=True)

    # ── mpv management ──

    async def _kill_mpv(self):
        """Terminate any running mpv process."""
        if self._watcher_task:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except (asyncio.CancelledError, Exception):
                pass
            self._watcher_task = None

        if self._process:
            self._process.terminate()
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._process.wait, 2)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    async def _watch_process(self):
        """Watch for mpv process exit."""
        try:
            start = asyncio.get_event_loop().time()
            while self._process and self._process.poll() is None:
                await asyncio.sleep(0.25)
            if self._current_playback_state == 'playing':
                elapsed = asyncio.get_event_loop().time() - start
                self._current_playback_state = 'stopped'
                # Log stderr if mpv died quickly (likely a playback error)
                if elapsed < 5.0 and self._process and self._process.stderr:
                    try:
                        err = self._process.stderr.read(2000)
                        if err:
                            logger.warning("mpv exited after %.1fs, stderr: %s",
                                           elapsed, err.decode(errors='replace').strip())
                    except Exception:
                        pass
                self._process = None
                logger.info("mpv process ended (after %.1fs)", elapsed)
        except asyncio.CancelledError:
            pass

    async def _mpv_ipc(self, *args) -> bool:
        """Send a command to mpv via IPC socket."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._mpv_ipc_sync, *args)

    def _mpv_ipc_sync(self, *args) -> bool:
        import socket as sock
        s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
        s.settimeout(2)
        try:
            s.connect(IPC_SOCKET)
            cmd = json.dumps({'command': list(args)}) + '\n'
            s.sendall(cmd.encode())
            return True
        except Exception as e:
            logger.error("mpv IPC error: %s", e)
            return False
        finally:
            s.close()

    async def fade_volume(self, target: float, duration: float = 0.5):
        """Smoothly fade mpv volume to target (0-100) over duration seconds."""
        if self._active_backend != 'mpv' or not self._process or self._process.poll() is not None:
            return
        steps = 10
        step_delay = duration / steps
        current = getattr(self, '_mpv_volume', 100.0)
        for i in range(1, steps + 1):
            vol = current + (target - current) * (i / steps)
            await self._mpv_ipc('set_property', 'volume', vol)
            await asyncio.sleep(step_delay)
        self._mpv_volume = target


async def main():
    player = LocalPlayer()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
