#!/usr/bin/env python3
import asyncio, threading, json, time, sys
import hid, websockets
import subprocess
import os
import logging
import aiohttp
from aiohttp import web, ClientSession
from lib.background_tasks import BackgroundTaskSet
from lib.transport import Transport
from lib.config import cfg
from lib.correlation import install_logging
from lib.endpoints import (
    ROUTER_BROADCAST,
    ROUTER_OUTPUT_OFF,
    ROUTER_RESYNC,
)
from lib.loop_monitor import LoopMonitor
from lib.watchdog import watchdog_loop

logger = install_logging('beo-input')

# Module-level background task set — exceptions in fire-and-forget tasks
# go to the journal instead of disappearing.
_background_tasks = BackgroundTaskSet(logger, label="input")

VID, PID = 0x0cd4, 0x1112
BTN_MAP = {0x20:'left', 0x10:'right', 0x40:'go', 0x80:'power'}
clients = set()
dev = None  # HID device handle, set by scan_loop when connected

# Unified transport for HA communication (webhook, MQTT, or both)
transport = Transport()

# Base path for BeoSound 5c installation (from env, or derive from script location)
BS5C_BASE_PATH = os.getenv('BS5C_BASE_PATH', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROUTER_BROADCAST_URL = ROUTER_BROADCAST

# ——— Update management ———

GITHUB_RELEASES_URL = 'https://api.github.com/repos/mkirsten/beosound5c/releases/latest'
_update_cache: dict = {'data': None, 'fetched_at': 0.0}
_UPDATE_CACHE_TTL = 3600  # seconds
_update_in_progress = False
_update_step = 'idle'  # 'downloading' | 'extracting' | 'installing' | 'restarting'
_UPDATE_EXCLUDES = [
    'web/json/config.json',
    'web/json/spotify_playlists.json',
    'web/json/digit_playlists.json',
    'web/json/apple_music_playlists.json',
    'web/json/apple_music_digit_playlists.json',
    'web/json/tidal_playlists.json',
    'web/json/tidal_digit_playlists.json',
    'web/json/plex_playlists.json',
    'web/json/plex_digit_playlists.json',
    'web/assets/cd-cache',
    'services/sources/spotify/spotify_tokens.json',
    'services/sources/apple_music/apple_music_tokens.json',
    'services/sources/tidal/tidal_tokens.json',
    'services/sources/plex/plex_tokens.json',
    'services/sources/radio/radio_last_station.json',
    'services/sources/radio/radio_favourites.json',
]

# Shared HTTP client session (created lazily in async context)
_http_session = None

async def get_http_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = ClientSession()
    return _http_session

# ——— track current "byte1" state (LED/backlight bits) ———
state_byte1 = 0x00
last_power_press_time = 0  # For debouncing power button
POWER_DEBOUNCE_TIME = 0.5  # Seconds to ignore repeated power button presses
power_button_state = 0  # 0 = released, 1 = pressed

def is_backlight_on():
    """Check backlight state from the hardware state byte."""
    return (state_byte1 & 0x40) != 0

def bs5_send(data: bytes):
    """Low-level HID write."""
    if dev is None:
        return
    try:
        dev.write(data)
    except Exception as e:
        logger.error("HID write failed: %s", e)

def bs5_send_cmd(byte1, byte2=0x00):
    """Build & send HID report."""
    bs5_send(bytes([byte1, byte2]))

def do_click():
    """Send click bit on top of current state."""
    global state_byte1
    bs5_send_cmd(state_byte1 | 0x01)

def set_led(mode: str):
    """mode in {'on','off','blink'}"""
    global state_byte1
    state_byte1 &= ~(0x80 | 0x10)       # clear LED bits
    if mode == 'on':
        state_byte1 |= 0x80
    elif mode == 'blink':
        state_byte1 |= 0x10
    bs5_send_cmd(state_byte1)

def set_backlight(on: bool):
    """Turn backlight bit on/off."""
    global state_byte1

    if on:
        state_byte1 |= 0x40
    else:
        state_byte1 &= ~0x40
    bs5_send_cmd(state_byte1)

    # Control screen using xrandr (Linux only, skip on Mac)
    try:
        env = os.environ.copy()
        env["DISPLAY"] = ":0"
        subprocess.run(
            ["xrandr", "--output", "HDMI-1"] +
            (["--mode", "1024x768", "--rate", "60"] if on else ["--off"]),
            env=env,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            check=False,
            timeout=2
        )
    except FileNotFoundError:
        # xrandr not available (e.g., on macOS) - skip screen control
        pass

def toggle_backlight():
    """Toggle backlight state."""
    current = is_backlight_on()
    new_state = not current
    logger.info("Toggling backlight from %s to %s", current, new_state)
    set_backlight(new_state)

def get_service_logs(service: str, lines: int = 100) -> list:
    """Fetch logs for a systemd service using journalctl."""
    try:
        result = subprocess.run(
            ['journalctl', '-u', service, '-n', str(lines), '--no-pager', '-o', 'short'],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip().split('\n') if result.stdout else []
    except Exception as e:
        return [f'Error fetching logs: {e}']

def get_system_info() -> dict:
    """Get system information including uptime, temp, memory, and service status."""
    info = {}
    try:
        # Uptime
        result = subprocess.run(['uptime', '-p'], capture_output=True, text=True, timeout=2)
        info['uptime'] = result.stdout.strip().replace('up ', '') if result.stdout else '--'

        # CPU Temperature
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000
                info['cpu_temp'] = f'{temp:.1f}C'
        except Exception:
            info['cpu_temp'] = '--'

        # Memory usage
        result = subprocess.run(['free', '-h'], capture_output=True, text=True, timeout=2)
        if result.stdout:
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                if len(parts) >= 3:
                    info['memory'] = f'{parts[2]} / {parts[1]}'

        # IP Address
        try:
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
            if result.stdout:
                info['ip_address'] = result.stdout.strip().split()[0]
        except Exception:
            info['ip_address'] = '--'

        # Hostname
        try:
            result = subprocess.run(['hostname'], capture_output=True, text=True, timeout=2)
            info['hostname'] = result.stdout.strip() if result.stdout else '--'
        except Exception:
            info['hostname'] = '--'

        # Backlight status
        info['backlight'] = 'On' if is_backlight_on() else 'Off'

        # Git info (try git first, fall back to VERSION file from deploy)
        info['git_tag'] = '--'
        try:
            result = subprocess.run(
                ['git', 'describe', '--tags', '--always'],
                capture_output=True, text=True, timeout=2,
                cwd=BS5C_BASE_PATH
            )
            if result.stdout and result.stdout.strip():
                info['git_tag'] = result.stdout.strip()
        except Exception:
            pass
        if info['git_tag'] == '--':
            try:
                vf = os.path.join(BS5C_BASE_PATH, 'VERSION')
                with open(vf) as f:
                    v = f.read().strip()
                    if v:
                        info['git_tag'] = v
            except Exception:
                pass

        # Audio HAT info (from install-time detection)
        info['audio_hat'] = None
        try:
            with open('/etc/beosound5c/audio-hat') as f:
                hat = {}
                for line in f:
                    if '=' in line:
                        k, v = line.strip().split('=', 1)
                        hat[k.lower()] = v
                if hat.get('hat_name'):
                    info['audio_hat'] = hat.get('hat_name')
        except Exception:
            pass

        # Service status — discover all beo-* units dynamically
        info['services'] = {}
        try:
            result = subprocess.run(
                ['systemctl', 'list-units', 'beo-*', '--no-legend', '--no-pager', '--plain'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if not parts:
                    continue
                unit = parts[0]  # e.g. "beo-input.service" or "beo-health.timer"
                # Skip timers — they're background infra, not user-facing
                if unit.endswith('.timer'):
                    continue
                svc = unit.removesuffix('.service')
                active = parts[2] if len(parts) > 2 else 'unknown'  # "active" or "failed" etc.
                info['services'][svc] = 'Running' if active == 'active' else active.capitalize()
        except Exception:
            pass

        # Config from JSON file
        info['config'] = {}
        try:
            import json as _json
            for p in ['/etc/beosound5c/config.json', 'config.json']:
                if os.path.exists(p):
                    with open(p) as f:
                        info['config'] = _json.load(f)
                    break
        except Exception as e:
            logger.error('Config read error: %s', e)

    except Exception as e:
        logger.error('System info error: %s', e)
    return info

def get_network_status() -> dict:
    """Ping default gateway and internet (8.8.8.8) to check connectivity."""
    net = {}
    try:
        # Get default gateway
        result = subprocess.run(
            ['ip', 'route', 'show', 'default'],
            capture_output=True, text=True, timeout=2
        )
        if result.stdout:
            parts = result.stdout.strip().split()
            if 'via' in parts:
                gw = parts[parts.index('via') + 1]
                net['gateway'] = gw
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', '1', gw],
                    capture_output=True, text=True, timeout=3
                )
                if result.returncode == 0 and 'time=' in result.stdout:
                    net['gateway_ping'] = result.stdout.split('time=')[1].split()[0]
                else:
                    net['gateway_ping'] = 'timeout'
        # Ping internet
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '1', '8.8.8.8'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and 'time=' in result.stdout:
            net['internet_ping'] = result.stdout.split('time=')[1].split()[0]
        else:
            net['internet_ping'] = 'timeout'
    except Exception as e:
        logger.error('Network check error: %s', e)
    return net

def _parse_semver(tag: str) -> tuple:
    """Parse 'v0.7.0', 'v0.7.0-dev.21', 'v0.7.0-21-gabc' into a comparable int tuple."""
    import re
    t = re.sub(r'[-+].*$', '', tag.lstrip('v'))
    try:
        return tuple(int(x) for x in t.split('.'))
    except ValueError:
        return (0, 0, 0)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_semver(latest) > _parse_semver(current)


def _get_current_version() -> str:
    """Read installed version from VERSION file, fallback to git describe."""
    try:
        with open(os.path.join(BS5C_BASE_PATH, 'VERSION')) as f:
            v = f.read().strip()
            if v:
                return v
    except Exception:
        pass
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--always'],
            capture_output=True, text=True, timeout=2, cwd=BS5C_BASE_PATH
        )
        if result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return '--'


async def _fetch_latest_release():
    """Fetch latest GitHub release info. Cached for 1 hour."""
    now = time.time()
    if _update_cache['data'] and now - _update_cache['fetched_at'] < _UPDATE_CACHE_TTL:
        return _update_cache['data']
    try:
        session = await get_http_session()
        async with session.get(
            GITHUB_RELEASES_URL,
            headers={'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'beosound5c'},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            result = {
                'latest': data.get('tag_name', ''),
                'release_url': data.get('html_url', ''),
                'tarball_url': data.get('tarball_url', ''),
                'release_notes': (data.get('body') or '').strip(),
                'published_at': data.get('published_at', ''),
            }
            _update_cache['data'] = result
            _update_cache['fetched_at'] = now
            return result
    except Exception as e:
        logger.warning('GitHub release check failed: %s', e)
        return None


async def _run_update():
    """Download and install the latest release, then restart all beo-* services."""
    global _update_in_progress, _update_step
    import tempfile, shutil

    tmp_dir = tempfile.mkdtemp(prefix='beo5c-update-')
    try:
        release = await _fetch_latest_release()
        if not release or not release.get('tarball_url'):
            raise RuntimeError('No release info available')

        latest_tag = release['latest']
        tarball_path = os.path.join(tmp_dir, 'release.tar.gz')
        extract_dir = os.path.join(tmp_dir, 'src')
        os.makedirs(extract_dir)

        logger.info('[update] Downloading %s', latest_tag)
        _update_step = 'downloading'
        session = await get_http_session()
        async with session.get(
            release['tarball_url'],
            headers={'User-Agent': 'beosound5c'},
            timeout=aiohttp.ClientTimeout(total=120),
            allow_redirects=True,
        ) as resp:
            if resp.status not in (200, 302):
                raise RuntimeError(f'Download failed: HTTP {resp.status}')
            with open(tarball_path, 'wb') as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)

        logger.info('[update] Extracting')
        _update_step = 'extracting'
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ['tar', 'xzf', tarball_path, '-C', extract_dir, '--strip-components=1'],
                capture_output=True, timeout=60, check=True,
            ),
        )

        logger.info('[update] Installing files to %s', BS5C_BASE_PATH)
        _update_step = 'installing'
        exclude_args = []
        for exc in _UPDATE_EXCLUDES:
            exclude_args += ['--exclude', exc]
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ['rsync', '-a'] + exclude_args + [extract_dir + '/', BS5C_BASE_PATH + '/'],
                capture_output=True, timeout=60, check=True,
            ),
        )

        # Write new VERSION file and invalidate cache
        with open(os.path.join(BS5C_BASE_PATH, 'VERSION'), 'w') as f:
            f.write(latest_tag + '\n')
        _update_cache['data'] = None
        _update_cache['fetched_at'] = 0.0

        logger.info('[update] Scheduling service restart')
        _update_step = 'restarting'

        # Discover active beo-* services
        res = subprocess.run(
            ['systemctl', 'list-units', 'beo-*.service', '--state=active',
             '--no-legend', '--no-pager', '--plain'],
            capture_output=True, text=True, timeout=5,
        )
        services = [
            line.split()[0].removesuffix('.service')
            for line in res.stdout.splitlines() if line.split()
        ]
        backend = [s for s in services if s != 'beo-ui']
        has_ui = 'beo-ui' in services

        parts = ['sleep 2']
        if backend:
            parts.append(f"sudo systemctl restart {' '.join(backend)}")
        if has_ui:
            parts.append('sleep 3 && sudo systemctl restart beo-ui')

        subprocess.Popen(
            ['bash', '-c', ' && '.join(parts)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    except Exception as e:
        logger.error('[update] Failed: %s', e)
        _update_in_progress = False
        _update_step = 'idle'
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def handle_update_check(request):
    """GET /update/check — current version vs latest GitHub release."""
    current = _get_current_version()

    release = await _fetch_latest_release()

    result = {'current': current, 'update_in_progress': _update_in_progress, 'update_step': _update_step}
    if release:
        latest = release['latest']
        result['latest'] = latest
        result['update_available'] = _is_newer(latest, current) and not _update_in_progress
        result['release_url'] = release['release_url']
        result['release_notes'] = release['release_notes']
    else:
        result['update_available'] = False
        result['error'] = 'Could not reach GitHub'

    return web.json_response(result, headers={'Access-Control-Allow-Origin': '*'})


async def handle_update_run(request):
    """POST /update/run — start background update, return 202 immediately."""
    global _update_in_progress
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    if _update_in_progress:
        return web.json_response(
            {'status': 'already_in_progress'},
            status=409,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    release = await _fetch_latest_release()
    if not release:
        return web.json_response(
            {'status': 'error', 'message': 'Cannot reach GitHub'},
            status=503,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    current = _get_current_version()
    if not _is_newer(release['latest'], current):
        return web.json_response(
            {'status': 'up_to_date'},
            headers={'Access-Control-Allow-Origin': '*'},
        )

    _update_in_progress = True
    _background_tasks.spawn(_run_update(), name='system_update')

    return web.json_response(
        {'status': 'started', 'latest': release['latest']},
        status=202,
        headers={'Access-Control-Allow-Origin': '*'},
    )


def _get_device_ip() -> str:
    """Return the primary LAN IP address of this device."""
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        if result.stdout:
            return result.stdout.strip().split()[0]
    except Exception:
        pass
    return '127.0.0.1'


async def handle_qrcode(request):
    """GET /qrcode — QR code PNG pointing to this device's config page."""
    try:
        import qrcode as _qrcode
        import io
        ip = _get_device_ip()
        url = f'http://{ip}/config'
        qr = _qrcode.QRCode(
            error_correction=_qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color='white', back_color='black')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return web.Response(
            body=buf.getvalue(),
            content_type='image/png',
            headers={'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-cache'},
        )
    except ImportError:
        return web.json_response(
            {'error': 'qrcode package not installed'},
            status=503,
            headers={'Access-Control-Allow-Origin': '*'},
        )
    except Exception as e:
        logger.warning('QR code generation failed: %s', e)
        return web.Response(status=500, headers={'Access-Control-Allow-Origin': '*'})


async def handle_discover_sonos(request):
    """GET /discover/sonos — find Sonos speakers on the local network."""
    try:
        import soco
        loop = asyncio.get_event_loop()
        devices = await loop.run_in_executor(None, lambda: soco.discover(timeout=5) or set())
        result = sorted(
            [{'ip': d.ip_address, 'name': d.player_name} for d in devices],
            key=lambda x: x['name'],
        )
        return web.json_response(result, headers={'Access-Control-Allow-Origin': '*'})
    except ImportError:
        return web.json_response(
            {'error': 'soco not installed'},
            status=503,
            headers={'Access-Control-Allow-Origin': '*'},
        )
    except Exception as e:
        logger.warning('Sonos discovery failed: %s', e)
        return web.json_response([], headers={'Access-Control-Allow-Origin': '*'})


async def handle_discover_bluesound(request):
    """GET /discover/bluesound — find BluOS/Bluesound players via mDNS (_musc._tcp)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            'avahi-browse', '-r', '-t', '-p', '_musc._tcp',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        devices = []
        seen: set = set()
        for line in stdout.decode(errors='replace').splitlines():
            parts = line.split(';')
            # avahi-browse -p resolved record: =;<iface>;<proto>;<name>;<type>;<domain>;<host>;<addr>;<port>;<txt>
            if len(parts) < 9 or parts[0] != '=' or parts[2] != 'IPv4':
                continue
            name, addr = parts[3], parts[7]
            if addr and addr not in seen:
                seen.add(addr)
                devices.append({'name': name, 'ip': addr})
        devices.sort(key=lambda x: x['name'])
        return web.json_response(devices, headers={'Access-Control-Allow-Origin': '*'})
    except asyncio.TimeoutError:
        return web.json_response([], headers={'Access-Control-Allow-Origin': '*'})
    except FileNotFoundError:
        logger.debug('avahi-browse not found — Bluesound discovery unavailable')
        return web.json_response([], headers={'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        logger.warning('Bluesound discovery failed: %s', e)
        return web.json_response([], headers={'Access-Control-Allow-Origin': '*'})


async def _write_secrets(updates: dict) -> None:
    """Update specific env vars in /etc/beosound5c/secrets.env, preserving others."""
    secrets_path = '/etc/beosound5c/secrets.env'
    # Read current content via sudo
    try:
        read_proc = await asyncio.create_subprocess_exec(
            'sudo', 'cat', secrets_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(read_proc.communicate(), timeout=5)
        lines = stdout.decode(errors='replace').splitlines(keepends=True)
    except Exception:
        lines = []

    # Replace matching lines, append any new ones
    new_lines = []
    seen: set = set()
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line else None
        if key and key in updates:
            val = updates[key].replace('\\', '\\\\').replace('"', '\\"')
            new_lines.append(f'{key}="{val}"\n')
            seen.add(key)
        else:
            new_lines.append(line if line.endswith('\n') else line + '\n')
    for key, val in updates.items():
        if key not in seen:
            val_esc = val.replace('\\', '\\\\').replace('"', '\\"')
            new_lines.append(f'{key}="{val_esc}"\n')

    content = ''.join(new_lines)
    try:
        write_proc = await asyncio.create_subprocess_exec(
            'sudo', 'tee', secrets_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(write_proc.communicate(content.encode()), timeout=10)
        if write_proc.returncode != 0:
            msg = stderr.decode().strip() if stderr else 'tee failed'
            logger.error('Secrets write failed: %s', msg)
    except asyncio.TimeoutError:
        logger.error('Timeout writing secrets.env')
    except Exception as e:
        logger.error('Secrets write error: %s', e)


async def handle_config_save(request):
    """POST /config — write a new config.json and restart all beo-* services."""
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {'status': 'error', 'message': 'Invalid JSON'},
            status=400,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    if not isinstance(body, dict) or not body.get('device'):
        return web.json_response(
            {'status': 'error', 'message': 'Config must be a JSON object with a "device" field'},
            status=400,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    # Extract secrets — they go to secrets.env, not config.json
    raw_secrets = body.pop('_secrets', None) or {}
    _SECRET_KEY_MAP = {'ha_token': 'HA_TOKEN', 'mqtt_user': 'MQTT_USER', 'mqtt_password': 'MQTT_PASSWORD'}
    secrets_to_write = {
        _SECRET_KEY_MAP[k]: v
        for k, v in raw_secrets.items()
        if k in _SECRET_KEY_MAP and v
    }

    config_path = '/etc/beosound5c/config.json'
    config_json = json.dumps(body, indent=2, ensure_ascii=False)

    try:
        # Write via sudo tee so the service user doesn't need direct write access
        proc = await asyncio.create_subprocess_exec(
            'sudo', 'tee', config_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(config_json.encode()), timeout=10)
        if proc.returncode != 0:
            msg = stderr.decode().strip() if stderr else 'sudo tee failed'
            logger.error('Config write failed: %s', msg)
            return web.json_response(
                {'status': 'error', 'message': msg},
                status=500,
                headers={'Access-Control-Allow-Origin': '*'},
            )
    except asyncio.TimeoutError:
        return web.json_response(
            {'status': 'error', 'message': 'Timeout writing config'},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'},
        )

    if secrets_to_write:
        await _write_secrets(secrets_to_write)
        logger.info('Secrets updated: %s', ', '.join(secrets_to_write.keys()))

    logger.info('Config saved to %s — scheduling service restart', config_path)

    # Restart all beo-* services after a short delay so the HTTP response
    # can be sent before this process itself is killed and restarted.
    async def _restart():
        await asyncio.sleep(1.5)
        try:
            # beo-input is LAST: systemctl processes each service in order,
            # and killing beo-input kills this subprocess's cgroup. All other
            # services must be restarted before that happens.
            # beo-ui (Chromium) is omitted: it reconnects to backends automatically.
            await asyncio.create_subprocess_exec(
                'sudo', 'systemctl', 'restart',
                'beo-router',
                'beo-player-sonos', 'beo-player-bluesound', 'beo-source-spotify',
                'beo-source-tidal', 'beo-source-apple-music', 'beo-source-plex',
                'beo-source-radio', 'beo-source-cd', 'beo-source-usb',
                'beo-source-news', 'beo-bluetooth', 'beo-masterlink',
                'beo-input',
            )
        except Exception as e:
            logger.error('Service restart failed: %s', e)

    _background_tasks.spawn(_restart(), name='config_restart')

    return web.json_response(
        {'status': 'ok', 'message': 'Config saved, services restarting'},
        headers={'Access-Control-Allow-Origin': '*'},
    )


def get_bt_remotes() -> list:
    """Get paired Bluetooth devices with connection info."""
    remotes = []
    try:
        # Get paired devices
        result = subprocess.run(
            ['bluetoothctl', 'paired-devices'],
            capture_output=True, text=True, timeout=5
        )
        if not result.stdout:
            return remotes

        for line in result.stdout.strip().split('\n'):
            # Format: "Device XX:XX:XX:XX:XX:XX Name"
            parts = line.strip().split(' ', 2)
            if len(parts) < 3 or parts[0] != 'Device':
                continue
            mac = parts[1]
            name = parts[2]

            remote = {'mac': mac, 'name': name, 'connected': False, 'rssi': None, 'battery': None, 'icon': 'input-gaming'}

            # Get detailed info for each device
            try:
                info_result = subprocess.run(
                    ['bluetoothctl', 'info', mac],
                    capture_output=True, text=True, timeout=3
                )
                if info_result.stdout:
                    for info_line in info_result.stdout.split('\n'):
                        info_line = info_line.strip()
                        if info_line.startswith('Connected:'):
                            remote['connected'] = 'yes' in info_line.lower()
                        elif info_line.startswith('RSSI:'):
                            try:
                                # Format: "RSSI: 0xffffffcc" or "RSSI: -52"
                                val = info_line.split(':', 1)[1].strip()
                                if val.startswith('0x'):
                                    rssi = int(val, 16)
                                    if rssi > 0x7FFFFFFF:
                                        rssi -= 0x100000000
                                    remote['rssi'] = rssi
                                else:
                                    remote['rssi'] = int(val)
                            except (ValueError, IndexError):
                                pass
                        elif info_line.startswith('Battery Percentage:'):
                            try:
                                # Format: "Battery Percentage: 0x55 (85)"
                                val = info_line.split('(')[1].rstrip(')')
                                remote['battery'] = int(val)
                            except (ValueError, IndexError):
                                pass
                        elif info_line.startswith('Icon:'):
                            remote['icon'] = info_line.split(':', 1)[1].strip()
            except Exception as e:
                logger.error('BT info error for %s: %s', mac, e)

            remotes.append(remote)
    except Exception as e:
        logger.error('BT remotes error: %s', e)
    return remotes


async def _run_cmd(*args, timeout: float = 3.0) -> int:
    """Run a subprocess without blocking the asyncio event loop."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0


async def start_bt_pairing() -> dict:
    """Start Bluetooth discoverable + scanning mode for pairing."""
    try:
        await _run_cmd('bluetoothctl', 'discoverable', 'on', timeout=3)
        await _run_cmd('bluetoothctl', 'scan', 'on', timeout=3)
        logger.info('BT pairing mode started')
        return {'status': 'started', 'message': 'Scanning for remotes... Press pairing button on remote.'}
    except Exception as e:
        logger.error('BT pairing error: %s', e)
        return {'status': 'error', 'message': str(e)}


# Live log streaming
log_stream_processes = {}

async def start_log_stream(ws, service: str):
    """Start streaming logs for a service."""
    global log_stream_processes

    # Stop any existing stream for this websocket
    await stop_log_stream(ws)

    try:
        process = subprocess.Popen(
            ['journalctl', '-u', service, '-f', '-n', '50', '--no-pager', '-o', 'short'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        log_stream_processes[id(ws)] = process
        logger.info('Log stream started for %s', service)

        # Read and send log lines in background
        async def stream_logs():
            try:
                while process.poll() is None:
                    line = await asyncio.get_running_loop().run_in_executor(
                        None, process.stdout.readline
                    )
                    if line and id(ws) in log_stream_processes:
                        try:
                            await ws.send(json.dumps({
                                'type': 'log_line',
                                'service': service,
                                'line': line.rstrip()
                            }))
                        except Exception:
                            break
                    await asyncio.sleep(0.01)
            except Exception as e:
                logger.error('Log stream error: %s', e)

        _background_tasks.spawn(stream_logs(), name=f"log_stream_{service}")
    except Exception as e:
        logger.error('Log stream failed to start: %s', e)

async def stop_log_stream(ws):
    """Stop log streaming for a websocket."""
    global log_stream_processes
    ws_id = id(ws)
    if ws_id in log_stream_processes:
        process = log_stream_processes[ws_id]
        process.terminate()
        del log_stream_processes[ws_id]
        logger.info('Log stream stopped')

_ALLOWED_SERVICES = {
    'beo-bluetooth', 'beo-masterlink', 'beo-router', 'beo-input', 'beo-http', 'beo-ui',
    'beo-player-sonos', 'beo-player-bluesound', 'beo-player-local',
    'beo-source-cd', 'beo-source-spotify', 'beo-source-plex', 'beo-source-radio',
    'beo-source-usb', 'beo-source-news', 'beo-source-tidal', 'beo-source-apple-music',
    'beo-librespot', 'beo-health', 'beo-beo6',
}

async def restart_service(action: str):
    """Restart a service or reboot the system."""
    logger.info('Executing restart action: %s', action)
    try:
        if action == 'reboot':
            subprocess.Popen(['sudo', 'reboot'])  # fire-and-forget, non-blocking
        elif action == 'restart-all':
            subprocess.Popen(['sudo', 'systemctl', 'restart', 'beo-masterlink', 'beo-bluetooth', 'beo-router', 'beo-player-sonos', 'beo-source-cd', 'beo-source-spotify', 'beo-input', 'beo-http', 'beo-ui'])
        elif action.startswith('restart-'):
            service = 'beo-' + action.replace('restart-', '')
            # CD source: eject disc first, use correct service name
            if service == 'beo-cd':
                try:
                    await _run_cmd('eject', '/dev/sr0', timeout=5)
                except asyncio.TimeoutError:
                    logger.warning('eject /dev/sr0 timed out — proceeding with restart')
                service = 'beo-source-cd'
            if service not in _ALLOWED_SERVICES:
                logger.warning('Blocked restart of unknown service: %s', service)
                return
            subprocess.Popen(['sudo', 'systemctl', 'restart', service])
    except Exception as e:
        logger.error('Restart error: %s', e)

async def refresh_spotify_playlists(ws):
    """Run the Spotify playlist fetch script."""
    logger.info('Starting Spotify playlist refresh')
    try:
        # Run fetch_playlists.py in background
        spotify_dir = os.path.join(BS5C_BASE_PATH, 'services/sources/spotify')
        process = subprocess.Popen(
            ['python3', os.path.join(spotify_dir, 'fetch.py')],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=spotify_dir
        )

        # Send initial status
        await ws.send(json.dumps({
            'type': 'spotify_refresh',
            'status': 'started',
            'message': 'Fetching playlists from Spotify...'
        }))

        # Wait for completion (non-blocking via executor)
        def wait_for_process():
            stdout, stderr = process.communicate(timeout=120)
            return process.returncode, stdout, stderr

        returncode, stdout, stderr = await asyncio.get_running_loop().run_in_executor(
            None, wait_for_process
        )

        if returncode == 0:
            logger.info('Spotify playlist refresh completed')
            await ws.send(json.dumps({
                'type': 'spotify_refresh',
                'status': 'completed',
                'message': 'Playlists updated successfully'
            }))
        else:
            logger.error('Spotify playlist refresh failed: %s', stderr)
            await ws.send(json.dumps({
                'type': 'spotify_refresh',
                'status': 'error',
                'message': f'Error: {stderr[:200] if stderr else "Unknown error"}'
            }))

    except subprocess.TimeoutExpired:
        process.kill()
        logger.warning('Spotify playlist refresh timed out')
        await ws.send(json.dumps({
            'type': 'spotify_refresh',
            'status': 'error',
            'message': 'Refresh timed out after 2 minutes'
        }))
    except Exception as e:
        logger.error('Spotify error: %s', e)
        await ws.send(json.dumps({
            'type': 'spotify_refresh',
            'status': 'error',
            'message': str(e)
        }))

# ——— HTTP Webhook Server ———

async def handle_camera_stream(request):
    """Proxy camera stream from Home Assistant to avoid CORS issues."""
    ha_url = cfg("home_assistant", "url", default="http://homeassistant.local:8123")
    ha_token = os.getenv('HA_TOKEN', '')

    # Get camera entity from query params, default to doorbell
    entity = request.query.get('entity', 'camera.doorbell_medium_resolution_channel')

    try:
        session = await get_http_session()
        headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}

        camera_url = f'{ha_url}/api/camera_proxy_stream/{entity}'
        logger.info('Proxying camera stream from: %s', camera_url)

        async with session.get(camera_url, headers=headers) as resp:
            if resp.status == 200:
                response = web.StreamResponse(
                    status=200,
                    headers={
                        'Content-Type': resp.content_type or 'multipart/x-mixed-replace;boundary=frame',
                        'Access-Control-Allow-Origin': '*',
                        'Cache-Control': 'no-cache, no-store, must-revalidate',
                    }
                )
                await response.prepare(request)

                async for chunk in resp.content.iter_any():
                    await response.write(chunk)

                return response
            else:
                logger.warning('Camera HA returned status: %s', resp.status)
                return web.json_response(
                    {'error': f'Camera unavailable: HTTP {resp.status}'},
                    status=resp.status,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
    except Exception as e:
        logger.error('Camera error: %s', e)
        return web.json_response(
            {'error': str(e)},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'}
        )

async def handle_camera_snapshot(request):
    """Get a single snapshot from camera via Home Assistant."""
    ha_url = cfg("home_assistant", "url", default="http://homeassistant.local:8123")
    ha_token = os.getenv('HA_TOKEN', '')

    entity = request.query.get('entity', 'camera.doorbell_medium_resolution_channel')

    try:
        session = await get_http_session()
        headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}

        camera_url = f'{ha_url}/api/camera_proxy/{entity}'
        logger.info('Getting camera snapshot from: %s', camera_url)

        async with session.get(camera_url, headers=headers) as resp:
            if resp.status == 200:
                content = await resp.read()
                return web.Response(
                    body=content,
                    content_type=resp.content_type or 'image/jpeg',
                    headers={'Access-Control-Allow-Origin': '*'}
                )
            else:
                logger.warning('Camera HA returned status: %s', resp.status)
                return web.json_response(
                    {'error': f'Camera unavailable: HTTP {resp.status}'},
                    status=resp.status,
                    headers={'Access-Control-Allow-Origin': '*'}
                )
    except Exception as e:
        logger.error('Camera error: %s', e)
        return web.json_response(
            {'error': str(e)},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'}
        )

async def _forward_to_router(event_type: str, data: dict):
    """Forward a UI event to the router's broadcast endpoint for WS delivery."""
    try:
        s = await get_http_session()
        async with s.post(
            ROUTER_BROADCAST_URL,
            json={'type': event_type, 'data': data},
            timeout=aiohttp.ClientTimeout(total=2),
        ) as resp:
            logger.debug('→ router broadcast %s: HTTP %d', event_type, resp.status)
    except Exception as e:
        logger.warning('Router broadcast %s failed: %s', event_type, e)


async def process_command(data: dict) -> dict:
    """Process an incoming command (from HTTP webhook or MQTT).

    Returns a result dict with 'status' and other fields.
    """
    command = data.get('command', '')
    params = data.get('params', {})

    if command == 'screen_on':
        logger.info('Turning screen ON')
        set_backlight(True)
        return {'status': 'ok', 'screen': 'on'}

    elif command == 'screen_off':
        logger.info('Turning screen OFF')
        set_backlight(False)
        # Also power off audio output (BeoLab 5 etc.)
        try:
            s = await get_http_session()
            await s.post(ROUTER_OUTPUT_OFF, timeout=aiohttp.ClientTimeout(total=2))
        except Exception:
            pass
        return {'status': 'ok', 'screen': 'off'}

    elif command == 'screen_toggle':
        logger.info('Toggling screen')
        toggle_backlight()
        return {'status': 'ok', 'screen': 'on' if is_backlight_on() else 'off'}

    elif command == 'show_page':
        page = params.get('page', 'now_playing')
        logger.info('Showing page: %s', page)
        await _forward_to_router('navigate', {'page': page})
        return {'status': 'ok', 'page': page}

    elif command == 'restart':
        target = params.get('target', 'all')
        logger.info('Restarting: %s', target)
        if target == 'system':
            await restart_service('reboot')
        else:
            await restart_service('restart-all')
        return {'status': 'ok', 'restart': target}

    elif command == 'wake':
        page = params.get('page', 'now_playing')
        logger.info('Waking up and showing: %s', page)
        set_backlight(True)
        await _forward_to_router('navigate', {'page': page})
        return {'status': 'ok', 'screen': 'on', 'page': page}

    elif command == 'status':
        # get_system_info() runs ~7 blocking subprocess.run calls with multi-
        # second timeouts; stay off the event loop.
        info = await asyncio.get_running_loop().run_in_executor(None, get_system_info)
        info['screen'] = 'on' if is_backlight_on() else 'off'
        return {'status': 'ok', **info}

    elif command == 'next_screen':
        logger.info('Next screen')
        set_backlight(True)
        await _forward_to_router('navigate', {'page': 'next'})
        return {'status': 'ok', 'action': 'next_screen'}

    elif command == 'prev_screen':
        logger.info('Previous screen')
        set_backlight(True)
        await _forward_to_router('navigate', {'page': 'previous'})
        return {'status': 'ok', 'action': 'prev_screen'}

    elif command == 'show_camera':
        title = params.get('title', 'Camera')
        camera_entity = params.get('camera_entity', 'camera.doorbell_medium_resolution_channel')
        camera_id = params.get('camera_id', 'doorbell')
        actions = params.get('actions', {})

        logger.info('Showing camera overlay: %s (%s)', title, camera_entity)
        set_backlight(True)
        await _forward_to_router('camera_overlay', {
            'action': 'show',
            'title': title,
            'camera_entity': camera_entity,
            'camera_id': camera_id,
            'actions': actions
        })
        return {'status': 'ok', 'command': 'show_camera', 'title': title}

    elif command == 'dismiss_camera':
        logger.info('Dismissing camera overlay')
        await _forward_to_router('camera_overlay', {'action': 'hide'})
        return {'status': 'ok', 'command': 'dismiss_camera'}

    elif command == 'add_menu_item':
        preset = params.get('preset')
        logger.info('Adding menu item (preset=%s)', preset)
        data = {'action': 'add'}
        if preset:
            data['preset'] = preset
        else:
            data.update({
                'title': params.get('title', 'Item'),
                'path': params.get('path', 'menu/item'),
                'after': params.get('after', 'menu/playing')
            })
        await _forward_to_router('menu_item', data)
        return {'status': 'ok', 'command': 'add_menu_item'}

    elif command == 'remove_menu_item':
        path = params.get('path')
        preset = params.get('preset')
        logger.info('Removing menu item (path=%s, preset=%s)', path, preset)
        data = {'action': 'remove'}
        if path:
            data['path'] = path
        if preset:
            data['preset'] = preset
        await _forward_to_router('menu_item', data)
        return {'status': 'ok', 'command': 'remove_menu_item'}

    elif command in ('hide_menu_item', 'show_menu_item'):
        path = params.get('path')
        action = 'hide' if command == 'hide_menu_item' else 'show'
        logger.info('%s menu item: %s', action.capitalize(), path)
        await _forward_to_router('menu_item', {'action': action, 'path': path})
        return {'status': 'ok', 'command': command}

    elif command == 'broadcast':
        # Forward an arbitrary event to UI via router WS
        evt_type = params.get('type', 'unknown')
        evt_data = params.get('data', {})
        logger.info('Broadcasting event: %s', evt_type)
        await _forward_to_router(evt_type, evt_data)
        return {'status': 'ok', 'command': 'broadcast', 'event_type': evt_type}

    else:
        return {'status': 'error', 'message': f'Unknown command: {command}'}


async def handle_webhook(request):
    """Handle incoming webhook requests from Home Assistant (HTTP)."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        data = await request.json()
        logger.info('Webhook received: %s', data)

        result = await process_command(data)

        status_code = 400 if result.get('status') == 'error' else 200
        response = web.json_response(result, status=status_code)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    except json.JSONDecodeError:
        response = web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error('Webhook error: %s', e)
        response = web.json_response({'status': 'error', 'message': str(e)}, status=500)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response


async def handle_mqtt_command(data: dict):
    """Handle incoming commands via MQTT (fire-and-forget, no response needed)."""
    logger.info('MQTT command received: %s', data)
    try:
        await process_command(data)
    except Exception as e:
        logger.error('MQTT command error: %s', e)

async def handle_health(request):
    """Health check endpoint."""
    return web.json_response({
        'status': 'ok',
        'service': 'beo-input',
        'screen': 'on' if is_backlight_on() else 'off',
        'hid_connected': dev is not None,
    })

async def handle_info(request):
    """GET /info — device info (IP, hostname) for UI use."""
    return web.json_response(
        {'ip_address': _get_device_ip(), 'hostname': subprocess.run(['hostname'], capture_output=True, text=True).stdout.strip()},
        headers={'Access-Control-Allow-Origin': '*'},
    )

async def handle_led(request):
    """Quick LED control for visual feedback. GET /led?mode=pulse|on|off|blink"""
    mode = request.query.get('mode', 'pulse')

    if mode == 'pulse':
        # Quick pulse: on then off after 100ms
        set_led('on')
        asyncio.get_running_loop().call_later(0.1, lambda: set_led('off'))
    else:
        set_led(mode)

    return web.Response(text='ok')

async def handle_forward(request):
    """Forward event to Home Assistant via configured transport (webhook/MQTT/both)."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        data = await request.json()
        logger.info('Forwarding via transport (%s): %s', transport.mode, data)

        await transport.send_event(data)

        response = web.json_response({'status': 'forwarded', 'transport': transport.mode})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    except json.JSONDecodeError:
        response = web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error('Forward error: %s', e)
        response = web.json_response({'status': 'error', 'message': str(e)}, status=500)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

async def handle_appletv(request):
    """Fetch Apple TV media info from Home Assistant."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    ha_url = cfg("home_assistant", "url", default="http://homeassistant.local:8123")
    ha_token = os.getenv('HA_TOKEN', '')
    entity_id = cfg("showing", "entity_id")
    if not entity_id:
        response = web.json_response({'error': 'showing.entity_id not configured', 'title': '—', 'app_name': '—', 'friendly_name': '—', 'artwork': '', 'state': 'error'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    try:
        session = await get_http_session()
        headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}
        async with session.get(f'{ha_url}/api/states/{entity_id}', headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Transform for frontend
                    result = {
                        'title': data.get('attributes', {}).get('media_title', '—'),
                        'app_name': data.get('attributes', {}).get('app_name', '—'),
                        'friendly_name': data.get('attributes', {}).get('friendly_name', '—'),
                        'artwork': data.get('attributes', {}).get('entity_picture', ''),
                        'state': data.get('state', 'unknown')
                    }
                    # Prepend HA URL to artwork if relative
                    if result['artwork'] and not result['artwork'].startswith('http'):
                        result['artwork'] = f'{ha_url}{result["artwork"]}'
                    response = web.json_response(result)
                else:
                    response = web.json_response({'error': 'Failed to fetch', 'title': '—', 'app_name': '—', 'friendly_name': '—', 'artwork': '', 'state': 'unavailable'}, status=resp.status)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
    except Exception as e:
        logger.error('Apple TV error: %s', e)
        response = web.json_response({'error': str(e), 'title': '—', 'app_name': '—', 'friendly_name': '—', 'artwork': '', 'state': 'error'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

async def handle_people(request):
    """Fetch all person.* entities from Home Assistant."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    ha_url = cfg("home_assistant", "url", default="http://homeassistant.local:8123")
    ha_token = os.getenv('HA_TOKEN', '')

    try:
        session = await get_http_session()
        headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}
        async with session.get(f'{ha_url}/api/states', headers=headers) as resp:
                if resp.status == 200:
                    all_states = await resp.json()
                    # Filter for person.* entities, excluding system users
                    excluded_users = {'person.mqtt', 'person.ha_user', 'person.ha-user'}
                    people = []
                    for entity in all_states:
                        entity_id = entity.get('entity_id', '')
                        if entity_id.startswith('person.') and entity_id not in excluded_users:
                            attrs = entity.get('attributes', {})
                            entity_picture = attrs.get('entity_picture', '')
                            # Prepend HA URL to picture if relative
                            if entity_picture and not entity_picture.startswith('http'):
                                entity_picture = f'{ha_url}{entity_picture}'
                            people.append({
                                'entity_id': entity_id,
                                'friendly_name': attrs.get('friendly_name', entity_id.replace('person.', '').title()),
                                'state': entity.get('state', 'unknown'),
                                'entity_picture': entity_picture
                            })
                    response = web.json_response(people)
                else:
                    response = web.json_response({'error': 'Failed to fetch'}, status=resp.status)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
    except Exception as e:
        logger.error('People error: %s', e)
        response = web.json_response({'error': str(e)})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

async def handle_bt_remotes(request):
    """Get paired Bluetooth remotes."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        remotes = await asyncio.get_running_loop().run_in_executor(None, get_bt_remotes)
        response = web.json_response(remotes)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error('BT remotes error: %s', e)
        response = web.json_response({'error': str(e)}, status=500)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

async def handler(ws, path=None):
    clients.add(ws)
    # Ask router to re-probe all sources so menu items are up-to-date for this new client
    _background_tasks.spawn(_notify_sources_resync(), name="notify_sources_resync")
    recv_task = asyncio.create_task(receive_commands(ws))
    try:
        await ws.wait_closed()
    finally:
        recv_task.cancel()
        clients.remove(ws)
        await stop_log_stream(ws)  # Clean up any active log streams


async def _notify_sources_resync():
    """Ask router to re-probe all sources (handles any service that restarted)."""
    try:
        session = await get_http_session()
        async with session.post(ROUTER_RESYNC,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                resynced = data.get('resynced', [])
                if resynced:
                    logger.info('Sources resynced for new client: %s', resynced)
    except Exception as e:
        logger.debug('Source resync skipped (router not reachable): %s', e)

async def broadcast(msg: str):
    if not clients:
        return
    await asyncio.gather(
        *(ws.send(msg) for ws in clients),
        return_exceptions=True
    )

async def receive_commands(ws):
    async for raw in ws:
        try:
            msg = json.loads(raw)
            logger.debug('WS received: %s', msg)

            # Handle hardware commands
            if msg.get('type') != 'command':
                continue

            cmd    = msg.get('command')
            params = msg.get('params', {})
            if cmd == 'click':
                do_click()
            elif cmd == 'led':
                set_led(params.get('mode','on'))
            elif cmd == 'backlight':
                set_backlight(bool(params.get('on',True)))
            elif cmd == 'get_logs':
                service = params.get('service', 'beo-input')
                lines = params.get('lines', 100)
                logs = await asyncio.get_running_loop().run_in_executor(
                    None, get_service_logs, service, lines)
                await ws.send(json.dumps({'type': 'logs', 'service': service, 'logs': logs}))
            elif cmd == 'start_log_stream':
                await start_log_stream(ws, params.get('service', 'beo-masterlink'))
            elif cmd == 'stop_log_stream':
                await stop_log_stream(ws)
            elif cmd == 'get_system_info':
                info = await asyncio.get_running_loop().run_in_executor(
                    None, get_system_info)
                await ws.send(json.dumps({'type': 'system_info', **info}))
            elif cmd == 'get_network_status':
                net = await asyncio.get_running_loop().run_in_executor(None, get_network_status)
                await ws.send(json.dumps({'type': 'network_status', **net}))
            elif cmd == 'restart_service':
                await restart_service(params.get('action', ''))
            elif cmd == 'refresh_playlists':
                await refresh_spotify_playlists(ws)
            elif cmd == 'get_bt_remotes':
                remotes = await asyncio.get_running_loop().run_in_executor(None, get_bt_remotes)
                await ws.send(json.dumps({'type': 'bt_remotes', 'remotes': remotes}))
            elif cmd == 'start_bt_pairing':
                result = await start_bt_pairing()
                await ws.send(json.dumps({'type': 'bt_pairing', **result}))
        except Exception as e:
            logger.error('WebSocket error: %s', e)

# ——— HID parse & broadcast loop ———

def parse_report(rep: list, loop=None):
    global last_power_press_time, power_button_state
    if len(rep) < 4:
        logger.warning("Truncated HID report (%d bytes), ignoring", len(rep))
        return None, None, None, None
    nav_evt = vol_evt = btn_evt = None
    laser_pos = rep[2]

    if rep[0] != 0:
        d = rep[0]
        nav_evt = {
            'direction': 'clock' if d < 0x80 else 'counter',
            'speed':     d if d < 0x80 else 256-d
        }
    if rep[1] != 0:
        d = rep[1]
        vol_evt = {
            'direction': 'clock' if d < 0x80 else 'counter',
            'speed':     d if d < 0x80 else 256-d
        }
    
    # Handle power button with state machine
    b = rep[3]
    is_power_pressed = (b & 0x80) != 0  # Check if power bit is set
    
    # Only create button events for non-power buttons
    if b in BTN_MAP and b != 0x80:
        btn_evt = {'button': BTN_MAP[b]}
    
    # State machine for power button
    if is_power_pressed:
        # Button is pressed
        if power_button_state == 0:  # Was released before
            power_button_state = 1  # Now pressed
            logger.info("Power button pressed")
    else:
        # Button is released
        if power_button_state == 1:  # Was pressed before
            power_button_state = 0  # Now released
            logger.info("Power button released")
            
            # Check debounce time
            current_time = time.time()
            if current_time - last_power_press_time > POWER_DEBOUNCE_TIME:
                logger.info("Power button action triggered")
                toggle_backlight()
                do_click()
                # Power off speakers when screen turns off (speakers power on via playback)
                if not is_backlight_on():
                    try:
                        asyncio.run_coroutine_threadsafe(
                            _output_power(ROUTER_OUTPUT_OFF), loop)
                    except Exception:
                        pass
                last_power_press_time = current_time
                # Create button event for power button release
                btn_evt = {'button': 'power'}
            else:
                logger.debug("Power button debounced (pressed too soon)")

    return nav_evt, vol_evt, btn_evt, laser_pos

async def _output_power(url):
    """Fire-and-forget call to router output power endpoint."""
    try:
        s = await get_http_session()
        await s.post(url, timeout=aiohttp.ClientTimeout(total=2))
    except Exception:
        pass

_hid_alive = True   # cleared when scan_loop thread dies

HID_RETRY_INTERVAL = 3  # seconds between device scan retries

def scan_loop(loop):
    global dev, _hid_alive

    while _hid_alive:
        # --- Try to find and open the device ---
        if dev is None:
            devices = hid.enumerate(VID, PID)
            if not devices:
                time.sleep(HID_RETRY_INTERVAL)
                continue
            try:
                d = hid.device()
                d.open(VID, PID)
                d.set_nonblocking(True)
                dev = d
                logger.info("Opened BS5 @ VID:PID=%04x:%04x", VID, PID)
                # Send current state (backlight/LED bits) to hardware on connect
                bs5_send_cmd(state_byte1)
            except Exception as e:
                logger.warning("Failed to open BS5: %s", e)
                time.sleep(HID_RETRY_INTERVAL)
                continue

        # --- Read loop (runs while device is connected) ---
        last_laser = None
        first = True
        last_probe_time = time.monotonic()
        HID_PROBE_INTERVAL = 60  # seconds between liveness probes
        try:
            while True:
                rpt = dev.read(64, timeout_ms=50)
                if rpt:
                    rep = list(rpt)
                    nav_evt, vol_evt, btn_evt, laser_pos = parse_report(rep, loop)
                    if laser_pos is None:
                        continue

                    for evt_type, evt in (
                        ('nav',    nav_evt),
                        ('volume', vol_evt),
                        ('button', btn_evt),
                    ):
                        if evt:
                            asyncio.run_coroutine_threadsafe(
                                broadcast(json.dumps({'type':evt_type,'data':evt})),
                                loop
                            )

                    if first or laser_pos != last_laser:
                        asyncio.run_coroutine_threadsafe(
                            broadcast(json.dumps({'type':'laser','data':{'position':laser_pos}})),
                            loop
                        )
                        last_laser, first = laser_pos, False

                # Periodic liveness probe: re-send current state to device.
                # A stale handle will throw here, triggering reconnect.
                now = time.monotonic()
                if now - last_probe_time > HID_PROBE_INTERVAL:
                    dev.write(bytes([state_byte1, 0x00]))
                    last_probe_time = now

                time.sleep(0.001)
        except Exception as e:
            logger.warning("BS5 disconnected: %s — will retry", e)
            try:
                dev.close()
            except Exception:
                pass
            dev = None
            time.sleep(HID_RETRY_INTERVAL)

    _hid_alive = False

# ——— Main & server start ———

async def main():
    # Start transport (webhook/MQTT/both for HA communication)
    transport.set_command_handler(handle_mqtt_command)
    await transport.start()
    logger.info("Transport started (mode: %s)", transport.mode)

    ws_srv = await websockets.serve(handler, '0.0.0.0', 8765)
    logger.info("WebSocket server listening on ws://0.0.0.0:8765")

    # Start HTTP webhook server
    app = web.Application()
    app.router.add_post('/webhook', handle_webhook)
    app.router.add_options('/webhook', handle_webhook)  # CORS preflight
    app.router.add_post('/forward', handle_forward)
    app.router.add_options('/forward', handle_forward)  # CORS preflight
    app.router.add_get('/appletv', handle_appletv)
    app.router.add_options('/appletv', handle_appletv)  # CORS preflight
    app.router.add_get('/people', handle_people)
    app.router.add_options('/people', handle_people)  # CORS preflight
    app.router.add_get('/health', handle_health)
    app.router.add_get('/info', handle_info)
    app.router.add_get('/led', handle_led)
    app.router.add_get('/bt/remotes', handle_bt_remotes)
    app.router.add_options('/bt/remotes', handle_bt_remotes)  # CORS preflight
    app.router.add_get('/camera/stream', handle_camera_stream)
    app.router.add_get('/camera/snapshot', handle_camera_snapshot)
    app.router.add_get('/update/check', handle_update_check)
    app.router.add_post('/update/run', handle_update_run)
    app.router.add_options('/update/run', handle_update_run)
    app.router.add_get('/qrcode', handle_qrcode)
    app.router.add_get('/discover/sonos', handle_discover_sonos)
    app.router.add_get('/discover/bluesound', handle_discover_bluesound)
    app.router.add_post('/config', handle_config_save)
    app.router.add_options('/config', handle_config_save)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    http_site = web.TCPSite(runner, '0.0.0.0', 8767)
    await http_site.start()
    logger.info("HTTP webhook server listening on http://0.0.0.0:8767")

    # Start HID scanning thread
    threading.Thread(target=scan_loop, args=(asyncio.get_running_loop(),), daemon=True).start()

    # Turn screen on at startup so the display is always visible after boot
    set_backlight(True)

    # Start systemd watchdog heartbeat
    asyncio.create_task(watchdog_loop())

    # Event-loop lag detector
    loop_monitor = LoopMonitor().start()

    try:
        # Wait for server to close
        await ws_srv.wait_closed()
    finally:
        await loop_monitor.stop()
        await _background_tasks.cancel_all()
        await transport.stop()
        if _http_session and not _http_session.closed:
            await _http_session.close()

if __name__ == '__main__':
    asyncio.run(main())
