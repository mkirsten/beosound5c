"""
HEOS volume adapter — controls volume via the HEOS CLI protocol (TCP 1255).

Uses a minimal request/response client rather than pyheos: this adapter runs
inside the router process, and a one-shot command needs no event
subscription or reconnect machinery. The connection is opened lazily and
re-opened on the next command after any error. Change events are never
registered, so every line read is a direct command response.
"""

import asyncio
import json
import logging
import urllib.parse

from .base import VolumeAdapter

logger = logging.getLogger("beo-router.volume.heos")

HEOS_PORT = 1255
COMMAND_TIMEOUT = 5


class HeosVolume(VolumeAdapter):
    """Volume control via the HEOS CLI protocol (port 1255)."""

    def __init__(self, ip: str, max_volume: int):
        super().__init__(max_volume, debounce_ms=80)
        self._ip = ip
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pid: int | None = None
        self._io_lock = asyncio.Lock()

    # -- CLI client --

    async def _command(self, cmd: str) -> dict | None:
        """Send one CLI command and return the parsed response, or None."""
        async with self._io_lock:
            try:
                return await asyncio.wait_for(
                    self._command_locked(cmd), timeout=COMMAND_TIMEOUT)
            except Exception as e:
                logger.warning("HEOS command failed (%s): %s", cmd, e)
                self._close()
                return None

    async def _command_locked(self, cmd: str) -> dict:
        if self._writer is None:
            self._reader, self._writer = await asyncio.open_connection(
                self._ip, HEOS_PORT)
        self._writer.write((cmd + "\r\n").encode())
        await self._writer.drain()
        while True:
            line = await self._reader.readline()
            if not line:
                raise ConnectionError("HEOS connection closed")
            resp = json.loads(line)
            heos = resp.get("heos", {})
            # Long-running commands emit an interim payload first
            if "command under process" in str(heos.get("message", "")):
                continue
            if heos.get("result") != "success":
                raise RuntimeError(heos.get("message") or "command failed")
            return resp

    def _close(self):
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        # Re-resolve on next command — pids can change across power-cycles
        self._pid = None

    async def _get_pid(self) -> int | None:
        """Resolve our player's pid (cached until a connection error)."""
        if self._pid is not None:
            return self._pid
        resp = await self._command("heos://player/get_players")
        if resp is None:
            return None
        payload = resp.get("payload", [])
        for p in payload:
            if p.get("ip") == self._ip:
                self._pid = p.get("pid")
                break
        else:
            if len(payload) == 1:
                self._pid = payload[0].get("pid")
            else:
                logger.warning(
                    "No HEOS player matches ip %s (players: %s)", self._ip,
                    ", ".join(f"{p.get('name')}@{p.get('ip')}" for p in payload)
                    or "none")
        return self._pid

    # -- VolumeAdapter interface --

    async def _apply_volume(self, volume: float) -> None:
        pid = await self._get_pid()
        if pid is None:
            logger.warning("HEOS unreachable — volume not set")
            return
        resp = await self._command(
            f"heos://player/set_volume?pid={pid}&level={int(volume)}")
        if resp is not None:
            logger.info("-> HEOS volume: %.0f%%", volume)

    async def get_volume(self) -> float | None:
        pid = await self._get_pid()
        if pid is None:
            return None
        resp = await self._command(f"heos://player/get_volume?pid={pid}")
        if resp is None:
            return None
        try:
            message = resp.get("heos", {}).get("message", "")
            level = urllib.parse.parse_qs(message)["level"][0]
            vol = int(level)
            logger.info("HEOS volume read: %d%%", vol)
            return float(vol)
        except (KeyError, ValueError, IndexError) as e:
            logger.warning("Could not parse HEOS volume response: %s", e)
            return None

    async def is_on(self) -> bool:
        return True  # HEOS is network standby — always reachable
