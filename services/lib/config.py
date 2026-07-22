from __future__ import annotations

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
    player_ip    = cfg("player", "ip", default="192.168.1.100")
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


class ConfigError(RuntimeError):
    """Raised when the loaded config has errors that prevent safe startup.

    Fatal cases (under :func:`load_config`):
      * No config file found anywhere in ``_SEARCH_PATHS``.
      * Invalid JSON in the only matching file.
      * Validation errors returned by :func:`_validate` (currently:
        duplicate source button mappings, which silently corrupt the
        IR remote → source routing).

    Non-fatal drift (missing device name, missing HA webhook, unknown
    volume type, news without API key) is still logged as WARNING /
    ERROR so operators can see it in the journal, but the service
    continues with sensible fallbacks.  Making those fatal would risk
    bricking live devices on config drift after a deploy.
    """


_VALID_VOLUME_TYPES = {
    "beolab5", "sonos", "bluesound", "heos", "powerlink",
    "c4amp", "hdmi", "spdif", "rca",
}


def _validate(config: dict, path: str) -> list[str]:
    """Validate ``config``.  Warnings are logged in place; fatal errors
    are returned as a list of human-readable strings so the caller can
    raise a :class:`ConfigError` with all of them at once.
    """
    errors: list[str] = []

    # ── Warnings (non-fatal drift) ──
    if not config.get("device"):
        logger.warning("Config %s: missing 'device' name", path)
    if not config.get("menu"):
        logger.warning(
            "Config %s: missing 'menu' section — UI will use fallback menu",
            path,
        )
    ha = config.get("home_assistant") or {}
    if not ha.get("webhook_url"):
        logger.warning(
            "Config %s: missing home_assistant.webhook_url — HA integration disabled",
            path,
        )
    vol = config.get("volume") or {}
    vol_type = vol.get("type", "beolab5")
    if vol_type not in _VALID_VOLUME_TYPES:
        logger.warning(
            "Config %s: unknown volume.type '%s'", path, vol_type,
        )

    # ── Fatal: duplicate source button mappings ──
    # A single IR button mapped to two sources silently corrupts routing
    # ("last one wins").  This has bitten us — the default.json shipped
    # with spotify.source = "radio" as a copy-paste typo, producing
    # "Radio" as the only reachable mapping.  Fail loud instead.
    menu = config.get("menu") or {}
    _STATIC = {"playing", "system", "scenes", "showing", "join"}
    source_buttons: dict[str, str] = {}
    for title, value in menu.items():
        sid = (
            value if isinstance(value, str)
            else (value.get("id", title.lower()) if isinstance(value, dict) else title.lower())
        )
        if sid in _STATIC:
            continue
        source_cfg = config.get(sid) or {}
        source_btn = (
            source_cfg.get("source")
            or (value.get("source") if isinstance(value, dict) else None)
        )
        if source_btn:
            if source_btn in source_buttons:
                errors.append(
                    f"source button '{source_btn}' mapped by both "
                    f"'{source_buttons[source_btn]}' and '{sid}'"
                )
            source_buttons[source_btn] = sid

    # ── Warning: news source requires Guardian API key ──
    # Not fatal at router level because only beo-source-news actually
    # needs the key; the router runs fine without it and beo-source-news
    # refuses to start via its own guard.
    has_news = any(
        (v == "news") or (isinstance(v, dict) and v.get("id") == "news")
        for v in menu.values()
    )
    if has_news:
        news_cfg = config.get("news") or {}
        if not news_cfg.get("guardian_api_key"):
            logger.error(
                "Config %s: NEWS source in menu but no news.guardian_api_key"
                " — beo-source-news will refuse to start",
                path,
            )

    return errors


def load_config() -> dict:
    """Load config from the first JSON file found.  Cached after first call.

    Raises :class:`ConfigError` if no file is found, if the first
    matching file has invalid JSON with no usable fallback, or if the
    loaded config has fatal validation errors.
    """
    global _config
    if _config is not None:
        return _config

    last_json_error: tuple[str, Exception] | None = None
    for path in _SEARCH_PATHS:
        try:
            with open(path) as f:
                raw = json.load(f)
            logger.info("Config loaded from %s", path)
            errors = _validate(raw, path)
            if errors:
                bullet_list = "\n  - ".join(errors)
                raise ConfigError(
                    f"Config {path} has {len(errors)} fatal error(s):\n"
                    f"  - {bullet_list}"
                )
            _config = raw
            return _config
        except FileNotFoundError:
            continue
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in %s: %s", path, e)
            last_json_error = (path, e)
            continue

    if last_json_error is not None:
        path, exc = last_json_error
        raise ConfigError(
            f"No usable config: last attempted file {path} "
            f"had invalid JSON ({exc})"
        )
    raise ConfigError(
        "No config.json found — searched: " + ", ".join(_SEARCH_PATHS)
    )


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
