"""
Pluggable volume adapters for BeoSound 5c audio outputs.

Each adapter handles volume control, power management, and debouncing for a
specific output type.  The factory function ``create_volume_adapter`` reads
config.json and returns the correct adapter.

Supported types:
  - ``beolab5`` / ``esphome``  – BeoLab 5 via ESPHome REST API (default)
  - ``sonos``                  – Sonos speaker via SoCo library
  - ``powerlink``              – B&O speakers via masterlink.py mixer HTTP API
  - ``hdmi``                   – HDMI1 audio output (ALSA software volume)
  - ``spdif``                  – S/PDIF HAT output (ALSA software volume)
  - ``rca``                    – RCA analog output (no volume control)
"""

import logging

import aiohttp

from ..config import cfg
from .base import VolumeAdapter
from .beolab5 import BeoLab5Volume
from .hdmi import HdmiVolume
from .powerlink import PowerLinkVolume
from .rca import RcaVolume
from .sonos import SonosVolume
from .spdif import SpdifVolume

logger = logging.getLogger("beo-router.volume")

__all__ = [
    "VolumeAdapter",
    "BeoLab5Volume",
    "HdmiVolume",
    "PowerLinkVolume",
    "RcaVolume",
    "SonosVolume",
    "SpdifVolume",
    "create_volume_adapter",
]


def create_volume_adapter(session: aiohttp.ClientSession) -> VolumeAdapter:
    """Create the right volume adapter based on config.json.

    Reads from config.json "volume" section:
      type        – "esphome"/"beolab5" (default), "sonos", "powerlink",
                    "hdmi", "spdif", or "rca"
      host        – target host/IP (not used by hdmi/spdif/rca/powerlink-localhost)
      max         – max volume percentage (default 70)
      mixer_port  – masterlink.py mixer HTTP port (default 8768, powerlink only)
    """
    vol_type = str(cfg("volume", "type", default="esphome")).lower()
    vol_host = cfg("volume", "host", default="beolab5-controller.local")
    vol_max = int(cfg("volume", "max", default=70))

    if vol_type == "powerlink":
        host = cfg("volume", "host", default="localhost")
        port = int(cfg("volume", "mixer_port", default=8768))
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
    elif vol_type == "rca":
        logger.info("Volume adapter: RCA analog output (no volume control, max %d%%)", vol_max)
        return RcaVolume(vol_max)
    else:
        logger.info("Volume adapter: BeoLab 5 @ %s (max %d%%)", vol_host, vol_max)
        return BeoLab5Volume(vol_host, vol_max, session)
