#!/usr/bin/env python3
"""
BeoSound 5c Local Player (beo-player-local)

Plays URL streams locally via mpv. Sources use the standard player HTTP API
(POST /player/play, etc.) — no source code changes needed.

Sources pre-broadcast their own metadata (e.g. Plex calls self.broadcast()
before player_play()), so this player does not need to monitor or broadcast
media updates. It just plays what it's told.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.player_base import PlayerBase

IPC_SOCKET = '/tmp/beo-player-local.sock'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-player-local')


class LocalPlayer(PlayerBase):
    """Local mpv-based player service."""

    id = "local"
    name = "Local"
    port = 8766

    def __init__(self):
        super().__init__()
        self._process: subprocess.Popen | None = None
        self._watcher_task: asyncio.Task | None = None

    # ── PlayerBase abstract methods ──

    async def play(self, uri=None, url=None, track_uri=None, meta=None) -> bool:
        if uri:
            logger.warning("Local player does not support share URIs — ignoring uri=%s", uri)
            return False

        if url:
            # Kill existing mpv if running
            await self._kill_mpv()

            try:
                env = os.environ.copy()
                env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
                self._process = subprocess.Popen([
                    'mpv', '--ao=pulse', url,
                    '--no-video', '--no-terminal',
                    f'--input-ipc-server={IPC_SOCKET}',
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                self._current_playback_state = 'playing'
                self._watcher_task = asyncio.create_task(self._watch_process())
                logger.info("Playing URL: %s", url)
                return True
            except Exception as e:
                logger.error("Play failed: %s", e)
                self._current_playback_state = 'stopped'
                return False

        # No URI/URL — try resume
        return await self.resume()

    async def pause(self) -> bool:
        if self._process and self._process.poll() is None:
            ok = await self._mpv_ipc('set_property', 'pause', True)
            if ok:
                self._current_playback_state = 'paused'
                logger.info("Paused")
            return ok
        return False

    async def resume(self) -> bool:
        if self._process and self._process.poll() is None:
            ok = await self._mpv_ipc('set_property', 'pause', False)
            if ok:
                self._current_playback_state = 'playing'
                logger.info("Resumed")
            return ok
        return False

    async def next_track(self) -> bool:
        # Sources manage their own track lists
        return False

    async def prev_track(self) -> bool:
        return False

    async def stop(self) -> bool:
        await self._kill_mpv()
        self._current_playback_state = 'stopped'
        logger.info("Stopped")
        return True

    async def get_capabilities(self) -> list:
        return ["url_stream"]

    async def get_status(self) -> dict:
        base = await super().get_status()
        base["state"] = self._current_playback_state or "stopped"
        base["mpv_running"] = self._process is not None and self._process.poll() is None
        return base

    # ── PlayerBase hooks ──

    async def on_start(self):
        logger.info("Local player ready (mpv backend)")

    async def on_stop(self):
        await self._kill_mpv()

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
            while self._process and self._process.poll() is None:
                await asyncio.sleep(0.25)
            if self._current_playback_state == 'playing':
                self._current_playback_state = 'stopped'
                self._process = None
                logger.info("mpv process ended")
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


async def main():
    player = LocalPlayer()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
