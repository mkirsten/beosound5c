"""
Pluggable volume adapters for BeoSound 5c audio outputs.

Each adapter handles volume control, power management, and debouncing for a
specific output type.  The factory function ``create_volume_adapter`` reads
environment variables and returns the correct adapter.

Supported types:
  - ``esphome``  – BeoLab 5 via ESPHome REST API (default)
  - ``sonos``    – Sonos speaker via SoCo library
"""

import asyncio
import logging
import os
from abc import ABC, abstractmethod

import aiohttp

logger = logging.getLogger("beo-router.volume")


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class VolumeAdapter(ABC):
    """Interface every volume output must implement."""

    @abstractmethod
    async def set_volume(self, volume: float) -> None: ...

    @abstractmethod
    async def get_volume(self) -> float: ...

    @abstractmethod
    async def power_on(self) -> None: ...

    @abstractmethod
    async def is_on(self) -> bool: ...


# ---------------------------------------------------------------------------
# ESPHome (BeoLab 5 controller)
# ---------------------------------------------------------------------------

class ESPHomeVolume(VolumeAdapter):
    """Volume control via an ESPHome REST API (e.g. BeoLab 5 ESP32)."""

    def __init__(self, host: str, max_volume: int, session: aiohttp.ClientSession):
        self._host = host
        self._max_volume = max_volume
        self._session = session
        self._base = f"http://{host}"
        # Debounce state
        self._pending_volume: float | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._debounce_ms = 100  # coalesce rapid calls

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
                f"{self._base}/number/beolab_5_volume",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                data = await resp.json()
                vol = float(data.get("value", 0))
                logger.info("ESPHome volume read: %.0f%%", vol)
                return vol
        except Exception as e:
            logger.warning("Could not read ESPHome volume: %s", e)
            return 0

    async def power_on(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/switch/beolab_5/turn_on",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("ESPHome power on: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Could not power on ESPHome device: %s", e)

    async def is_on(self) -> bool:
        try:
            async with self._session.get(
                f"{self._base}/switch/beolab_5",
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                data = await resp.json()
                return data.get("value", False) is True
        except Exception as e:
            logger.warning("Could not check ESPHome power state: %s", e)
            return False

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
                f"{self._base}/number/beolab_5_volume/set",
                params={"value": str(vol)},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("-> ESPHome volume: %.0f%% (HTTP %d)", vol, resp.status)
        except Exception as e:
            logger.warning("ESPHome controller unreachable: %s", e)


# ---------------------------------------------------------------------------
# Sonos (direct SoCo control)
# ---------------------------------------------------------------------------

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

    async def power_on(self) -> None:
        # Sonos is always on — no-op
        pass

    async def is_on(self) -> bool:
        return True

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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_volume_adapter(session: aiohttp.ClientSession) -> VolumeAdapter:
    """Create the right volume adapter based on environment variables.

    Reads:
      VOLUME_TYPE  – "esphome" (default) or "sonos"
      VOLUME_HOST  – target host/IP
      VOLUME_MAX   – max volume percentage (default 70)
    """
    vol_type = os.getenv("VOLUME_TYPE", "esphome").lower()
    vol_host = os.getenv("VOLUME_HOST", "beolab5-controller.local")
    vol_max = int(os.getenv("VOLUME_MAX", "70"))

    if vol_type == "sonos":
        logger.info("Volume adapter: Sonos @ %s (max %d%%)", vol_host, vol_max)
        return SonosVolume(vol_host, vol_max)
    else:
        logger.info("Volume adapter: ESPHome @ %s (max %d%%)", vol_host, vol_max)
        return ESPHomeVolume(vol_host, vol_max, session)
