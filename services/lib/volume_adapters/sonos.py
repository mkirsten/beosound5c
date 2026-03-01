"""
Sonos volume adapter — controls volume via SoCo library.
"""

import asyncio
import logging

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.sonos")


class SonosVolume(VolumeAdapter):
    """Volume control via SoCo library talking directly to a Sonos speaker."""

    def __init__(self, ip: str, max_volume: int):
        super().__init__(max_volume, debounce_ms=50)
        from soco import SoCo
        self._ip = ip
        self._speaker = SoCo(ip)

    async def _apply_volume(self, volume: float) -> None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: setattr(self._speaker, 'volume', int(volume)))
            logger.info("-> Sonos volume: %.0f%%", volume)
        except Exception as e:
            logger.warning("Sonos unreachable: %s", e)

    async def get_volume(self) -> float | None:
        try:
            loop = asyncio.get_running_loop()
            vol = await loop.run_in_executor(None, lambda: self._speaker.volume)
            logger.info("Sonos volume read: %d%%", vol)
            return float(vol)
        except Exception as e:
            logger.warning("Could not read Sonos volume: %s", e)
            return None

    async def is_on(self) -> bool:
        return True  # Sonos is always on
