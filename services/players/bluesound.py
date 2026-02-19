#!/usr/bin/env python3
"""
BeoSound 5c BlueSound Player (beo-player-bluesound)

Monitors a BlueSound speaker for track changes, fetches artwork, and broadcasts
updates to the UI via WebSocket (port 8766). Also reports volume changes to
the router so the volume arc stays in sync.

BluOS API reference: the BlueSound speaker exposes a local HTTP API
(typically port 11000) for status polling, transport control, and metadata.

STATUS: STUB — not yet implemented. Will raise an error if started.
"""

import asyncio
import logging
import os
import sys

import aiohttp

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.config import cfg
from lib.player_base import PlayerBase
from lib.watchdog import watchdog_loop

# Configuration
BLUESOUND_IP = cfg("player", "ip", default="")
BLUOS_PORT = 11000    # BluOS HTTP API port

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-player-bluesound')


class BluesoundPlayer(PlayerBase):
    """BlueSound player service — extends PlayerBase.

    BluOS exposes endpoints like:
        GET /Status       — current playback state, track metadata
        GET /Artwork      — album art for the current track
        GET /Volume       — current volume level
        POST /Play        — start/resume playback
        POST /Pause       — pause playback
        POST /Skip        — next track
        POST /Back        — previous track
        POST /Stop        — stop playback

    TODO: implement all of the above.
    """

    id = "bluesound"
    name = "BlueSound"
    port = 8766

    def __init__(self):
        super().__init__()
        self.ip = BLUESOUND_IP
        self.base_url = f"http://{BLUESOUND_IP}:{BLUOS_PORT}"
        self._session: aiohttp.ClientSession | None = None

    async def on_start(self):
        if not BLUESOUND_IP:
            logger.error("No BlueSound IP configured (set player.ip in config)")
            sys.exit(1)

        logger.error(
            "BlueSound player is a stub — not yet implemented. "
            "Configure player.type=sonos to use the Sonos player instead."
        )
        sys.exit(1)

    async def on_stop(self):
        if self._session:
            await self._session.close()
            self._session = None

    # ── PlayerBase abstract methods (all raise NotImplementedError) ──

    async def play(self, uri=None, url=None, track_uri=None) -> bool:
        raise NotImplementedError("BlueSound player not yet implemented")

    async def pause(self) -> bool:
        raise NotImplementedError("BlueSound player not yet implemented")

    async def resume(self) -> bool:
        raise NotImplementedError("BlueSound player not yet implemented")

    async def next_track(self) -> bool:
        raise NotImplementedError("BlueSound player not yet implemented")

    async def prev_track(self) -> bool:
        raise NotImplementedError("BlueSound player not yet implemented")

    async def stop(self) -> bool:
        raise NotImplementedError("BlueSound player not yet implemented")

    async def get_state(self) -> str:
        raise NotImplementedError("BlueSound player not yet implemented")

    async def get_capabilities(self) -> list:
        raise NotImplementedError("BlueSound player not yet implemented")


async def main():
    player = BluesoundPlayer()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
