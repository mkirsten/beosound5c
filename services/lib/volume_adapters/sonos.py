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
        # Control ONLY the paired speaker's own volume, even when it is grouped
        # with others. If the speaker is part of a group you're listening to,
        # this changes just this speaker's contribution — that's intentional.
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

    async def power_off(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._speaker.pause)
            logger.info("Sonos paused on power off")
        except Exception as e:
            logger.warning("Sonos pause failed: %s", e)

    async def is_on(self) -> bool:
        return True  # Sonos is always on
