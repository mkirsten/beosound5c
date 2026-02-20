"""
Pluggable volume adapters for BeoSound 5c audio outputs.

Each adapter handles volume control, power management, and debouncing for a
specific output type.  The factory function ``create_volume_adapter`` reads
config.json and returns the correct adapter.

Supported types:
  - ``beolab5`` (or ``esphome`` for compat) – BeoLab 5 via controller REST API (default)
  - ``sonos``                  – Sonos speaker via SoCo library
  - ``bluesound``              – BlueSound speaker via BluOS HTTP API
  - ``powerlink``              – B&O speakers via masterlink.py mixer HTTP API
  - ``c4amp``                  – Control4 amplifier via UDP
  - ``hdmi``                   – HDMI1 audio output (ALSA software volume)
  - ``spdif``                  – S/PDIF HAT output (ALSA software volume)
  - ``rca``                    – RCA analog output (no volume control)
"""

import logging

import aiohttp

from ..config import cfg
from .base import VolumeAdapter
from .beolab5 import BeoLab5Volume
from .bluesound import BluesoundVolume
from .c4amp import C4AmpVolume
from .hdmi import HdmiVolume
from .powerlink import PowerLinkVolume
from .rca import RcaVolume
from .sonos import SonosVolume
from .spdif import SpdifVolume

logger = logging.getLogger("beo-router.volume")

__all__ = [
    "VolumeAdapter",
    "BeoLab5Volume",
    "BluesoundVolume",
    "C4AmpVolume",
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
      type        – "beolab5" (also accepts "esphome"), "sonos", "bluesound",
                    "powerlink", "c4amp", "hdmi", "spdif", or "rca".
                    If omitted, defaults to player.type for sonos/bluesound,
                    "powerlink" for local/powerlink, otherwise "beolab5".
      host        – target host/IP (not used by hdmi/spdif/rca/powerlink-localhost)
      max         – max volume percentage (default 70)
      zone        – C4 amp output zone, e.g. "01" (c4amp only, default "01")
      input       – C4 amp source input for power_on (c4amp only, default "01")
      mixer_port  – masterlink.py mixer HTTP port (default 8768, powerlink only)
    """
    vol_type = cfg("volume", "type")
    if vol_type is None:
        # Default to matching the player type for sonos/bluesound
        player_type = str(cfg("player", "type", default="")).lower()
        if player_type in ("sonos", "bluesound"):
            vol_type = player_type
        elif player_type in ("local", "powerlink"):
            vol_type = "powerlink"
        else:
            vol_type = "beolab5"
    vol_type = str(vol_type).lower()
    # Default host: use player IP for sonos/bluesound, otherwise beolab5 controller
    vol_host_default = "beolab5-controller.local"
    if vol_type in ("sonos", "bluesound") and not cfg("volume", "host"):
        vol_host_default = cfg("player", "ip", default="")
    elif vol_type == "powerlink" and not cfg("volume", "host"):
        vol_host_default = "localhost"
    vol_host = cfg("volume", "host", default=vol_host_default)
    vol_max = int(cfg("volume", "max", default=70))

    if vol_type == "powerlink":
        host = cfg("volume", "host", default="localhost")
        port = int(cfg("volume", "mixer_port", default=8768))
        logger.info("Volume adapter: PowerLink via masterlink.py @ %s:%d (max %d%%)",
                     host, port, vol_max)
        return PowerLinkVolume(host, vol_max, session, port)
    elif vol_type == "c4amp":
        zone = str(cfg("volume", "zone", default="01"))
        input_id = str(cfg("volume", "input", default="01"))
        logger.info("Volume adapter: C4 amp @ %s zone %s (max %d%%)",
                     vol_host, zone, vol_max)
        return C4AmpVolume(vol_host, vol_max, zone, input_id)
    elif vol_type == "bluesound":
        logger.info("Volume adapter: BlueSound @ %s (max %d%%)", vol_host, vol_max)
        return BluesoundVolume(vol_host, vol_max, session)
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
