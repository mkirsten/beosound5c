#!/usr/bin/env python3
import asyncio, threading, json, time, sys
import hid, websockets
import subprocess
import os
import logging
import aiohttp
from aiohttp import web, ClientSession
from lib.transport import Transport
from lib.config import cfg
from lib.watchdog import watchdog_loop

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-input')

VID, PID = 0x0cd4, 0x1112
BTN_MAP = {0x20:'left', 0x10:'right', 0x40:'go', 0x80:'power'}
clients = set()

# Unified transport for HA communication (webhook, MQTT, or both)
transport = Transport()

# Base path for BeoSound 5c installation (from env, or derive from script location)
BS5C_BASE_PATH = os.getenv('BS5C_BASE_PATH', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Media server connection
MEDIA_SERVER_URL = 'ws://localhost:8766/ws'
media_server_ws = None

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

        # Git info
        try:
            result = subprocess.run(
                ['git', 'describe', '--tags', '--always'],
                capture_output=True, text=True, timeout=2,
                cwd=BS5C_BASE_PATH
            )
            info['git_tag'] = result.stdout.strip() if result.stdout else '--'
        except Exception:
            info['git_tag'] = '--'

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


async def start_bt_pairing() -> dict:
    """Start Bluetooth discoverable + scanning mode for pairing."""
    try:
        subprocess.run(['bluetoothctl', 'discoverable', 'on'], capture_output=True, timeout=3)
        subprocess.run(['bluetoothctl', 'scan', 'on'], capture_output=True, timeout=3)
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
                    line = await asyncio.get_event_loop().run_in_executor(
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

        asyncio.create_task(stream_logs())
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

def restart_service(action: str):
    """Restart a service or reboot the system."""
    logger.info('Executing restart action: %s', action)
    try:
        if action == 'reboot':
            subprocess.Popen(['sudo', 'reboot'])
        elif action == 'restart-all':
            subprocess.Popen(['sudo', 'systemctl', 'restart', 'beo-masterlink', 'beo-bluetooth', 'beo-router', 'beo-player-sonos', 'beo-source-cd', 'beo-source-spotify', 'beo-input', 'beo-http', 'beo-ui'])
        elif action.startswith('restart-'):
            service = 'beo-' + action.replace('restart-', '')
            # CD source: eject disc first, use correct service name
            if service == 'beo-cd':
                subprocess.run(['eject', '/dev/sr0'], timeout=5, capture_output=True)
                service = 'beo-source-cd'
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

        returncode, stdout, stderr = await asyncio.get_event_loop().run_in_executor(
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
        async with ClientSession() as session:
            headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}

            # Get camera stream URL from HA
            camera_url = f'{ha_url}/api/camera_proxy_stream/{entity}'
            logger.info('Proxying camera stream from: %s', camera_url)

            async with session.get(camera_url, headers=headers) as resp:
                if resp.status == 200:
                    # Stream the MJPEG response
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

    # Get camera entity from query params, default to doorbell
    entity = request.query.get('entity', 'camera.doorbell_medium_resolution_channel')

    try:
        async with ClientSession() as session:
            headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}

            # Get camera snapshot from HA
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
            async with ClientSession() as s:
                await s.post('http://localhost:8770/router/output/off', timeout=aiohttp.ClientTimeout(total=2))
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
        await broadcast(json.dumps({
            'type': 'navigate',
            'data': {'page': page}
        }))
        return {'status': 'ok', 'page': page}

    elif command == 'restart':
        target = params.get('target', 'all')
        logger.info('Restarting: %s', target)
        if target == 'system':
            restart_service('reboot')
        else:
            restart_service('restart-all')
        return {'status': 'ok', 'restart': target}

    elif command == 'wake':
        page = params.get('page', 'now_playing')
        logger.info('Waking up and showing: %s', page)
        set_backlight(True)
        await broadcast(json.dumps({
            'type': 'navigate',
            'data': {'page': page}
        }))
        return {'status': 'ok', 'screen': 'on', 'page': page}

    elif command == 'status':
        info = get_system_info()
        info['screen'] = 'on' if is_backlight_on() else 'off'
        return {'status': 'ok', **info}

    elif command == 'next_screen':
        logger.info('Next screen')
        set_backlight(True)
        await broadcast(json.dumps({
            'type': 'navigate',
            'data': {'page': 'next'}
        }))
        return {'status': 'ok', 'action': 'next_screen'}

    elif command == 'prev_screen':
        logger.info('Previous screen')
        set_backlight(True)
        await broadcast(json.dumps({
            'type': 'navigate',
            'data': {'page': 'previous'}
        }))
        return {'status': 'ok', 'action': 'prev_screen'}

    elif command == 'show_camera':
        title = params.get('title', 'Camera')
        camera_entity = params.get('camera_entity', 'camera.doorbell_medium_resolution_channel')
        camera_id = params.get('camera_id', 'doorbell')
        actions = params.get('actions', {})

        logger.info('Showing camera overlay: %s (%s)', title, camera_entity)
        set_backlight(True)
        await broadcast(json.dumps({
            'type': 'camera_overlay',
            'data': {
                'action': 'show',
                'title': title,
                'camera_entity': camera_entity,
                'camera_id': camera_id,
                'actions': actions
            }
        }))
        return {'status': 'ok', 'command': 'show_camera', 'title': title}

    elif command == 'dismiss_camera':
        logger.info('Dismissing camera overlay')
        await broadcast(json.dumps({
            'type': 'camera_overlay',
            'data': {
                'action': 'hide'
            }
        }))
        return {'status': 'ok', 'command': 'dismiss_camera'}

    elif command == 'add_menu_item':
        preset = params.get('preset')
        logger.info('Adding menu item (preset=%s)', preset)
        msg = {'type': 'menu_item', 'data': {'action': 'add'}}
        if preset:
            msg['data']['preset'] = preset
        else:
            msg['data'].update({
                'title': params.get('title', 'Item'),
                'path': params.get('path', 'menu/item'),
                'after': params.get('after', 'menu/playing')
            })
        await broadcast(json.dumps(msg))
        return {'status': 'ok', 'command': 'add_menu_item'}

    elif command == 'remove_menu_item':
        path = params.get('path')
        preset = params.get('preset')
        logger.info('Removing menu item (path=%s, preset=%s)', path, preset)
        msg = {'type': 'menu_item', 'data': {'action': 'remove'}}
        if path:
            msg['data']['path'] = path
        if preset:
            msg['data']['preset'] = preset
        await broadcast(json.dumps(msg))
        return {'status': 'ok', 'command': 'remove_menu_item'}

    elif command in ('hide_menu_item', 'show_menu_item'):
        path = params.get('path')
        action = 'hide' if command == 'hide_menu_item' else 'show'
        logger.info('%s menu item: %s', action.capitalize(), path)
        await broadcast(json.dumps({
            'type': 'menu_item',
            'data': {'action': action, 'path': path}
        }))
        return {'status': 'ok', 'command': command}

    elif command == 'broadcast':
        # Forward an arbitrary event to all WebSocket clients (used by cd.py etc.)
        evt_type = params.get('type', 'unknown')
        evt_data = params.get('data', {})
        logger.info('Broadcasting event: %s', evt_type)
        await broadcast(json.dumps({'type': evt_type, 'data': evt_data}))
        return {'status': 'ok', 'command': 'broadcast', 'event_type': evt_type}

    else:
        return {'status': 'error', 'message': f'Unknown command: {command}'}


async def handle_webhook(request):
    """Handle incoming webhook requests from Home Assistant (HTTP)."""
    try:
        data = await request.json()
        logger.info('Webhook received: %s', data)

        result = await process_command(data)

        status_code = 400 if result.get('status') == 'error' else 200
        return web.json_response(result, status=status_code)

    except json.JSONDecodeError:
        return web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error('Webhook error: %s', e)
        return web.json_response({'status': 'error', 'message': str(e)}, status=500)


async def handle_mqtt_command(data: dict):
    """Handle incoming commands via MQTT (fire-and-forget, no response needed)."""
    logger.info('MQTT command received: %s', data)
    try:
        await process_command(data)
    except Exception as e:
        logger.error('MQTT command error: %s', e)

async def handle_health(request):
    """Health check endpoint."""
    return web.json_response({'status': 'ok', 'service': 'beo-input', 'screen': 'on' if is_backlight_on() else 'off'})

async def handle_led(request):
    """Quick LED control for visual feedback. GET /led?mode=pulse|on|off|blink"""
    mode = request.query.get('mode', 'pulse')

    if mode == 'pulse':
        # Quick pulse: on then off after 100ms
        set_led('on')
        asyncio.get_event_loop().call_later(0.1, lambda: set_led('off'))
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

    try:
        async with ClientSession() as session:
            headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}
            async with session.get(f'{ha_url}/api/states/media_player.loft_apple_tv', headers=headers) as resp:
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
        async with ClientSession() as session:
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
        remotes = await asyncio.get_event_loop().run_in_executor(None, get_bt_remotes)
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
    # Notify CD service so it can resync menu items / metadata for this new client
    asyncio.create_task(_notify_cd_resync())
    recv_task = asyncio.create_task(receive_commands(ws))
    try:
        await ws.wait_closed()
    finally:
        recv_task.cancel()
        clients.remove(ws)
        await stop_log_stream(ws)  # Clean up any active log streams


async def _notify_cd_resync():
    """Ask beo-cd to re-send its menu item and metadata (if a disc is in)."""
    try:
        from aiohttp import ClientTimeout
        async with ClientSession(timeout=ClientTimeout(total=3)) as session:
            async with session.get('http://localhost:8769/resync') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('resynced'):
                        logger.info('CD resync triggered for new client')
    except Exception as e:
        logger.debug('CD resync skipped (beo-cd not reachable): %s', e)

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

            # Handle media requests by forwarding to media server
            if msg.get('type') == 'media_request':
                await forward_to_media_server(raw)
                continue

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
                logs = get_service_logs(params.get('service', 'beo-input'), params.get('lines', 100))
                await ws.send(json.dumps({'type': 'logs', 'service': params.get('service'), 'logs': logs}))
            elif cmd == 'start_log_stream':
                await start_log_stream(ws, params.get('service', 'beo-masterlink'))
            elif cmd == 'stop_log_stream':
                await stop_log_stream(ws)
            elif cmd == 'get_system_info':
                info = get_system_info()
                await ws.send(json.dumps({'type': 'system_info', **info}))
            elif cmd == 'get_network_status':
                net = await asyncio.get_event_loop().run_in_executor(None, get_network_status)
                await ws.send(json.dumps({'type': 'network_status', **net}))
            elif cmd == 'restart_service':
                restart_service(params.get('action', ''))
            elif cmd == 'refresh_playlists':
                await refresh_spotify_playlists(ws)
            elif cmd == 'get_bt_remotes':
                remotes = await asyncio.get_event_loop().run_in_executor(None, get_bt_remotes)
                await ws.send(json.dumps({'type': 'bt_remotes', 'remotes': remotes}))
            elif cmd == 'start_bt_pairing':
                result = await start_bt_pairing()
                await ws.send(json.dumps({'type': 'bt_pairing', **result}))
        except Exception as e:
            logger.error('WebSocket error: %s', e)

# ——— HID parse & broadcast loop ———

def parse_report(rep: list, loop=None):
    global last_power_press_time, power_button_state
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
                            _output_power('http://localhost:8770/router/output/off'), loop)
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
        async with ClientSession() as s:
            await s.post(url, timeout=aiohttp.ClientTimeout(total=2))
    except Exception:
        pass

_hid_alive = True   # cleared when scan_loop thread dies

def scan_loop(loop):
    global dev, _hid_alive
    devices = hid.enumerate(VID, PID)
    if not devices:
        logger.warning("BS5 not found (no HID device)")
        sys.exit(1)

    dev = hid.device()
    dev.open(VID, PID)
    dev.set_nonblocking(True)
    logger.info("Opened BS5 @ VID:PID=%04x:%04x", VID, PID)

    last_laser = None
    first = True

    try:
        while True:
            rpt = dev.read(64, timeout_ms=50)
            if rpt:
                rep = list(rpt)
                nav_evt, vol_evt, btn_evt, laser_pos = parse_report(rep, loop)

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

            time.sleep(0.001)
    except Exception as e:
        logger.error("HID scan_loop crashed: %s", e)
        _hid_alive = False

# ——— Media server communication ———

async def connect_to_media_server():
    """Connect to the media server and handle bidirectional communication."""
    global media_server_ws
    
    while True:
        try:
            logger.info("Connecting to media server at %s", MEDIA_SERVER_URL)
            media_server_ws = await websockets.connect(MEDIA_SERVER_URL)
            logger.info("Connected to media server")
            
            # Listen for messages from media server
            async for message in media_server_ws:
                try:
                    data = json.loads(message)
                    logger.debug("Received from media server: %s", data.get('type', 'unknown'))
                    
                    # Forward media updates to web clients
                    if data.get('type') == 'media_update':
                        await broadcast(message)
                        
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from media server: %s", message)
                except Exception as e:
                    logger.error("Error processing media server message: %s", e)
                    
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Media server connection closed, reconnecting in 5s...")
            media_server_ws = None
            await asyncio.sleep(5)
        except Exception as e:
            logger.error("Error connecting to media server: %s, retrying in 5s...", e)
            media_server_ws = None
            await asyncio.sleep(5)

async def forward_to_media_server(message):
    """Forward messages from web clients to media server."""
    if media_server_ws and not media_server_ws.closed:
        try:
            await media_server_ws.send(message)
            logger.debug("Forwarded to media server: %s", json.loads(message).get('type', 'unknown'))
        except Exception as e:
            logger.error("Error forwarding to media server: %s", e)
    else:
        logger.warning("Media server not connected, cannot forward message")

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
    app.router.add_post('/forward', handle_forward)
    app.router.add_options('/forward', handle_forward)  # CORS preflight
    app.router.add_get('/appletv', handle_appletv)
    app.router.add_options('/appletv', handle_appletv)  # CORS preflight
    app.router.add_get('/people', handle_people)
    app.router.add_options('/people', handle_people)  # CORS preflight
    app.router.add_get('/health', handle_health)
    app.router.add_get('/led', handle_led)
    app.router.add_get('/bt/remotes', handle_bt_remotes)
    app.router.add_options('/bt/remotes', handle_bt_remotes)  # CORS preflight
    app.router.add_get('/camera/stream', handle_camera_stream)
    app.router.add_get('/camera/snapshot', handle_camera_snapshot)
    runner = web.AppRunner(app)
    await runner.setup()
    http_site = web.TCPSite(runner, '0.0.0.0', 8767)
    await http_site.start()
    logger.info("HTTP webhook server listening on http://0.0.0.0:8767")

    # Start HID scanning thread
    threading.Thread(target=scan_loop, args=(asyncio.get_event_loop(),), daemon=True).start()

    # Start media server connection task
    media_task = asyncio.create_task(connect_to_media_server())

    # Start systemd watchdog heartbeat (stops if HID thread dies)
    async def guarded_watchdog():
        from lib.watchdog import sd_notify
        sd_notify("READY=1")
        logger.info("Watchdog started (guarded by HID thread)")
        while _hid_alive:
            sd_notify("WATCHDOG=1")
            await asyncio.sleep(20)
        logger.error("HID thread dead — stopping watchdog, systemd will restart us")
    asyncio.create_task(guarded_watchdog())

    try:
        # Wait for server to close
        await ws_srv.wait_closed()
    finally:
        await transport.stop()

if __name__ == '__main__':
    asyncio.run(main())
