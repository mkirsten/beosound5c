"""Startup beacon — sends a single anonymous POST to beosound5c.com/api/beacon.

Payload: device_id (stable UUID), version, sources, player_type, volume_type.
The server adds IP and country via Cloudflare headers.

Opt-out: create a file called NO_TELEMETRY in the repo root.
"""

import json
import logging
import os
import uuid

import aiohttp

logger = logging.getLogger('beacon')

BEACON_URL = 'https://beosound5c.com/api/beacon'

# Project-specific namespace so uuid5(namespace, mac) is unique to beosound5c
# even if other projects derive UUIDs from the same MAC.
_BEACON_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, 'beosound5c.com')

# Onboard interfaces on a Pi — MACs are derived from the CPU serial, so they
# are persistent across re-imaging. USB WiFi (wlan1+) is intentionally skipped:
# the dongle is swappable and would invalidate the ID.
_STABLE_IFACES = ('eth0', 'wlan0')

_KNOWN_SYSTEM_KEYS = frozenset({
    'device', 'menu', 'scenes', 'player', 'volume',
    'home_assistant', 'transport', 'showing', 'join',
    'bluetooth', 'remote',
})


def _read_stable_mac() -> str | None:
    for iface in _STABLE_IFACES:
        try:
            with open(f'/sys/class/net/{iface}/address') as f:
                mac = f.read().strip().lower()
            if mac and mac != '00:00:00:00:00:00':
                return mac
        except OSError:
            continue
    return None


def _get_or_create_device_id(base_path: str) -> str:
    id_file = os.path.join(base_path, 'device_id')
    try:
        if os.path.isfile(id_file):
            existing = open(id_file).read().strip()
            if existing:
                try:
                    uuid.UUID(existing)
                    return existing
                except ValueError:
                    # Corrupted file (e.g. truncated by a power cut).  On
                    # Pis the MAC-derived id below is deterministic, so
                    # re-deriving restores the SAME identity instead of
                    # beaconing as a phantom device forever.
                    logger.debug('Invalid device_id %r — re-deriving', existing)

        mac = _read_stable_mac()
        if mac:
            device_id = str(uuid.uuid5(_BEACON_NAMESPACE, mac))
            logger.debug('Derived device_id from MAC %s: %s', mac, device_id)
        else:
            device_id = str(uuid.uuid4())
            logger.debug('No stable MAC available; generated random device_id: %s', device_id)

        with open(id_file, 'w') as f:
            f.write(device_id + '\n')
        return device_id
    except Exception as e:
        logger.debug('device_id file unavailable: %s', e)
        return 'unknown'


def _build_payload(base_path: str) -> dict:
    # Version
    try:
        version = open(os.path.join(base_path, 'VERSION')).read().strip()
    except Exception:
        version = 'unknown'

    # Sources: top-level config keys that aren't system sections
    try:
        from lib.config import load_config
        config = load_config()
        sources = [k for k, v in config.items()
                   if k not in _KNOWN_SYSTEM_KEYS and isinstance(v, dict)]
        player_type = (config.get('player') or {}).get('type', 'unknown')
        volume_type = (config.get('volume') or {}).get('type', 'unknown')
    except Exception:
        sources = []
        player_type = 'unknown'
        volume_type = 'unknown'

    return {
        'device_id':   _get_or_create_device_id(base_path),
        'version':     version,
        'sources':     sources,
        'player_type': player_type,
        'volume_type': volume_type,
    }


async def send_beacon(base_path: str) -> None:
    """Fire-and-forget. Logs result at DEBUG; never raises."""
    try:
        opt_out = os.path.join(base_path, 'NO_TELEMETRY')
        if os.path.isfile(opt_out):
            logger.debug('Telemetry disabled (NO_TELEMETRY file present)')
            return

        payload = _build_payload(base_path)

        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(BEACON_URL, json=payload) as resp:
                body = await resp.json()
                logger.info(
                    'Beacon sent — version=%s country=%s',
                    payload['version'], body.get('country', '?'),
                )
    except Exception as e:
        logger.debug('Beacon failed (non-fatal): %s', e)
