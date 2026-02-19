"""
Digital output volume adapter — future support for generic digital volume control.
"""

from .base import VolumeAdapter


class DigitalOutVolume(VolumeAdapter):
    """Volume control via digital output (not yet implemented)."""

    async def set_volume(self, volume: float) -> None:
        raise NotImplementedError("Digital output volume adapter not yet implemented")

    async def get_volume(self) -> float:
        raise NotImplementedError("Digital output volume adapter not yet implemented")

    async def is_on(self) -> bool:
        raise NotImplementedError("Digital output volume adapter not yet implemented")
