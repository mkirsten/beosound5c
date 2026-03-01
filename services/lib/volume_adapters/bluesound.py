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
        super().__init__(max_volume, debounce_ms=50)
        self._ip = ip
        self._session = session
        self._base_url = f"http://{ip}:{BLUOS_PORT}"

    async def _apply_volume(self, volume: float) -> None:
        try:
            async with self._session.get(
                f"{self._base_url}/Volume?level={int(volume)}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                resp.raise_for_status()
                logger.info("-> BlueSound volume: %.0f%%", volume)
        except Exception as e:
            logger.warning("BlueSound unreachable: %s", e)

    async def get_volume(self) -> float | None:
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
            return None

    async def is_on(self) -> bool:
        return True  # BlueSound is always on
