"""
RCA analog volume adapter — software volume on RPi DAC HAT with RCA output.

For DAC HATs with RCA analog output (e.g. HiFiBerry DAC+, IQaudIO DAC).
This adapter uses ALSA software volume via amixer, same approach as the
HDMI and S/PDIF adapters.

Setup:
  1. Add the appropriate dtoverlay to /boot/firmware/config.txt
     (e.g. dtoverlay=hifiberry-dacplus)
  2. Reboot, verify with: aplay -l  (should show the card)
  3. Set volume.type to "rca" in config.json

ALSA card name depends on the HAT — override with ALSA_CARD env var
if the default doesn't match.
"""

import asyncio
import logging
import os

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.rca")

DEFAULT_CARD = "sndrpihifiberry"
DEFAULT_CONTROL = "Digital"


class RcaVolume(VolumeAdapter):
    """Volume control via ALSA software mixer on DAC HAT."""

    def __init__(self, max_volume: int, card: str | None = None,
                 control: str | None = None):
        self._max_volume = max_volume
        self._card = card or os.getenv("ALSA_CARD", DEFAULT_CARD)
        self._control = control or os.getenv("ALSA_CONTROL", DEFAULT_CONTROL)
        self._powered = False
        # Debounce state
        self._pending_volume: float | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._debounce_ms = 100

    async def _amixer(self, *args) -> str:
        """Run an amixer command and return stdout."""
        cmd = ["amixer", "-c", self._card] + list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("amixer failed (rc=%d): %s", proc.returncode,
                               stderr.decode().strip())
            return stdout.decode()
        except FileNotFoundError:
            logger.error("amixer not found — install alsa-utils")
            return ""

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
        output = await self._amixer("sget", self._control)
        for line in output.splitlines():
            if "%" in line:
                start = line.index("[") + 1
                end = line.index("%")
                return float(line[start:end])
        return 0

    async def power_on(self) -> None:
        await self._amixer("sset", self._control, "unmute")
        self._powered = True
        logger.info("RCA DAC audio unmuted")

    async def power_off(self) -> None:
        await self._amixer("sset", self._control, "mute")
        self._powered = False
        logger.info("RCA DAC audio muted")

    async def is_on(self) -> bool:
        return self._powered

    # -- internal --

    async def _flush(self):
        vol = self._pending_volume
        if vol is None:
            return
        self._pending_volume = None
        self._debounce_handle = None
        await self._amixer("sset", self._control, f"{vol:.0f}%")
        logger.info("-> RCA volume: %.0f%%", vol)
