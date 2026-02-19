"""
BeoLab 5 volume adapter — controls volume via ESPHome REST API on an ESP32.
"""

import asyncio
import logging

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.beolab5")


class BeoLab5Volume(VolumeAdapter):
    """Volume control via an ESPHome REST API (e.g. BeoLab 5 ESP32)."""

    def __init__(self, host: str, max_volume: int, session: aiohttp.ClientSession):
        self._host = host
        self._max_volume = max_volume
        self._session = session
        self._base = f"http://{host}"
        # Debounce state
        self._pending_volume: float | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._debounce_ms = 50  # coalesce rapid calls
        # Cached power state to avoid HTTP round-trip on every volume change
        self._power_cache: bool | None = None
        self._power_cache_time: float = 0
        self._power_cache_ttl = 30.0  # seconds

    # -- public API --

    async def set_volume(self, volume: float) -> None:
        capped = min(volume, self._max_volume)
        if volume > self._max_volume:
            logger.warning("Volume %.0f%% capped to %d%%", volume, self._max_volume)
        self._pending_volume = capped
        # Cancel any pending send and schedule a new one
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
        loop = asyncio.get_running_loop()
        self._debounce_handle = loop.call_later(
            self._debounce_ms / 1000, lambda: asyncio.ensure_future(self._flush())
        )

    async def get_volume(self) -> float:
        try:
            async with self._session.get(
                f"{self._base}/number/volume",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                data = await resp.json()
                vol = float(data.get("value", 0))
                logger.info("BeoLab 5 volume read: %.0f%%", vol)
                return vol
        except Exception as e:
            logger.warning("Could not read BeoLab 5 volume: %s", e)
            return 0

    async def power_on(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/switch/power/turn_on",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("BeoLab 5 power on: HTTP %d", resp.status)
                self._power_cache = True
                self._power_cache_time = asyncio.get_event_loop().time()
        except Exception as e:
            logger.warning("Could not power on BeoLab 5: %s", e)

    async def is_on(self) -> bool:
        now = asyncio.get_event_loop().time()
        if self._power_cache is not None and (now - self._power_cache_time) < self._power_cache_ttl:
            return self._power_cache
        try:
            async with self._session.get(
                f"{self._base}/switch/power",
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                data = await resp.json()
                self._power_cache = data.get("value", False) is True
                self._power_cache_time = now
                return self._power_cache
        except Exception as e:
            logger.warning("Could not check BeoLab 5 power state: %s", e)
            return self._power_cache if self._power_cache is not None else False

    # -- internal --

    async def _flush(self):
        """Send the most recent pending volume value to ESPHome."""
        vol = self._pending_volume
        if vol is None:
            return
        self._pending_volume = None
        self._debounce_handle = None
        try:
            async with self._session.post(
                f"{self._base}/number/volume/set",
                params={"value": str(vol)},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("-> BeoLab 5 volume: %.0f%% (HTTP %d)", vol, resp.status)
        except Exception as e:
            logger.warning("BeoLab 5 controller unreachable: %s", e)
