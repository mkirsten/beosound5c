"""
Pluggable volume adapters for BeoSound 5c audio outputs.

Each adapter handles volume control, power management, and debouncing for a
specific output type.  The factory function ``create_volume_adapter`` reads
environment variables and returns the correct adapter.

Supported types:
  - ``beolab5`` / ``esphome``  – BeoLab 5 via ESPHome REST API (default)
  - ``sonos``                  – Sonos speaker via SoCo library
  - ``powerlink``              – B&O speakers via masterlink.py mixer HTTP API
  - ``hdmi``                   – HDMI1 audio output (ALSA software volume)
  - ``spdif``                  – S/PDIF HAT output (ALSA software volume)
"""

import logging
import os

import aiohttp

from .base import VolumeAdapter
from .beolab5 import BeoLab5Volume
from .hdmi import HdmiVolume
from .powerlink import PowerLinkVolume
from .sonos import SonosVolume
from .spdif import SpdifVolume

logger = logging.getLogger("beo-router.volume")

__all__ = [
    "VolumeAdapter",
    "BeoLab5Volume",
    "HdmiVolume",
    "PowerLinkVolume",
    "SonosVolume",
    "SpdifVolume",
    "create_volume_adapter",
]


def create_volume_adapter(session: aiohttp.ClientSession) -> VolumeAdapter:
    """Create the right volume adapter based on environment variables.

    Reads:
      VOLUME_TYPE   – "esphome"/"beolab5" (default), "sonos", "powerlink",
                      "hdmi", or "spdif"
      VOLUME_HOST   – target host/IP (not used by hdmi/spdif/powerlink-localhost)
      VOLUME_MAX    – max volume percentage (default 70)
      MIXER_PORT    – masterlink.py mixer HTTP port (default 8768, powerlink only)
      ALSA_CARD     – ALSA card name override (hdmi/spdif only)
      ALSA_CONTROL  – ALSA mixer control override (hdmi/spdif only)
    """
    vol_type = os.getenv("VOLUME_TYPE", "esphome").lower()
    vol_host = os.getenv("VOLUME_HOST", "beolab5-controller.local")
    vol_max = int(os.getenv("VOLUME_MAX", "70"))

    if vol_type == "powerlink":
        host = os.getenv("VOLUME_HOST", "localhost")
        port = int(os.getenv("MIXER_PORT", "8768"))
        logger.info("Volume adapter: PowerLink via masterlink.py @ %s:%d (max %d%%)",
                     host, port, vol_max)
        return PowerLinkVolume(host, vol_max, session, port)
    elif vol_type == "sonos":
        logger.info("Volume adapter: Sonos @ %s (max %d%%)", vol_host, vol_max)
        return SonosVolume(vol_host, vol_max)
    elif vol_type == "hdmi":
        logger.info("Volume adapter: HDMI1 ALSA software volume (max %d%%)", vol_max)
        return HdmiVolume(vol_max)
    elif vol_type == "spdif":
        logger.info("Volume adapter: S/PDIF ALSA software volume (max %d%%)", vol_max)
        return SpdifVolume(vol_max)
    else:
        logger.info("Volume adapter: BeoLab 5 @ %s (max %d%%)", vol_host, vol_max)
        return BeoLab5Volume(vol_host, vol_max, session)
