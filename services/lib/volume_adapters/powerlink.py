"""
PowerLink volume adapter — controls B&O speakers via masterlink.py HTTP API.

masterlink.py owns the PC2 USB device and exposes mixer control on a local
HTTP port (default 8768).  This adapter is a thin HTTP client.

Chain: router.py -> PowerLinkVolume -> HTTP -> masterlink.py -> PC2 USB -> speakers

Volume is an absolute value (0-max).  masterlink.py uses 0xE3 to set initial
volume at power-on and 0xEB steps for live changes.  The device echoes back
confirmed volume via USB feedback messages.
"""

import logging

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.powerlink")


class PowerLinkVolume(VolumeAdapter):
    """Volume control via masterlink.py mixer HTTP API."""

    def __init__(self, host: str, max_volume: int, default_volume: int,
                 session: aiohttp.ClientSession, port: int = 8768):
        super().__init__(max_volume, debounce_ms=200)
        self._host = host
        self._port = port
        self._default_volume = default_volume
        self._session = session
        self._base = f"http://{host}:{port}"
        self._cached_on: bool = False

    async def _apply_volume(self, volume: float) -> None:
        volume = min(int(volume), self._max_volume)
        try:
            async with self._session.post(
                f"{self._base}/mixer/volume",
                json={"volume": volume},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                data = await resp.json()
                confirmed = data.get("volume_confirmed", volume)
                logger.info("-> PowerLink volume: %d (confirmed %d, HTTP %d)",
                            volume, confirmed, resp.status)
        except Exception as e:
            logger.warning("PowerLink mixer unreachable: %s", e)

    async def get_volume(self) -> float:
        try:
            async with self._session.get(
                f"{self._base}/mixer/status",
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                data = await resp.json()
                vol = float(data.get("volume_confirmed", data.get("volume", 0)))
                logger.info("PowerLink volume read: %d", vol)
                return vol
        except Exception as e:
            logger.warning("Could not read PowerLink volume: %s", e)
            return 0

    def is_on_cached(self) -> bool | None:
        return self._cached_on

    async def power_on(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/mixer/power",
                json={"on": True, "volume": self._default_volume},
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as resp:
                self._cached_on = True
                logger.info("PowerLink power on (vol %d): HTTP %d",
                            self._default_volume, resp.status)
        except Exception as e:
            logger.warning("Could not power on PowerLink: %s", e)

    async def power_off(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/mixer/power",
                json={"on": False},
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                self._cached_on = False
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
                on = data.get("speakers_on", False) is True
                self._cached_on = on
                return on
        except Exception as e:
            logger.warning("Could not check PowerLink state: %s", e)
            return False
