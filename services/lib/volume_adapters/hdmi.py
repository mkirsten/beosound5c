"""
HDMI volume adapter — software volume on RPi HDMI1 audio output.

The RPi 5 has two micro-HDMI ports. HDMI0 drives the BS5 display (video only).
HDMI1 can be used as a digital audio output to an external DAC or receiver.

There is no hardware volume control on the HDMI output — this adapter uses
ALSA software volume via amixer.  The receiving device does the D/A conversion.

Requires ALSA config:  dtoverlay=vc4-kms-v3d in /boot/firmware/config.txt
                       (default on RPi OS Bookworm)

ALSA card name on RPi 5: "vc4hdmi1"
"""

import os

from .local import LocalVolume


class HdmiVolume(LocalVolume):
    """Volume control via ALSA software mixer on HDMI1."""

    def __init__(self, max_volume: int, card: str | None = None,
                 control: str | None = None):
        super().__init__(
            max_volume,
            card=card or os.getenv("ALSA_CARD", "vc4hdmi1"),
            control=control or os.getenv("ALSA_CONTROL", "Playback"),
            label="HDMI1",
        )
