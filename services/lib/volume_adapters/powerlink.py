"""
PowerLink volume adapter — controls B&O speakers via masterlink.py HTTP API.

masterlink.py owns the PC2 USB device and exposes mixer control on a local
HTTP port (default 8768).  This adapter is a thin HTTP client, following the
same pattern as BeoLab5Volume → BeoLab 5 controller REST API.

Chain: router.py → PowerLinkVolume → HTTP → masterlink.py → PC2 USB → speakers
"""

import asyncio
import logging

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.powerlink")


class PowerLinkVolume(VolumeAdapter):
    """Volume control via masterlink.py mixer HTTP API."""

    def __init__(self, host: str, max_volume: int, session: aiohttp.ClientSession,
                 port: int = 8768):
        self._host = host
        self._port = port
        self._max_volume = max_volume
        self._session = session
        self._base = f"http://{host}:{port}"
        # Debounce state
        self._pending_volume: float | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._debounce_ms = 100

    # -- public API --

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
                f"{self._base}/mixer/status",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                data = await resp.json()
                vol = float(data.get("volume_pct", 0))
                logger.info("PowerLink volume read: %.0f%%", vol)
                return vol
        except Exception as e:
            logger.warning("Could not read PowerLink volume: %s", e)
            return 0

    async def power_on(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/mixer/power",
                json={"on": True},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("PowerLink power on: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Could not power on PowerLink: %s", e)

    async def power_off(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/mixer/power",
                json={"on": False},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("PowerLink power off: HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Could not power off PowerLink: %s", e)

    async def is_on(self) -> bool:
        try:
            async with self._session.get(
                f"{self._base}/mixer/status",
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                data = await resp.json()
                return data.get("speakers_on", False) is True
        except Exception as e:
            logger.warning("Could not check PowerLink state: %s", e)
            return False

    # -- internal --

    async def _flush(self):
        """Send the most recent pending volume value to masterlink.py."""
        vol = self._pending_volume
        if vol is None:
            return
        self._pending_volume = None
        self._debounce_handle = None
        try:
            async with self._session.post(
                f"{self._base}/mixer/volume",
                json={"volume": vol},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                logger.info("-> PowerLink volume: %.0f%% (HTTP %d)", vol, resp.status)
        except Exception as e:
            logger.warning("PowerLink mixer unreachable: %s", e)
