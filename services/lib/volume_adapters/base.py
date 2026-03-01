# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""
Abstract base class for BeoSound 5c volume adapters.

Every volume output must implement _apply_volume, get_volume, and is_on.
The base class provides debounced set_volume() — subclasses only need to
send the value to hardware in _apply_volume().

Power and balance methods have sensible defaults for adapters that don't
support them (power always on, balance always centred).
"""

import asyncio
import logging
from abc import ABC, abstractmethod

log = logging.getLogger("beo-router.volume")


class VolumeAdapter(ABC):
    """Interface every volume output must implement."""

    def __init__(self, max_volume: int = 100, debounce_ms: int = 50):
        self._max_volume = max_volume
        self._pending_volume: float | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._debounce_ms = debounce_ms

    # -- Volume (debounced) --

    async def set_volume(self, volume: float) -> None:
        """Cap and debounce, then call _apply_volume()."""
        capped = min(volume, self._max_volume)
        if volume > self._max_volume:
            log.warning("Volume %.0f%% capped to %d%%", volume, self._max_volume)
        self._pending_volume = capped
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
        loop = asyncio.get_running_loop()
        self._debounce_handle = loop.call_later(
            self._debounce_ms / 1000, lambda: asyncio.ensure_future(self._do_flush())
        )

    async def _do_flush(self):
        """Send the pending volume to hardware."""
        vol = self._pending_volume
        if vol is None:
            return
        self._pending_volume = None
        self._debounce_handle = None
        await self._apply_volume(vol)

    @abstractmethod
    async def _apply_volume(self, volume: float) -> None:
        """Actually send the volume to hardware.  Called after debounce."""
        ...

    @abstractmethod
    async def get_volume(self) -> float | None: ...

    @abstractmethod
    async def is_on(self) -> bool: ...

    def is_on_cached(self) -> bool | None:
        """Return cached power state without querying the device.
        Returns None if no cached value is available."""
        return None

    # -- Optional: override in adapters that support power control --

    async def power_on(self) -> None:
        pass  # no-op by default (always on)

    async def power_off(self) -> None:
        pass  # no-op by default (always on)

    # -- Optional: override in adapters that support balance --

    async def set_balance(self, balance: float) -> None:
        pass  # no-op by default (no balance control)

    async def get_balance(self) -> float:
        return 0  # centred by default
