# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""
Abstract base class for BeoSound 5c volume adapters.

Every volume output must implement set_volume, get_volume, and is_on.
Power and balance methods have sensible defaults for adapters that don't
support them (power always on, balance always centred).
"""

from abc import ABC, abstractmethod


class VolumeAdapter(ABC):
    """Interface every volume output must implement."""

    @abstractmethod
    async def set_volume(self, volume: float) -> None: ...

    @abstractmethod
    async def get_volume(self) -> float: ...

    @abstractmethod
    async def is_on(self) -> bool: ...

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
