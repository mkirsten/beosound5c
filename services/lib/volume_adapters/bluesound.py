"""
BlueSound volume adapter — controls volume via BluOS HTTP API.
"""

import asyncio
import logging
from xml.etree import ElementTree

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.bluesound")

BLUOS_PORT = 11000


class BluesoundVolume(VolumeAdapter):
    """Volume control via BluOS HTTP API (port 11000)."""

    def __init__(self, ip: str, max_volume: int, session: aiohttp.ClientSession):
        self._ip = ip
        self._max_volume = max_volume
        self._session = session
        self._base_url = f"http://{ip}:{BLUOS_PORT}"
        # Debounce state
        self._pending_volume: float | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._debounce_ms = 50

    async def set_volume(self, volume: float) -> None:
        capped = min(volume, self._max_volume)
        if volume > self._max_volume:
            logger.warning("Volume %.0f%% capped to %d%%", volume, self._max_volume)
        self._pending_volume = capped
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
        loop = asyncio.get_running_loop()
        self._debounce_handle = loop.call_later(
            self._debounce_ms / 1000, lambda: asyncio.ensure_future(self._flush())
        )

    async def get_volume(self) -> float:
        try:
            async with self._session.get(
                f"{self._base_url}/Volume",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
                root = ElementTree.fromstring(text)
                vol_text = root.text if root.text else root.get("volume", "0")
                vol = int(vol_text)
                logger.info("BlueSound volume read: %d%%", vol)
                return float(vol)
        except Exception as e:
            logger.warning("Could not read BlueSound volume: %s", e)
            return 0

    async def is_on(self) -> bool:
        return True  # BlueSound is always on

    async def _flush(self):
        """Send the most recent pending volume to BluOS."""
        vol = self._pending_volume
        if vol is None:
            return
        self._pending_volume = None
        self._debounce_handle = None
        try:
            async with self._session.get(
                f"{self._base_url}/Volume?level={int(vol)}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                resp.raise_for_status()
                logger.info("-> BlueSound volume: %.0f%%", vol)
        except Exception as e:
            logger.warning("BlueSound unreachable: %s", e)
