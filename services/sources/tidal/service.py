#!/usr/bin/env python3
"""
BeoSound 5c TIDAL Source (beo-source-tidal)

Provides TIDAL playback via tidalapi OAuth device login.
Plays on the configured player service (Sonos, BlueSound, etc.) via its
HTTP API — SoCo ShareLink natively supports TIDAL URLs.

Port: 8777
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

from aiohttp import web

# Sibling imports (this directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auth import TidalAuth
from tokens import load_tokens, save_tokens, delete_tokens

# Shared library (services/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib.config import cfg
from lib.source_base import SourceBase

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-source-tidal')

# Configuration
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLAYLISTS_FILE = os.path.join(
    os.getenv('BS5C_BASE_PATH', PROJECT_ROOT),
    'web', 'json', 'tidal_playlists.json')
DIGIT_PLAYLISTS_FILE = os.path.join(
    os.getenv('BS5C_BASE_PATH', PROJECT_ROOT),
    'web', 'json', 'tidal_digit_playlists.json')

POLL_INTERVAL = 3
PLAYLIST_REFRESH_COOLDOWN = 5 * 60
NIGHTLY_REFRESH_HOUR = 3  # slightly offset from Apple Music (2am)
FETCH_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fetch.py')


def _find_token_file():
    """Find the token file path (same logic as tokens.py)."""
    paths = [
        "/etc/beosound5c/tidal_tokens.json",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "tidal_tokens.json"),
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    for path in paths:
        d = os.path.dirname(path)
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return path
    return paths[-1]


class TidalService(SourceBase):
    """Main TIDAL source service."""

    id = "tidal"
    name = "TIDAL"
    port = 8777
    action_map = {
        "play": "toggle",
        "pause": "toggle",
        "go": "toggle",
        "next": "next",
        "prev": "prev",
        "right": "next",
        "left": "prev",
        "up": "next",
        "down": "prev",
        "stop": "stop",
        "0": "digit", "1": "digit", "2": "digit",
        "3": "digit", "4": "digit", "5": "digit",
        "6": "digit", "7": "digit", "8": "digit",
        "9": "digit",
    }

    def __init__(self):
        super().__init__()
        self.auth = TidalAuth()
        self.playlists = []
        self.state = "stopped"
        self.now_playing = None
        self._poll_task = None
        self._refresh_task = None
        self._nightly_task = None
        self._fetching_playlists = False
        self._last_refresh = 0
        self._last_refresh_wall = None
        self._last_refresh_duration = None
        self._pending_login = None  # (session, future) during device login

    async def on_start(self):
        has_creds = self.auth.load()

        if has_creds:
            self._load_playlists()
            self.player = "remote"

            caps = await self.player_capabilities()
            if caps:
                log.info("Player service available — using player API")
            else:
                log.warning("No player service available")
        else:
            log.info("No TIDAL credentials — waiting for setup via /setup")

        # Always register so TIDAL appears in menu
        await self.register("available")

        log.info("TIDAL source ready (%s)",
                 "player service" if has_creds else "awaiting setup")

        if self.auth.is_configured:
            self._refresh_task = asyncio.create_task(
                self._delayed_refresh(delay=10))
            self._nightly_task = asyncio.create_task(
                self._nightly_refresh_loop())

    async def on_stop(self):
        for task in (self._poll_task, self._refresh_task, self._nightly_task):
            if task:
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
        await self.register("gone")

    def _load_playlists(self):
        """Load playlists from the pre-fetched JSON file."""
        try:
            with open(PLAYLISTS_FILE) as f:
                self.playlists = json.load(f)
            log.info("Loaded %d playlists from disk", len(self.playlists))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            log.warning("Could not load playlists: %s", e)
            self.playlists = []

    # -- SourceBase hooks --

    def add_routes(self, app):
        app.router.add_get('/playlists', self._handle_playlists)
        app.router.add_get('/setup', self._handle_setup)
        app.router.add_post('/start-login', self._handle_start_login)
        app.router.add_options('/start-login', self._handle_cors)
        app.router.add_post('/check-login', self._handle_check_login)
        app.router.add_options('/check-login', self._handle_cors)
        app.router.add_post('/logout', self._handle_logout)
        app.router.add_options('/logout', self._handle_cors)

    async def handle_status(self) -> dict:
        digit_playlists = {}
        try:
            with open(DIGIT_PLAYLISTS_FILE) as f:
                raw = json.load(f)
            for d, info in raw.items():
                if info and info.get('name'):
                    digit_playlists[d] = info['name']
        except Exception:
            pass

        return {
            'state': self.state,
            'now_playing': self.now_playing,
            'playlist_count': len(self.playlists),
            'has_credentials': self.auth.is_configured,
            'needs_reauth': self.auth.revoked,
            'user_name': self.auth.user_name,
            'last_refresh': self._last_refresh_wall.isoformat() if self._last_refresh_wall else None,
            'last_refresh_duration': self._last_refresh_duration,
            'digit_playlists': digit_playlists,
            'fetching': self._fetching_playlists,
        }

    async def handle_resync(self) -> dict:
        if self.auth.is_configured:
            state = self.state if self.state in ('playing', 'paused') else 'available'
            await self.register(state)
            return {'status': 'ok', 'resynced': True}
        return {'status': 'ok', 'resynced': False}

    async def handle_command(self, cmd, data) -> dict:
        if cmd == 'digit':
            digit = data.get('action', '0')
            playlist = self._get_digit_playlist(digit)
            if playlist:
                log.info("Digit %s -> playlist %s", digit, playlist.get('id'))
                await self._play_playlist(playlist['id'])
            else:
                log.info("No playlist mapped to digit %s", digit)

        elif cmd == 'play_playlist':
            playlist_id = data.get('playlist_id', '')
            track_index = data.get('track_index')
            await self._play_playlist(playlist_id, track_index)

        elif cmd == 'play_track':
            url = data.get('url', '')
            await self._play_track(url)

        elif cmd == 'toggle':
            await self._toggle()

        elif cmd == 'play':
            await self._resume()

        elif cmd == 'pause':
            await self._pause()

        elif cmd == 'next':
            await self._next()

        elif cmd == 'prev':
            await self._prev()

        elif cmd == 'stop':
            await self._stop()

        elif cmd == 'refresh_playlists':
            await self._refresh_playlists()

        elif cmd == 'logout':
            await self._logout()

        else:
            return {'status': 'error', 'message': f'Unknown: {cmd}'}

        return {'state': self.state}

    # -- Digit playlist lookup --

    def _get_digit_playlist(self, digit):
        """Look up a digit playlist from the TIDAL digit mapping file."""
        try:
            with open(DIGIT_PLAYLISTS_FILE) as f:
                mapping = json.load(f)
            info = mapping.get(str(digit))
            if info and info.get('id'):
                return info
        except Exception:
            pass
        return None

    # -- Playback control --

    async def _play_playlist(self, playlist_id, track_index=None):
        """Start playing a playlist via its TIDAL URL."""
        url = None
        track_meta = None
        for pl in self.playlists:
            if pl.get('id') == playlist_id:
                url = pl.get('url')
                if track_index is not None:
                    tracks = pl.get('tracks', [])
                    if 0 <= track_index < len(tracks):
                        track_meta = tracks[track_index]
                break

        if not url:
            if track_meta and track_meta.get('url'):
                log.info("No playlist URL — falling back to track URL")
                return await self._play_track(track_meta['url'])
            for pl in self.playlists:
                if pl.get('id') == playlist_id:
                    tracks = pl.get('tracks', [])
                    if tracks and tracks[0].get('url'):
                        log.info("No playlist URL — playing first track")
                        return await self._play_track(tracks[0]['url'])
                    break
            log.warning("No URL for playlist %s — cannot play", playlist_id)
            return

        # Pre-broadcast selected track metadata for instant artwork
        if track_meta:
            await self.broadcast("media_update", {
                "title": track_meta.get("name", ""),
                "artist": track_meta.get("artist", ""),
                "artwork": track_meta.get("image", ""),
                "state": "PLAYING",
            })
            log.info("Pre-broadcast metadata for %s", track_meta.get("name", "?"))

        # Play individual track when a specific song is selected
        if track_meta and track_meta.get('url'):
            return await self._play_track(track_meta['url'])

        log.info("Play playlist %s (track_index=%s)", playlist_id, track_index)
        ok = await self.player_play(uri=url)
        if ok:
            self.state = "playing"
            await self.register("playing", auto_power=True)
            self._start_polling()
        else:
            log.error("Player service failed to start playlist")

    async def _play_track(self, url):
        """Play a specific track by TIDAL URL."""
        log.info("Play track %s", url)
        ok = await self.player_play(uri=url)
        if ok:
            self.state = "playing"
            await self.register("playing", auto_power=True)
            self._start_polling()

    async def _toggle(self):
        if self.state == "playing":
            await self._pause()
        elif self.state == "paused":
            await self._resume()
        elif self.state == "stopped" and self.playlists:
            first = self.playlists[0]
            if first.get('url'):
                await self._play_playlist(first['id'])

    async def _resume(self):
        if await self.player_resume():
            self.state = "playing"
            await self.register("playing", auto_power=True)
            self._start_polling()

    async def _pause(self):
        if await self.player_pause():
            self.state = "paused"
            await self.register("paused")

    async def _next(self):
        if await self.player_next():
            await asyncio.sleep(0.5)
            await self._poll_now_playing()

    async def _prev(self):
        if await self.player_prev():
            await asyncio.sleep(0.5)
            await self._poll_now_playing()

    async def _stop(self):
        await self.player_stop()
        self.state = "stopped"
        self._stop_polling()
        await self.register("available")

    async def _refresh_playlists(self):
        """Re-fetch playlists by running fetch.py."""
        if self.auth.revoked:
            return
        self._fetching_playlists = True
        t0 = time.monotonic()
        try:
            token_file = _find_token_file()
            if not os.path.exists(token_file):
                log.error("Cannot refresh playlists — no token file")
                return

            log.info("Starting playlist refresh via fetch.py")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, FETCH_SCRIPT,
                '--output', PLAYLISTS_FILE,
                '--token-file', token_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode == 0:
                self._load_playlists()
                self._last_refresh = time.monotonic()
                self._last_refresh_wall = datetime.now()
                self._last_refresh_duration = round(time.monotonic() - t0, 1)
                log.info("Playlist refresh complete (%d playlists, %.1fs)",
                         len(self.playlists), self._last_refresh_duration)
            elif proc.returncode == 1:
                err_msg = (stdout.decode() + stderr.decode())[-500:]
                if 'invalid' in err_msg.lower() or 'expired' in err_msg.lower():
                    self.auth.revoked = True
                    log.error("TIDAL session expired — re-authentication required")
                else:
                    log.error("fetch.py failed (rc=%d): %s", proc.returncode, err_msg)
            else:
                err_msg = (stdout.decode() + stderr.decode())[-500:]
                log.error("fetch.py failed (rc=%d): %s", proc.returncode, err_msg)
        except asyncio.TimeoutError:
            log.error("Playlist refresh timed out")
        except Exception as e:
            log.error("Playlist refresh failed: %s", e)
        finally:
            self._fetching_playlists = False

    async def _delayed_refresh(self, delay):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._refresh_playlists()
        except asyncio.CancelledError:
            return

    async def _nightly_refresh_loop(self):
        try:
            while True:
                now = datetime.now()
                target = now.replace(hour=NIGHTLY_REFRESH_HOUR, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                delay = (target - now).total_seconds()
                log.info("Next nightly playlist refresh at %s (in %.0fh)",
                         target.strftime('%H:%M'), delay / 3600)
                await asyncio.sleep(delay)
                log.info("Nightly playlist refresh starting")
                await self._refresh_playlists()
        except asyncio.CancelledError:
            return

    def _should_refresh(self):
        return time.monotonic() - self._last_refresh > PLAYLIST_REFRESH_COOLDOWN

    async def _logout(self):
        """Clear TIDAL tokens and playlists."""
        log.info("Logging out of TIDAL")

        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None
        if self._nightly_task:
            self._nightly_task.cancel()
            self._nightly_task = None
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        self.auth.clear()
        self.playlists = []
        self.state = "stopped"
        self.now_playing = None
        self._fetching_playlists = False
        self._pending_login = None

        try:
            path = delete_tokens()
            if path:
                log.info("Deleted token file: %s", path)
        except Exception as e:
            log.warning("Could not delete token file: %s", e)

        try:
            if os.path.exists(PLAYLISTS_FILE):
                os.unlink(PLAYLISTS_FILE)
                log.info("Deleted playlist file: %s", PLAYLISTS_FILE)
        except Exception as e:
            log.warning("Could not delete playlist file: %s", e)

        await self.register("available")
        log.info("TIDAL logged out — ready for new setup")

    # -- Now-playing polling --

    def _start_polling(self):
        if self._poll_task and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._poll_loop())

    def _stop_polling(self):
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self):
        try:
            while self.state in ("playing", "paused"):
                await self._poll_now_playing()
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            return

    async def _poll_now_playing(self):
        try:
            state = await self.player_state()
            if state == "playing" and self.state != "playing":
                self.state = "playing"
                await self.register("playing")
            elif state != "playing" and self.state == "playing":
                self.state = "paused"
                await self.register("paused")
        except Exception as e:
            log.warning("Player state poll error: %s", e)

    # -- Extra routes --

    async def _handle_playlists(self, request):
        if not self.auth.is_configured:
            return web.json_response({
                'setup_needed': True,
                'setup_url': f'http://localhost:{self.port}/setup',
            }, headers=self._cors_headers())
        if self.auth.revoked:
            return web.json_response({
                'needs_reauth': True,
                'setup_url': f'http://localhost:{self.port}/setup',
            }, headers=self._cors_headers())
        if self._fetching_playlists and not self.playlists:
            return web.json_response({
                'loading': True,
            }, headers=self._cors_headers())

        if self._should_refresh() and not self._fetching_playlists:
            self._fetching_playlists = True
            log.info("Playlist view opened — refreshing in background")
            asyncio.create_task(self._refresh_playlists())

        return web.json_response(
            self.playlists,
            headers=self._cors_headers())

    async def _handle_setup(self, request):
        """Serve the TIDAL setup info page."""
        is_reconnect = self.auth.revoked
        title = "RECONNECT TIDAL" if is_reconnect else "TIDAL SETUP"
        heading = "Reconnect your TIDAL account" if is_reconnect else "Connect your TIDAL account"
        desc = ("Your TIDAL session has expired. Start a new login below."
                if is_reconnect else
                "Start the login process below, then open the link on your phone to authorize.")

        html = f'''<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BeoSound 5c - TIDAL Setup</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Helvetica Neue',-apple-system,sans-serif;background:#000;color:#fff;padding:20px;line-height:1.7}}
.container{{max-width:500px;margin:0 auto}}
.header{{text-align:center;margin-bottom:30px;padding-bottom:20px;border-bottom:1px solid #333}}
h1{{font-size:24px;font-weight:300;letter-spacing:2px;margin-bottom:8px}}
.subtitle{{color:#666;font-size:14px}}
.step{{background:#111;border-radius:8px;padding:20px;margin-bottom:16px;border:1px solid #222}}
.step-number{{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border:2px solid #fff;color:#fff;border-radius:50%;font-weight:600;font-size:14px;margin-right:12px}}
.step-title{{font-size:16px;font-weight:500;margin-bottom:12px;display:flex;align-items:center}}
.step-content{{color:#999;font-size:14px;margin-left:40px}}
.step-content p{{margin-bottom:8px}}
.submit-btn{{display:block;width:100%;padding:14px;margin-top:20px;background:#fff;border:none;border-radius:4px;color:#000;font-size:16px;font-weight:600;cursor:pointer;text-align:center;text-decoration:none}}
.submit-btn:hover{{background:#ddd}}
.submit-btn:disabled{{background:#333;color:#666;cursor:not-allowed}}
.note{{background:#0a0a0a;border:1px solid #222;border-radius:4px;padding:12px;margin:12px 0;font-size:13px;color:#666}}
.status{{text-align:center;margin-top:20px;color:#666;font-size:14px;display:none}}
.status.show{{display:block}}
.login-url{{font-family:'SF Mono',Monaco,Consolas,monospace;font-size:15px;color:#fff;word-break:break-all;text-align:center;padding:16px;background:#111;border:1px solid #333;border-radius:4px;margin:12px 0;display:none}}
.ok-container{{display:none;text-align:center;margin-top:30px}}
.ok{{width:80px;height:80px;border:3px solid #fff;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 20px;font-size:36px;color:#fff}}
</style></head><body>
<div class="container">
<div class="header"><h1>{title}</h1><div class="subtitle">BeoSound 5c</div></div>
<div id="setup-form">
<div class="step">
    <div class="step-title"><span class="step-number">1</span>{heading}</div>
    <div class="step-content">
        <p>{desc}</p>
        <button id="start-btn" class="submit-btn">Start TIDAL Login</button>
        <div id="login-url" class="login-url"></div>
        <div id="status" class="status"></div>
    </div>
</div>
</div>
<div id="ok-container" class="ok-container">
    <div class="ok">&#10003;</div>
    <h1 style="font-size:24px;font-weight:300;margin-bottom:20px;letter-spacing:1px">Connected to TIDAL</h1>
    <p style="color:#999">Playlists are loading now.<br>You can close this page.</p>
    <p class="note" style="margin-top:30px">The BeoSound 5c screen will update automatically.</p>
</div>
</div>

<script>
const BASE_URL = window.location.origin;
const startBtn = document.getElementById('start-btn');
const statusEl = document.getElementById('status');
const loginUrlEl = document.getElementById('login-url');

startBtn.addEventListener('click', async () => {{
    startBtn.disabled = true;
    statusEl.textContent = 'Starting login...';
    statusEl.classList.add('show');

    try {{
        const resp = await fetch(BASE_URL + '/start-login', {{ method: 'POST' }});
        const data = await resp.json();
        if (data.error) {{
            statusEl.textContent = 'Error: ' + data.error;
            startBtn.disabled = false;
            return;
        }}

        loginUrlEl.innerHTML = '<a href="' + data.url + '" target="_blank" style="color:#fff">' + data.url + '</a>';
        loginUrlEl.style.display = 'block';
        statusEl.textContent = 'Open the link above on your phone and log in. Waiting...';

        // Poll for completion
        const pollInterval = setInterval(async () => {{
            try {{
                const check = await fetch(BASE_URL + '/check-login', {{ method: 'POST' }});
                const result = await check.json();
                if (result.status === 'ok') {{
                    clearInterval(pollInterval);
                    document.getElementById('setup-form').style.display = 'none';
                    document.getElementById('ok-container').style.display = 'block';
                }} else if (result.status === 'expired') {{
                    clearInterval(pollInterval);
                    statusEl.textContent = 'Login expired. Please try again.';
                    startBtn.disabled = false;
                    loginUrlEl.style.display = 'none';
                }}
            }} catch (e) {{ /* keep polling */ }}
        }}, 3000);
    }} catch (e) {{
        statusEl.textContent = 'Error: ' + e.message;
        startBtn.disabled = false;
    }}
}});
</script>
</body></html>'''
        return web.Response(text=html, content_type='text/html')

    async def _handle_start_login(self, request):
        """Start the TIDAL device login flow."""
        try:
            loop = asyncio.get_running_loop()
            session, login, future = await loop.run_in_executor(
                None, self.auth.start_device_login)
            self._pending_login = (session, future)

            url = login.verification_uri_complete

            log.info("TIDAL device login started — URL: %s", url)
            return web.json_response({
                'url': url,
                'user_code': getattr(login, 'user_code', ''),
                'expires_in': getattr(login, 'expires_in', 300),
            }, headers=self._cors_headers())
        except Exception as e:
            log.error("Failed to start TIDAL login: %s", e)
            return web.json_response(
                {'error': str(e)},
                status=500, headers=self._cors_headers())

    async def _handle_check_login(self, request):
        """Check if the TIDAL device login has completed."""
        if not self._pending_login:
            return web.json_response(
                {'status': 'no_pending'},
                headers=self._cors_headers())

        session, future = self._pending_login

        if not future.done():
            return web.json_response(
                {'status': 'pending'},
                headers=self._cors_headers())

        try:
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(
                None, self.auth.complete_login, session, future)
        except Exception as e:
            self._pending_login = None
            log.error("TIDAL login check failed: %s", e)
            return web.json_response(
                {'status': 'expired', 'error': str(e)},
                headers=self._cors_headers())

        self._pending_login = None

        if success:
            self.player = "remote"
            await self.register("available")

            # Kick off playlist refresh
            self._fetching_playlists = True
            if self._refresh_task:
                self._refresh_task.cancel()
            self._refresh_task = asyncio.create_task(
                self._delayed_refresh(delay=0))

            if not self._nightly_task or self._nightly_task.done():
                self._nightly_task = asyncio.create_task(
                    self._nightly_refresh_loop())

            log.info("TIDAL login successful")
            return web.json_response(
                {'status': 'ok'},
                headers=self._cors_headers())
        else:
            return web.json_response(
                {'status': 'expired'},
                headers=self._cors_headers())

    async def _handle_logout(self, request):
        """HTTP endpoint for logout — called from system.html."""
        await self._logout()
        return web.json_response(
            {'status': 'ok', 'message': 'Logged out'},
            headers=self._cors_headers())


if __name__ == '__main__':
    service = TidalService()
    asyncio.run(service.run())
