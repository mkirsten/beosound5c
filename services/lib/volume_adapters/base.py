"""
Abstract base class for BeoSound 5c volume adapters.

Every volume output must implement this interface.
"""

from abc import ABC, abstractmethod


class VolumeAdapter(ABC):
    """Interface every volume output must implement."""

    @abstractmethod
    async def set_volume(self, volume: float) -> None: ...

    @abstractmethod
    async def get_volume(self) -> float: ...

    @abstractmethod
    async def power_on(self) -> None: ...

    @abstractmethod
    async def power_off(self) -> None: ...

    @abstractmethod
    async def set_balance(self, balance: float) -> None: ...

    @abstractmethod
    async def get_balance(self) -> float: ...

    @abstractmethod
    async def is_on(self) -> bool: ...
