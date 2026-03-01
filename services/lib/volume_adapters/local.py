"""
Local ALSA volume adapter — shared base for HDMI, S/PDIF, and RCA outputs.

All three use ALSA software volume via amixer.  Subclasses only need to
specify the card name, control name, and display label.
"""

import asyncio
import logging
import os

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.local")


class LocalVolume(VolumeAdapter):
    """Volume control via ALSA software mixer.  Subclass and set card/control."""

    def __init__(self, max_volume: int, card: str, control: str, label: str):
        super().__init__(max_volume, debounce_ms=100)
        self._card = card
        self._control = control
        self._label = label
        self._powered = False

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

    async def _apply_volume(self, volume: float) -> None:
        await self._amixer("sset", self._control, f"{volume:.0f}%")
        logger.info("-> %s volume: %.0f%%", self._label, volume)

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
        logger.info("%s audio unmuted", self._label)

    async def power_off(self) -> None:
        await self._amixer("sset", self._control, "mute")
        self._powered = False
        logger.info("%s audio muted", self._label)

    async def is_on(self) -> bool:
        return self._powered
