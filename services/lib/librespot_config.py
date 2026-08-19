"""go-librespot's on-disk config — keeping the Spotify Connect name in sync.

go-librespot advertises a `device_name` from its own config file, separate from
ours, and reads it only at startup. The installer writes that file before
/etc/beosound5c/config.json exists (install_librespot runs inside
run_system_setup, ahead of ensure_default_config), so it can't know the device
name — every unit would sit on the literal "BeoSound 5c", and two of them on one
Spotify account collide: the app shows a single entry and the second unit can't
be paired at all.

reconcile-services.sh calls this, so the name is fixed up wherever the config
lands: first install, deploy, and every save from the setup UI. It runs just
before that script try-restarts the beo-* services, which is what makes a rename
take effect — only beo-librespot bounces, not the device.

Deliberately stdlib-only: lib/librespot.py (the runtime HTTP client) imports
aiohttp at module level, and the reconcile script must not depend on that.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

CONFIG_PATH = '/etc/beosound5c/config.json'
LIBRESPOT_CONFIG_PATH = '/etc/beosound5c/librespot/config.yml'

# What config/default.json ships, and the fallback for an empty device name.
DEFAULT_NAME = 'BeoSound 5c'


def spotify_connect_name(device: str) -> str:
    """The Spotify Connect name for a config.json device name.

    'Kitchen' becomes 'BeoSound 5c Kitchen'. The prefix is what makes the entry
    recognisable in Spotify's device picker, which lists every endpoint on the
    account — a bare 'Kitchen' is indistinguishable from the Sonos or Echo that
    someone else in the house already named after the same room. A name that
    already says BeoSound is used as-is, so nobody ends up advertising
    'BeoSound 5c BeoSound 5c'.
    """
    device = (device or '').strip()
    if not device:
        return DEFAULT_NAME
    if 'beosound' in device.lower():
        return device
    return f'{DEFAULT_NAME} {device}'


def _quote(name: str) -> str:
    """YAML double-quoted scalar. go-librespot parses this file as YAML, and an
    unescaped quote in a device name breaks the whole config, not just the name.
    """
    escaped = name.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def set_device_name(yml_path: str, name: str) -> bool:
    """Point go-librespot's device_name at `name`. True if the file changed.

    Missing file means no local player on this device — Sonos and friends
    advertise their own names — so there is nothing to rename. Returns False.

    Written to a temp file alongside and renamed over, so a power cut mid-write
    can't leave go-librespot a truncated config it won't boot from.
    """
    try:
        with open(yml_path) as f:
            lines = f.read().splitlines(keepends=True)
    except FileNotFoundError:
        return False

    new_line = f'device_name: {_quote(name)}\n'
    for i, line in enumerate(lines):
        if line.startswith('device_name:'):
            if line == new_line:
                return False  # already correct — don't churn the file
            lines[i] = new_line
            break
    else:
        # Hand-edited or from a go-librespot version that dropped the key.
        # Appending could contradict a name set some other way, so say so.
        print(f'⚠️  {yml_path} has no device_name line — leaving it alone',
              file=sys.stderr)
        return False

    directory = os.path.dirname(yml_path) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.config.yml.')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(''.join(lines))
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, yml_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return True


def sync_from_config(config_path: str = CONFIG_PATH,
                     yml_path: str = LIBRESPOT_CONFIG_PATH) -> str | None:
    """Derive the Connect name from config.json and write it. Returns the new
    name if the file changed, else None."""
    with open(config_path) as f:
        device = json.load(f).get('device') or ''
    name = spotify_connect_name(device)
    return name if set_device_name(yml_path, name) else None


def main(argv: list[str]) -> int:
    config_path = argv[1] if len(argv) > 1 else CONFIG_PATH
    yml_path = argv[2] if len(argv) > 2 else LIBRESPOT_CONFIG_PATH
    changed = sync_from_config(config_path, yml_path)
    if changed:
        print(f'ℹ️  Spotify Connect name -> {changed}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
