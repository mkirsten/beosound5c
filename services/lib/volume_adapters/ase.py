"""
B&O ASE / SoundCenter volume adapter — volume and standby via the
BeoNetRemote (BNR) API on port 8080.

  GET /BeoZone/Zone/Sound/Volume                 -> {"volume": {"speaker": {"level", "muted", "range": {"minimum", "maximum"}}}}
  GET /BeoZone/Zone/Sound/Volume/Speaker/Level   -> {"level": N}
  PUT /BeoZone/Zone/Sound/Volume/Speaker/Level   <- {"level": N}      (what the B&O app sends)
  GET /BeoDevice/powerManagement/standby         -> {"standby": {"powerState": "on"|"standby"|"allStandby"}}
  PUT /BeoDevice/powerManagement/standby         <- {"standby": {"powerState": "standby"}}

The product's level runs in its own range — 0–90 on every ASE product seen
so far — so the router's 0–100 percent is scaled through the range maximum
read once from /Sound/Volume.

Sources: B&O app v7.6.2 decompiled and watched live (private/apk,
bs9000-gateway's bo-emulator/), plus a real BeoLink Converter NL/ML
(private/blc-nlml-bs9000-re.md). Not yet run against an ASE speaker;
note a BLC answers 501 to every volume call because volume is rendered
by the MasterLink product behind it.
"""

import asyncio
import logging

import aiohttp

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.ase")

ASE_PORT = 8080
_LEVEL_PATH = "/BeoZone/Zone/Sound/Volume/Speaker/Level"
_VOLUME_PATH = "/BeoZone/Zone/Sound/Volume"
_STANDBY_PATH = "/BeoDevice/powerManagement/standby"
DEFAULT_RANGE_MAX = 90
_POWER_CACHE_TTL = 30.0


# ── Pure helpers (unit-tested) ──

def level_from_body(data) -> int | None:
    """``{"level": N}``; also tolerate the nested ``{"speaker": {"level": N}}``."""
    if not isinstance(data, dict):
        return None
    lvl = data.get("level")
    if lvl is None:
        lvl = (data.get("speaker") or {}).get("level")
    try:
        return int(lvl) if lvl is not None else None
    except (TypeError, ValueError):
        return None


def range_max_from_volume(data, default: int = DEFAULT_RANGE_MAX) -> int:
    """range.maximum out of GET /Sound/Volume, else the default."""
    try:
        speaker = ((data or {}).get("volume") or {}).get("speaker") or (data or {}).get("speaker") or {}
        value = int((speaker.get("range") or {}).get("maximum"))
        return value if value > 0 else default
    except (AttributeError, TypeError, ValueError):
        return default


def percent_to_level(percent: float, range_max: int) -> int:
    return max(0, min(range_max, int(round(percent * range_max / 100.0))))


def level_to_percent(level: int, range_max: int) -> float:
    return max(0.0, min(100.0, level * 100.0 / (range_max or DEFAULT_RANGE_MAX)))


def power_state_from(data) -> str:
    """powerState out of GET /BeoDevice/powerManagement/standby."""
    if not isinstance(data, dict):
        return ""
    standby = data.get("standby") if isinstance(data.get("standby"), dict) else data
    return str(standby.get("powerState", "")).strip()


class AseVolume(VolumeAdapter):
    def __init__(self, ip: str, max_volume: int, session: aiohttp.ClientSession):
        super().__init__(max_volume, debounce_ms=50)
        self._session = session
        self._base = f"http://{ip}:{ASE_PORT}"
        self._range_max: int | None = None
        self._power_cache: bool | None = None
        self._power_cache_time: float = 0

    async def _device_range_max(self) -> int:
        if self._range_max:
            return self._range_max
        try:
            async with self._session.get(
                f"{self._base}{_VOLUME_PATH}",
                timeout=aiohttp.ClientTimeout(total=3)) as resp:
                resp.raise_for_status()
                self._range_max = range_max_from_volume(await resp.json(content_type=None))
                logger.info("ASE volume range maximum: %d", self._range_max)
        except Exception as e:
            logger.debug("ASE volume range unavailable (%s) — assuming 0-%d",
                         e or type(e).__name__, DEFAULT_RANGE_MAX)
            return DEFAULT_RANGE_MAX
        return self._range_max

    async def _apply_volume(self, volume: float) -> None:
        range_max = await self._device_range_max()
        level = percent_to_level(volume, range_max)
        try:
            async with self._session.put(
                f"{self._base}{_LEVEL_PATH}",
                json={"level": level},
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                logger.info("-> ASE volume: %.0f%% (level %d/%d)", volume, level, range_max)
        except Exception as e:
            logger.warning("ASE unreachable for volume set: %s", e or type(e).__name__)

    async def get_volume(self) -> float | None:
        range_max = await self._device_range_max()
        try:
            async with self._session.get(
                f"{self._base}{_LEVEL_PATH}",
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                level = level_from_body(await resp.json(content_type=None))
                return None if level is None else level_to_percent(level, range_max)
        except Exception as e:
            logger.warning("Could not read ASE volume: %s", e or type(e).__name__)
            return None

    # -- Power --

    def is_on_cached(self) -> bool | None:
        return self._power_cache

    async def is_on(self) -> bool:
        now = asyncio.get_running_loop().time()
        if self._power_cache is not None and (now - self._power_cache_time) < _POWER_CACHE_TTL:
            return self._power_cache
        try:
            async with self._session.get(
                f"{self._base}{_STANDBY_PATH}",
                timeout=aiohttp.ClientTimeout(total=2)) as resp:
                resp.raise_for_status()
                self._power_cache = power_state_from(await resp.json(content_type=None)) == "on"
                self._power_cache_time = now
                return self._power_cache
        except Exception as e:
            logger.warning("Could not read ASE power state: %s", e or type(e).__name__)
            return self._power_cache if self._power_cache is not None else True

    async def _set_power_state(self, state: str) -> None:
        try:
            async with self._session.put(
                f"{self._base}{_STANDBY_PATH}",
                json={"standby": {"powerState": state}},
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
                resp.raise_for_status()
                logger.info("ASE power %s: HTTP %d", state, resp.status)
                self._power_cache = state == "on"
                self._power_cache_time = asyncio.get_running_loop().time()
        except Exception as e:
            logger.warning("Could not set ASE power %s: %s", state, e or type(e).__name__)

    async def power_on(self) -> None:
        # "on" is what ha-beoplay sends; on a BeoLink Converter only
        # standby/allStandby are editable and a source activation wakes it.
        await self._set_power_state("on")

    async def power_off(self) -> None:
        await self._set_power_state("standby")
