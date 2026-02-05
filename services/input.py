#!/usr/bin/env python3
import asyncio, threading, json, time, sys
import hid, websockets
import subprocess  # Add subprocess for xset commands
import os  # For path operations
import logging  # For media server communication logging
from aiohttp import web, ClientSession  # For HTTP webhook server and forwarding

VID, PID = 0x0cd4, 0x1112
BTN_MAP = {0x20:'left', 0x10:'right', 0x40:'go', 0x80:'power'}
clients = set()

# Base path for BeoSound 5c installation (from env or default)
BS5C_BASE_PATH = os.getenv('BS5C_BASE_PATH', '/home/pi/beosound5c')

# Media server connection
MEDIA_SERVER_URL = 'ws://localhost:8766'
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
        print("HID write failed:", e)

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
    print(f"[BACKLIGHT] Toggling from {current} to {new_state}")
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
        except:
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
        except:
            info['ip_address'] = '--'

        # Hostname
        try:
            result = subprocess.run(['hostname'], capture_output=True, text=True, timeout=2)
            info['hostname'] = result.stdout.strip() if result.stdout else '--'
        except:
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
        except:
            info['git_tag'] = '--'

        # Service status
        services = ['beo-http', 'beo-ui', 'beo-media', 'beo-input', 'beo-bluetooth', 'beo-masterlink', 'beo-spotify-fetch']
        info['services'] = {}
        for svc in services:
            # For timers, check the timer status
            unit = svc + '.timer' if svc == 'beo-spotify-fetch' else svc
            result = subprocess.run(
                ['systemctl', 'is-active', unit],
                capture_output=True, text=True, timeout=2
            )
            status = result.stdout.strip()
            info['services'][svc] = 'Running' if status == 'active' else status.capitalize()

        # Config from ENV file
        config_file = '/etc/beosound5c/config.env'
        info['config'] = {}
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            # Remove quotes from value
                            value = value.strip().strip('"').strip("'")
                            info['config'][key] = value
        except Exception as e:
            print(f'[CONFIG READ ERROR] {e}')

    except Exception as e:
        print(f'[SYSTEM INFO ERROR] {e}')
    return info

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
        print(f'[LOG STREAM] Started for {service}')

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
                        except:
                            break
                    await asyncio.sleep(0.01)
            except Exception as e:
                print(f'[LOG STREAM ERROR] {e}')

        asyncio.create_task(stream_logs())
    except Exception as e:
        print(f'[LOG STREAM] Failed to start: {e}')

async def stop_log_stream(ws):
    """Stop log streaming for a websocket."""
    global log_stream_processes
    ws_id = id(ws)
    if ws_id in log_stream_processes:
        process = log_stream_processes[ws_id]
        process.terminate()
        del log_stream_processes[ws_id]
        print('[LOG STREAM] Stopped')

def restart_service(action: str):
    """Restart a service or reboot the system."""
    print(f'[RESTART] Executing action: {action}')
    try:
        if action == 'reboot':
            subprocess.Popen(['sudo', 'reboot'])
        elif action == 'restart-all':
            subprocess.Popen(['sudo', 'systemctl', 'restart', 'beo-http', 'beo-ui', 'beo-media', 'beo-input', 'beo-bluetooth', 'beo-masterlink'])
        elif action.startswith('restart-'):
            service = 'beo-' + action.replace('restart-', '')
            subprocess.Popen(['sudo', 'systemctl', 'restart', service])
    except Exception as e:
        print(f'[RESTART ERROR] {e}')

async def refresh_spotify_playlists(ws):
    """Run the Spotify playlist fetch script."""
    print('[SPOTIFY] Starting playlist refresh')
    try:
        # Run fetch_playlists.py in background
        spotify_dir = os.path.join(BS5C_BASE_PATH, 'tools/spotify')
        process = subprocess.Popen(
            ['python3', os.path.join(spotify_dir, 'fetch_playlists.py')],
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
            print('[SPOTIFY] Playlist refresh completed successfully')
            await ws.send(json.dumps({
                'type': 'spotify_refresh',
                'status': 'completed',
                'message': 'Playlists updated successfully'
            }))
        else:
            print(f'[SPOTIFY] Playlist refresh failed: {stderr}')
            await ws.send(json.dumps({
                'type': 'spotify_refresh',
                'status': 'error',
                'message': f'Error: {stderr[:200] if stderr else "Unknown error"}'
            }))

    except subprocess.TimeoutExpired:
        process.kill()
        print('[SPOTIFY] Playlist refresh timed out')
        await ws.send(json.dumps({
            'type': 'spotify_refresh',
            'status': 'error',
            'message': 'Refresh timed out after 2 minutes'
        }))
    except Exception as e:
        print(f'[SPOTIFY ERROR] {e}')
        await ws.send(json.dumps({
            'type': 'spotify_refresh',
            'status': 'error',
            'message': str(e)
        }))

# ——— HTTP Webhook Server ———

async def handle_camera_stream(request):
    """Proxy camera stream from Home Assistant to avoid CORS issues."""
    ha_url = os.getenv('HA_URL', 'http://homeassistant.local:8123')
    ha_token = os.getenv('HA_TOKEN', '')

    # Get camera entity from query params, default to doorbell
    entity = request.query.get('entity', 'camera.doorbell_medium_resolution_channel')

    try:
        async with ClientSession() as session:
            headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}

            # Get camera stream URL from HA
            camera_url = f'{ha_url}/api/camera_proxy_stream/{entity}'
            print(f'[CAMERA] Proxying stream from: {camera_url}')

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
                    print(f'[CAMERA] HA returned status: {resp.status}')
                    return web.json_response(
                        {'error': f'Camera unavailable: HTTP {resp.status}'},
                        status=resp.status,
                        headers={'Access-Control-Allow-Origin': '*'}
                    )
    except Exception as e:
        print(f'[CAMERA ERROR] {e}')
        return web.json_response(
            {'error': str(e)},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'}
        )

async def handle_camera_snapshot(request):
    """Get a single snapshot from camera via Home Assistant."""
    ha_url = os.getenv('HA_URL', 'http://homeassistant.local:8123')
    ha_token = os.getenv('HA_TOKEN', '')

    # Get camera entity from query params, default to doorbell
    entity = request.query.get('entity', 'camera.doorbell_medium_resolution_channel')

    try:
        async with ClientSession() as session:
            headers = {'Authorization': f'Bearer {ha_token}'} if ha_token else {}

            # Get camera snapshot from HA
            camera_url = f'{ha_url}/api/camera_proxy/{entity}'
            print(f'[CAMERA] Getting snapshot from: {camera_url}')

            async with session.get(camera_url, headers=headers) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    return web.Response(
                        body=content,
                        content_type=resp.content_type or 'image/jpeg',
                        headers={'Access-Control-Allow-Origin': '*'}
                    )
                else:
                    print(f'[CAMERA] HA returned status: {resp.status}')
                    return web.json_response(
                        {'error': f'Camera unavailable: HTTP {resp.status}'},
                        status=resp.status,
                        headers={'Access-Control-Allow-Origin': '*'}
                    )
    except Exception as e:
        print(f'[CAMERA ERROR] {e}')
        return web.json_response(
            {'error': str(e)},
            status=500,
            headers={'Access-Control-Allow-Origin': '*'}
        )

async def handle_webhook(request):
    """Handle incoming webhook requests from Home Assistant."""
    try:
        data = await request.json()
        print(f'[WEBHOOK] Received: {data}')

        command = data.get('command', '')
        params = data.get('params', {})

        if command == 'screen_on':
            print('[WEBHOOK] Turning screen ON')
            set_backlight(True)
            return web.json_response({'status': 'ok', 'screen': 'on'})

        elif command == 'screen_off':
            print('[WEBHOOK] Turning screen OFF')
            set_backlight(False)
            return web.json_response({'status': 'ok', 'screen': 'off'})

        elif command == 'screen_toggle':
            print('[WEBHOOK] Toggling screen')
            toggle_backlight()
            return web.json_response({'status': 'ok', 'screen': 'on' if is_backlight_on() else 'off'})

        elif command == 'show_page':
            page = params.get('page', 'now_playing')
            print(f'[WEBHOOK] Showing page: {page}')
            # Broadcast to all WebSocket clients to navigate
            await broadcast(json.dumps({
                'type': 'navigate',
                'data': {'page': page}
            }))
            return web.json_response({'status': 'ok', 'page': page})

        elif command == 'restart':
            target = params.get('target', 'all')
            print(f'[WEBHOOK] Restarting: {target}')
            if target == 'system':
                restart_service('reboot')
            else:
                restart_service('restart-all')
            return web.json_response({'status': 'ok', 'restart': target})

        elif command == 'wake':
            # Combined: turn on screen and show a page
            page = params.get('page', 'now_playing')
            print(f'[WEBHOOK] Waking up and showing: {page}')
            set_backlight(True)
            await broadcast(json.dumps({
                'type': 'navigate',
                'data': {'page': page}
            }))
            return web.json_response({'status': 'ok', 'screen': 'on', 'page': page})

        elif command == 'status':
            info = get_system_info()
            info['screen'] = 'on' if is_backlight_on() else 'off'
            return web.json_response({'status': 'ok', **info})

        elif command == 'next_screen':
            print('[WEBHOOK] Next screen')
            set_backlight(True)
            await broadcast(json.dumps({
                'type': 'navigate',
                'data': {'page': 'next'}
            }))
            return web.json_response({'status': 'ok', 'action': 'next_screen'})

        elif command == 'prev_screen':
            print('[WEBHOOK] Previous screen')
            set_backlight(True)
            await broadcast(json.dumps({
                'type': 'navigate',
                'data': {'page': 'previous'}
            }))
            return web.json_response({'status': 'ok', 'action': 'prev_screen'})

        elif command == 'show_camera':
            # Show camera overlay (e.g., doorbell camera)
            title = params.get('title', 'Camera')
            camera_entity = params.get('camera_entity', 'camera.doorbell_medium_resolution_channel')
            camera_id = params.get('camera_id', 'doorbell')
            actions = params.get('actions', {})  # Optional custom action labels

            print(f'[WEBHOOK] Showing camera overlay: {title} ({camera_entity})')

            # Turn on screen if off
            set_backlight(True)

            # Broadcast camera overlay command to all clients
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
            return web.json_response({'status': 'ok', 'command': 'show_camera', 'title': title})

        elif command == 'dismiss_camera':
            print('[WEBHOOK] Dismissing camera overlay')
            await broadcast(json.dumps({
                'type': 'camera_overlay',
                'data': {
                    'action': 'hide'
                }
            }))
            return web.json_response({'status': 'ok', 'command': 'dismiss_camera'})

        else:
            return web.json_response({'status': 'error', 'message': f'Unknown command: {command}'}, status=400)

    except json.JSONDecodeError:
        return web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    except Exception as e:
        print(f'[WEBHOOK ERROR] {e}')
        return web.json_response({'status': 'error', 'message': str(e)}, status=500)

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
    """Forward webhook to Home Assistant."""
    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    try:
        data = await request.json()
        print(f'[FORWARD] Received webhook to forward: {data}')

        ha_url = os.getenv('HA_WEBHOOK_URL', 'http://homeassistant.local:8123/api/webhook/beosound5c')

        async with ClientSession() as session:
            async with session.post(ha_url, json=data) as resp:
                print(f'[FORWARD] HA response status: {resp.status}')
                response = web.json_response({'status': 'forwarded', 'ha_status': resp.status})
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response

    except json.JSONDecodeError:
        response = web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        print(f'[FORWARD ERROR] {e}')
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

    ha_url = os.getenv('HA_URL', 'http://homeassistant.local:8123')
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
        print(f'[APPLETV ERROR] {e}')
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

    ha_url = os.getenv('HA_URL', 'http://homeassistant.local:8123')
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
        print(f'[PEOPLE ERROR] {e}')
        response = web.json_response({'error': str(e)})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

async def handler(ws, path=None):
    clients.add(ws)
    recv_task = asyncio.create_task(receive_commands(ws))
    try:
        await ws.wait_closed()
    finally:
        recv_task.cancel()
        clients.remove(ws)
        await stop_log_stream(ws)  # Clean up any active log streams

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
            print('[WS RECEIVED]', msg)  # Log every received message

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
            elif cmd == 'restart_service':
                restart_service(params.get('action', ''))
            elif cmd == 'refresh_playlists':
                await refresh_spotify_playlists(ws)
        except Exception as e:
            print(f'[WS ERROR] {e}')

# ——— HID parse & broadcast loop ———

def parse_report(rep: list):
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
            print("[BUTTON] Power button pressed")
    else:
        # Button is released
        if power_button_state == 1:  # Was pressed before
            power_button_state = 0  # Now released
            print("[BUTTON] Power button released")
            
            # Check debounce time
            current_time = time.time()
            if current_time - last_power_press_time > POWER_DEBOUNCE_TIME:
                print("[BUTTON] Power button action triggered")
                toggle_backlight()
                do_click()
                last_power_press_time = current_time
                # Create button event for power button release
                btn_evt = {'button': 'power'}
            else:
                print(f"[BUTTON] Power button debounced (pressed too soon)")

    return nav_evt, vol_evt, btn_evt, laser_pos

def scan_loop(loop):
    global dev
    devices = hid.enumerate(VID, PID)
    if not devices:
        print("BS5 not found (no HID device)")
        sys.exit(1)

    dev = hid.device()
    dev.open(VID, PID)
    dev.set_nonblocking(True)
    print(f"[HID] Opened BS5 @ VID:PID={VID:04x}:{PID:04x}")

    last_laser = None
    first = True

    while True:
        rpt = dev.read(64, timeout_ms=50)
        if rpt:
            rep = list(rpt)
            nav_evt, vol_evt, btn_evt, laser_pos = parse_report(rep)

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

        # Turn screen on when anything happens
        # set_backlight(True)
        time.sleep(0.001)

# ——— Media server communication ———

async def connect_to_media_server():
    """Connect to the media server and handle bidirectional communication."""
    global media_server_ws
    
    while True:
        try:
            print(f"[MEDIA] Connecting to media server at {MEDIA_SERVER_URL}")
            media_server_ws = await websockets.connect(MEDIA_SERVER_URL)
            print("[MEDIA] Connected to media server")
            
            # Listen for messages from media server
            async for message in media_server_ws:
                try:
                    data = json.loads(message)
                    print(f"[MEDIA] Received from media server: {data.get('type', 'unknown')}")
                    
                    # Forward media updates to web clients
                    if data.get('type') == 'media_update':
                        await broadcast(message)
                        
                except json.JSONDecodeError:
                    print(f"[MEDIA] Invalid JSON from media server: {message}")
                except Exception as e:
                    print(f"[MEDIA] Error processing media server message: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            print("[MEDIA] Media server connection closed, reconnecting in 5s...")
            media_server_ws = None
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[MEDIA] Error connecting to media server: {e}, retrying in 5s...")
            media_server_ws = None
            await asyncio.sleep(5)

async def forward_to_media_server(message):
    """Forward messages from web clients to media server."""
    if media_server_ws and not media_server_ws.closed:
        try:
            await media_server_ws.send(message)
            print(f"[MEDIA] Forwarded to media server: {json.loads(message).get('type', 'unknown')}")
        except Exception as e:
            print(f"[MEDIA] Error forwarding to media server: {e}")
    else:
        print("[MEDIA] Media server not connected, cannot forward message")

# ——— Main & server start ———

async def main():
    ws_srv = await websockets.serve(handler, '0.0.0.0', 8765)
    print("WebSocket server listening on ws://0.0.0.0:8765")

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
    app.router.add_get('/camera/stream', handle_camera_stream)
    app.router.add_get('/camera/snapshot', handle_camera_snapshot)
    runner = web.AppRunner(app)
    await runner.setup()
    http_site = web.TCPSite(runner, '0.0.0.0', 8767)
    await http_site.start()
    print("HTTP webhook server listening on http://0.0.0.0:8767")

    # Start HID scanning thread
    threading.Thread(target=scan_loop, args=(asyncio.get_event_loop(),), daemon=True).start()

    # Start media server connection task
    media_task = asyncio.create_task(connect_to_media_server())

    # Wait for server to close
    await ws_srv.wait_closed()

if __name__ == '__main__':
    asyncio.run(main())
