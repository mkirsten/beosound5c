"""
B&O Mozart volume adapter — controls volume via the Mozart local REST API.

GET  /api/v1/sound/volume/level  -> {"level": 0-100}
PUT  /api/v1/sound/volume/level  <- {"level": 0-100}

EXPERIMENTAL — not yet validated on hardware.
"""

import logging

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.mozart")

MOZART_PORT = 8080


class MozartVolume(VolumeAdapter):
    def __init__(self, ip: str, max_volume: int, session: aiohttp.ClientSession):
        super().__init__(max_volume, debounce_ms=50)
        self._session = session
        self._base = f"http://{ip}:{MOZART_PORT}/api/v1"

    async def _apply_volume(self, volume: float) -> None:
        try:
            async with self._session.put(
                f"{self._base}/sound/volume/level",
                json={"level": int(volume)},
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                logger.info("-> Mozart volume: %.0f%%", volume)
        except Exception as e:
            logger.warning("Mozart unreachable for volume set: %s", e)

    async def get_volume(self) -> float | None:
        try:
            async with self._session.get(
                f"{self._base}/sound/volume/level",
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                return float(data.get("level", 0))
        except Exception as e:
            logger.warning("Could not read Mozart volume: %s", e)
            return None

    async def is_on(self) -> bool:
        return True
