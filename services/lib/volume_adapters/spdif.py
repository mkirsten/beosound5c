"""
S/PDIF volume adapter — software volume on RPi S/PDIF HAT (e.g. InnoMaker Digi One).

The Digi One HAT outputs bit-perfect S/PDIF via coax RCA and optical TOSLINK.
The WM8804 transceiver chip has no volume control — this adapter uses ALSA
software volume via amixer.  The receiving device does the D/A conversion.

Setup:
  1. Add to /boot/firmware/config.txt:
       dtoverlay=hifiberry-digi
  2. Reboot, verify with: aplay -l  (should show the card)
  3. Set VOLUME_TYPE=spdif in /etc/beosound5c/config.env

ALSA card name with hifiberry-digi overlay: "sndrpihifiberry"
"""

import os

from .local import LocalVolume


class SpdifVolume(LocalVolume):
    """Volume control via ALSA software mixer on S/PDIF HAT."""

    def __init__(self, max_volume: int, card: str | None = None,
                 control: str | None = None):
        super().__init__(
            max_volume,
            card=card or os.getenv("ALSA_CARD", "sndrpihifiberry"),
            control=control or os.getenv("ALSA_CONTROL", "Playback"),
            label="S/PDIF",
        )
