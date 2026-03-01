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

import os

from .local import LocalVolume


class RcaVolume(LocalVolume):
    """Volume control via ALSA software mixer on DAC HAT."""

    def __init__(self, max_volume: int, card: str | None = None,
                 control: str | None = None):
        super().__init__(
            max_volume,
            card=card or os.getenv("ALSA_CARD", "sndrpihifiberry"),
            control=control or os.getenv("ALSA_CONTROL", "Digital"),
            label="RCA",
        )
