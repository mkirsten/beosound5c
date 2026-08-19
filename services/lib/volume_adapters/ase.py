"""
B&O ASE / SoundCenter volume adapter — controls volume via the BeoZone API.

GET  /BeoZone/Zone/Sound/Volume/Speaker/Level  -> {"speaker":{"level":0-90}}
PUT  /BeoZone/Zone/Sound/Volume/Speaker/Level  <- {"speaker":{"level":0-90}}

EXPERIMENTAL / UNVERIFIED — no published spec to validate against.
"""

import logging

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.ase")

ASE_PORT = 8080
_PATH = "/BeoZone/Zone/Sound/Volume/Speaker/Level"


class AseVolume(VolumeAdapter):
    def __init__(self, ip: str, max_volume: int, session: aiohttp.ClientSession):
        super().__init__(max_volume, debounce_ms=50)
        self._session = session
        self._base = f"http://{ip}:{ASE_PORT}"

    async def _apply_volume(self, volume: float) -> None:
        try:
            async with self._session.put(
                f"{self._base}{_PATH}",
                json={"speaker": {"level": int(volume)}},
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                logger.info("-> ASE volume: %.0f%%", volume)
        except Exception as e:
            logger.warning("ASE unreachable for volume set: %s", e)

    async def get_volume(self) -> float | None:
        try:
            async with self._session.get(
                f"{self._base}{_PATH}",
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                return float((data.get("speaker") or {}).get("level", 0))
        except Exception as e:
            logger.warning("Could not read ASE volume: %s", e)
            return None

    async def is_on(self) -> bool:
        return True
