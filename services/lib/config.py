"""
Shared configuration loader for BeoSound 5c services.

Loads a single JSON config file per device.  Search order:
  1. /etc/beosound5c/config.json   (deployed by deploy.sh)
  2. config.json                    (CWD — handy for local dev)
  3. ../config/default.json         (repo fallback)

Secrets (HA_TOKEN, MQTT_USER, etc.) stay in environment variables,
loaded from /etc/beosound5c/secrets.env by systemd EnvironmentFile.

Usage:
    from lib.config import cfg

    device_name  = cfg("device", default="BeoSound5c")
    player_ip    = cfg("player", "ip", default="192.168.0.190")
    volume_max   = cfg("volume", "max", default=70)
    menu         = cfg("menu")  # returns the whole dict
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_config: dict | None = None

_SEARCH_PATHS = [
    "/etc/beosound5c/config.json",
    "config.json",
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "default.json"),
]


def _validate(config: dict, path: str) -> None:
    """Warn about missing or suspicious config values."""
    if not config.get("device"):
        logger.warning("Config %s: missing 'device' name", path)
    if not config.get("menu"):
        logger.warning("Config %s: missing 'menu' section — UI will use fallback menu", path)
    ha = config.get("home_assistant") or {}
    if not ha.get("webhook_url"):
        logger.warning("Config %s: missing home_assistant.webhook_url — HA integration disabled", path)
    vol = config.get("volume") or {}
    vol_type = vol.get("type", "beolab5")
    if vol_type not in ("esphome", "beolab5", "sonos", "bluesound", "powerlink", "c4amp", "hdmi", "spdif", "rca"):
        logger.warning("Config %s: unknown volume.type '%s'", path, vol_type)
    # News source requires a Guardian API key
    menu = config.get("menu") or {}
    has_news = any(
        (v == "news") or (isinstance(v, dict) and v.get("id") == "news")
        for v in menu.values()
    )
    if has_news:
        news_cfg = config.get("news") or {}
        if not news_cfg.get("guardian_api_key"):
            logger.error("Config %s: NEWS source in menu but no news.guardian_api_key — service will refuse to start", path)


def load_config() -> dict:
    """Load config from the first JSON file found. Cached after first call."""
    global _config
    if _config is not None:
        return _config

    for path in _SEARCH_PATHS:
        try:
            with open(path) as f:
                _config = json.load(f)
                logger.info("Config loaded from %s", path)
                _validate(_config, path)
                return _config
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in %s: %s", path, e)
            continue

    logger.warning("No config.json found — using empty config")
    _config = {}
    return _config


def cfg(section: str, key: str | None = None, *, default=None):
    """Read a config value.

    cfg("device")                → config["device"]
    cfg("player", "ip")          → config["player"]["ip"]
    cfg("volume", "max", default=70)  → config["volume"]["max"] or 70
    """
    config = load_config()
    val = config.get(section)
    if key is None:
        return val if val is not None else default
    if isinstance(val, dict):
        return val.get(key, default)
    return default


def reload_config():
    """Force re-read from disk (for testing or hot-reload)."""
    global _config
    _config = None
    return load_config()
