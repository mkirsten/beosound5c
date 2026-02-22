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
        from soco import SoCo
        self._ip = ip
        self._max_volume = max_volume
        self._speaker = SoCo(ip)
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
            loop = asyncio.get_running_loop()
            vol = await loop.run_in_executor(None, lambda: self._speaker.volume)
            logger.info("Sonos volume read: %d%%", vol)
            return float(vol)
        except Exception as e:
            logger.warning("Could not read Sonos volume: %s", e)
            return 0

    async def is_on(self) -> bool:
        return True  # Sonos is always on

    async def _flush(self):
        """Send the most recent pending volume to Sonos."""
        vol = self._pending_volume
        if vol is None:
            return
        self._pending_volume = None
        self._debounce_handle = None
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: setattr(self._speaker, 'volume', int(vol)))
            logger.info("-> Sonos volume: %.0f%%", vol)
        except Exception as e:
            logger.warning("Sonos unreachable: %s", e)
