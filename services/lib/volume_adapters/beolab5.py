"""
BeoLab 5 volume adapter — controls volume via BeoLab 5 controller REST API.
"""

import asyncio
import logging

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.beolab5")


class BeoLab5Volume(VolumeAdapter):
    """Volume control via the BeoLab 5 controller REST API."""

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
        self._last_volume: float = 0  # last volume sent, for safe power-on
        self._power_on_max = 40  # cap volume on power-on (%)

    # -- public API --

    async def set_volume(self, volume: float) -> None:
        capped = min(volume, self._max_volume)
        if volume > self._max_volume:
            logger.warning("Volume %.0f%% capped to %d%%", volume, self._max_volume)
        self._last_volume = capped
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
                self._last_volume = vol
                logger.info("BeoLab 5 volume read: %.0f%%", vol)
                return vol
        except Exception as e:
            logger.warning("Could not read BeoLab 5 volume: %s", e)
            return 0

    async def set_balance(self, balance: float) -> None:
        bal = max(-20, min(20, balance))
        try:
            async with self._session.post(
                f"{self._base}/number/balance/set",
                params={"value": str(bal)},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("-> BeoLab 5 balance: %.0f (HTTP %d)", bal, resp.status)
        except Exception as e:
            logger.warning("BeoLab 5 controller unreachable (balance): %s", e)

    async def get_balance(self) -> float:
        try:
            async with self._session.get(
                f"{self._base}/number/balance",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                data = await resp.json()
                return float(data.get("value", 0))
        except Exception as e:
            logger.warning("Could not read BeoLab 5 balance: %s", e)
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
            return
        # Send remembered volume, capped for safety
        safe_vol = min(self._last_volume, self._power_on_max)
        if safe_vol > 0:
            logger.info("Power-on volume: %.0f%% (capped from %.0f%%)", safe_vol, self._last_volume)
            await self.set_volume(safe_vol)

    async def power_off(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/switch/power/turn_off",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("BeoLab 5 power off: HTTP %d", resp.status)
                self._power_cache = False
                self._power_cache_time = asyncio.get_event_loop().time()
        except Exception as e:
            logger.warning("Could not power off BeoLab 5: %s", e)

    def is_on_cached(self) -> bool | None:
        return self._power_cache

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
        """Send the most recent pending volume value to the BeoLab 5 controller."""
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
                self._last_volume = vol
                logger.info("-> BeoLab 5 volume: %.0f%% (HTTP %d)", vol, resp.status)
        except Exception as e:
            logger.warning("BeoLab 5 controller unreachable: %s", e)
