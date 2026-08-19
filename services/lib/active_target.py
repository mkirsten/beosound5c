from __future__ import annotations

"""Runtime active-target pointer for the SPEAKERS "Play here" feature.

The BS5c is configured with one primary speaker (``player.ip``).  The SPEAKERS
menu can re-point it to another room of the *same platform* at runtime; that
choice is **sticky** (survives reboot) and lives here, in a small state file
separate from ``config.json`` so a ``deploy.sh`` never clobbers it.

Robustness (the two review gaps):
  * On read we drop a stale override if the configured ``player.ip`` changed
    since the override was written — a real config edit + redeploy wins over an
    old runtime target.  The config baseline is stored alongside the override.
  * Callers that reach an unreachable target should fall back to the configured
    value; ``effective_player_ip`` never raises.

Consumers read :func:`effective_player_ip` instead of ``cfg("player","ip")`` on
the output/volume path so audio and volume follow the active room.
"""

import json
import logging
import os
import tempfile

from lib.config import cfg

log = logging.getLogger("beo-active-target")


def _state_path() -> str:
    """Prod: /etc/beosound5c/active_target.json.  Dev: a per-user file that is
    never committed or published."""
    etc = "/etc/beosound5c"
    if os.path.isdir(etc) and os.access(etc, os.W_OK):
        return os.path.join(etc, "active_target.json")
    return os.path.join(os.path.expanduser("~"), ".beosound5c_active_target.json")


def _config_ip() -> str:
    return str(cfg("player", "ip", default="") or "")


def _read() -> dict:
    try:
        with open(_state_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        log.warning("Could not read active target: %s", e)
        return {}


def load_active_target() -> dict | None:
    """Return the active override ``{"ip","name"}`` or None.

    Drops the override when the configured ``player.ip`` no longer matches the
    baseline captured when it was saved (config was reconfigured underneath it).
    """
    data = _read()
    ip = data.get("ip")
    if not ip:
        return None
    if data.get("config_ip") != _config_ip():
        log.info("Active target dropped: player.ip changed since it was set "
                 "(baseline %s → config %s)", data.get("config_ip"), _config_ip())
        clear_active_target()
        return None
    return {"ip": ip, "name": data.get("name", "")}


def save_active_target(ip: str, name: str = "") -> bool:
    """Persist a new active target, stamping the current config baseline."""
    payload = {"ip": ip, "name": name, "config_ip": _config_ip()}
    path = _state_path()
    try:
        d = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".active_target.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        log.info("Active target set: %s (%s)", name or "?", ip)
        return True
    except Exception as e:
        log.warning("Could not save active target: %s", e)
        return False


def clear_active_target() -> None:
    """Revert to the configured default."""
    try:
        os.remove(_state_path())
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Could not clear active target: %s", e)


def effective_player_ip() -> str:
    """The IP the BS5c currently drives — the override if set, else config.
    Never raises."""
    try:
        target = load_active_target()
        if target and target.get("ip"):
            return target["ip"]
    except Exception as e:
        log.warning("effective_player_ip fell back to config: %s", e)
    return _config_ip()


def is_overridden() -> bool:
    return load_active_target() is not None
