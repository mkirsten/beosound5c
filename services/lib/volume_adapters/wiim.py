"""
WiiM / LinkPlay volume adapter — controls volume via the LinkPlay HTTP API.

Newer WiiM firmware serves HTTPS with a self-signed cert (verification is
disabled); older LinkPlay devices serve plain HTTP. We try HTTPS first and
remember whichever scheme answered.
"""

import asyncio
import logging

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.wiim")


class WiimVolume(VolumeAdapter):
    """Volume control via the LinkPlay HTTP API (getPlayerStatus / setPlayerCmd:vol)."""

    def __init__(self, ip: str, max_volume: int, session: aiohttp.ClientSession):
        super().__init__(max_volume, debounce_ms=50)
        self._ip = ip
        self._session = session
        self._scheme = None

    async def _lp_get(self, command: str, timeout: float = 5):
        schemes = [self._scheme] if self._scheme else ["https", "http"]
        for scheme in schemes:
            try:
                async with self._session.get(
                    f"{scheme}://{self._ip}/httpapi.asp?command={command}",
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
                    self._scheme = scheme
                    return text
            except Exception:
                continue
        return None

    async def _apply_volume(self, volume: float) -> None:
        resp = await self._lp_get(f"setPlayerCmd:vol:{int(volume)}")
        if resp is None:
            logger.warning("WiiM unreachable for volume set")
        else:
            logger.info("-> WiiM volume: %.0f%%", volume)

    async def get_volume(self) -> float | None:
        import json
        text = await self._lp_get("getPlayerStatus")
        if text is None:
            return None
        try:
            vol = int(json.loads(text).get("vol", 0))
            logger.info("WiiM volume read: %d%%", vol)
            return float(vol)
        except (ValueError, TypeError):
            return None

    async def is_on(self) -> bool:
        return True  # LinkPlay devices are always on
