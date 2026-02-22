"""
Control4 amplifier volume adapter — controls volume via UDP commands.

Protocol (from c4mp-test):
  - UDP port 8750
  - Frame: ``0s2a{nn} {command} \r\n`` where nn is random 10–99
  - Volume: ``c4.amp.chvol {zone} {level}``
  - Source/power: ``c4.amp.out {zone} {input}``  (input 00 = off)

Config (config.json "volume" section):
  - type: "c4amp"
  - host: IP of the Control4 amplifier
  - zone: output zone ID, e.g. "01" (default "01")
  - input: source input to select on power_on, e.g. "01" (default "01")
  - max: max volume percentage (default 70)
"""

import asyncio
import logging
import random
import socket

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.c4amp")

C4_PORT = 8750


class C4AmpVolume(VolumeAdapter):
    """Volume control for a Control4 multi-zone amplifier over UDP."""

    def __init__(self, host: str, max_volume: int, zone: str = "01", input_id: str = "01"):
        self._host = host
        self._max_volume = max_volume
        self._zone = zone
        self._input_id = input_id
        self._last_volume: float = 0
        self._is_on = False
        # Debounce state
        self._pending_volume: float | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._debounce_ms = 50

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
        # UDP protocol has no query command — return last known value
        return self._last_volume

    async def set_balance(self, balance: float) -> None:
        # C4 amp has no balance control
        pass

    async def get_balance(self) -> float:
        return 0

    async def power_on(self) -> None:
        await self._send(f"c4.amp.out {self._zone} {self._input_id}")
        self._is_on = True
        logger.info("C4 amp zone %s on (input %s)", self._zone, self._input_id)

    async def power_off(self) -> None:
        await self._send(f"c4.amp.out {self._zone} 00")
        self._is_on = False
        logger.info("C4 amp zone %s off", self._zone)

    async def is_on(self) -> bool:
        return self._is_on

    # -- internal --

    async def _flush(self) -> None:
        """Send the most recent pending volume value."""
        vol = self._pending_volume
        if vol is None:
            return
        self._pending_volume = None
        self._debounce_handle = None
        await self._send(f"c4.amp.chvol {self._zone} {int(vol)}")
        self._last_volume = vol
        logger.info("-> C4 amp zone %s volume: %d%%", self._zone, int(vol))

    async def _send(self, command: str) -> str | None:
        """Send a UDP command and return the response (if any)."""
        counter = f"0s2a{random.randint(10, 99)}"
        message = f"{counter} {command} \r\n"
        loop = asyncio.get_running_loop()
        try:
            resp = await loop.run_in_executor(None, self._send_sync, message)
            return resp
        except Exception as e:
            logger.warning("C4 amp @ %s unreachable: %s", self._host, e)
            return None

    def _send_sync(self, message: str) -> str:
        """Blocking UDP send/recv — run in executor."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        try:
            sock.sendto(message.encode(), (self._host, C4_PORT))
            data, _ = sock.recvfrom(1024)
            return data.decode()
        finally:
            sock.close()
