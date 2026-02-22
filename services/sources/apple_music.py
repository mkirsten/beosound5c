#!/usr/bin/env python3
"""
BeoSound 5c Apple Music Source (beo-apple-music)

Provides Apple Music playback and library browsing.

Playback paths (determined at startup based on player capabilities):
  - Remote player supports Apple Music → player="remote", source tells player
    to play via POST /player/play {uri}. Player handles native queue.
  - Remote player does NOT support Apple Music → player="local", source plays
    locally and streams via AirPlay to the configured output.

Authentication uses Apple's MusicKit framework which requires a developer
token (JWT signed with an Apple Music key) and a user token obtained via
OAuth-like authorization.

Port: 8774

STATUS: STUB — not yet implemented. Will raise an error if started.
"""

import asyncio
import json
import logging
import os
import signal
import sys

from aiohttp import web, ClientSession

# Shared library
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.config import cfg
from lib.source_base import SourceBase

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-apple-music')


class AppleMusicService(SourceBase):
    """Apple Music source service.

    Follows the same SourceBase contract as Spotify, CD, USB:
    - Registers with router (handles, player type, command_url)
    - Receives routed button events via /command
    - Broadcasts metadata via apple_music_update events
    - UI preset in web/sources/apple_music/view.js
    """

    id = "apple_music"
    name = "Apple Music"
    port = 8774
    action_map = {
        "play": "toggle",
        "pause": "toggle",
        "go": "toggle",
        "next": "next",
        "prev": "prev",
        "right": "next",
        "left": "prev",
        "up": "next",
        "down": "prev",
        "stop": "stop",
    }

    def __init__(self):
        super().__init__()
        self.state = "stopped"  # stopped | playing | paused
        self.playlists = []
        self.now_playing = None

    async def on_start(self):
        log.error(
            "Apple Music source is a stub — not yet implemented. "
            "Remove 'apple_music' from config.json menu to hide it."
        )

        # Register as available so it appears in the menu (shows error on use)
        await self.register("available")

    async def on_stop(self):
        await self.register("gone")

    async def handle_command(self, cmd, data):
        """Handle playback commands — all stubbed."""
        log.warning("Apple Music not implemented — command ignored: %s", cmd)
        return {"status": "error", "message": "Apple Music source not yet implemented"}

    async def handle_status(self):
        return {
            "source": self.id,
            "name": self.name,
            "state": self.state,
            "implemented": False,
        }

    async def handle_resync(self):
        await self.register("available")
        return {"status": "ok", "resynced": True}


async def main():
    service = AppleMusicService()
    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
