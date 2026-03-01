#!/usr/bin/env python3
"""
BeoSound 5c Plex Source (beo-source-plex)

Provides Plex playback via PIN-based OAuth login.
Plays on the configured player service (Sonos, BlueSound, etc.) via its
HTTP API — uses direct stream URLs via play_uri() (no ShareLink support).
Source-managed track advancement (next/prev play the URL directly).

Port: 8778
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
from auth import PlexAuth
from tokens import load_tokens, save_tokens, delete_tokens

# Shared library (services/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib.config import cfg
from lib.source_base import SourceBase
from lib.digit_playlists import DigitPlaylistMixin

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-source-plex')

# Configuration
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLAYLISTS_FILE = os.path.join(
    os.getenv('BS5C_BASE_PATH', PROJECT_ROOT),
    'web', 'json', 'plex_playlists.json')
DIGIT_PLAYLISTS_FILE = os.path.join(
    os.getenv('BS5C_BASE_PATH', PROJECT_ROOT),
    'web', 'json', 'plex_digit_playlists.json')

POLL_INTERVAL = 3
PLAYLIST_REFRESH_COOLDOWN = 5 * 60
NIGHTLY_REFRESH_HOUR = 4  # offset from TIDAL (3am) and Apple Music (2am)
FETCH_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fetch.py')


def _find_token_file():
    """Find the token file path (same logic as tokens.py)."""
    paths = [
        "/etc/beosound5c/plex_tokens.json",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "plex_tokens.json"),
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    for path in paths:
        d = os.path.dirname(path)
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return path
    return paths[-1]


class PlexService(DigitPlaylistMixin, SourceBase):
    """Main Plex source service with source-managed track advancement."""

    id = "plex"
    name = "PLEX"
    port = 8778
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
        self.auth = PlexAuth()
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
        self._pending_login = None  # (pinlogin,) during OAuth
        # Source-managed track advancement
        self._current_playlist = None  # full playlist dict with tracks
        self._current_index = 0

    async def on_start(self):
        has_creds = self.auth.load()

        if has_creds:
            self._load_playlists()
            self.player = "remote"

            caps = await self.player_capabilities()
            if caps:
                log.info("Player service available - using player API")
            else:
                log.warning("No player service available")
        else:
            log.info("No Plex credentials - waiting for setup via /setup")

        # Always register so PLEX appears in menu
        await self.register("available")

        log.info("Plex source ready (%s)",
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
        self._reload_digit_playlists()

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
        return {
            'state': self.state,
            'now_playing': self.now_playing,
            'playlist_count': len(self.playlists),
            'has_credentials': self.auth.is_configured,
            'user_name': self.auth.user_name,
            'server_name': self.auth.server_name,
            'last_refresh': self._last_refresh_wall.isoformat() if self._last_refresh_wall else None,
            'last_refresh_duration': self._last_refresh_duration,
            'digit_playlists': self._get_digit_names(),
            'fetching': self._fetching_playlists,
            'current_playlist': self._current_playlist.get('name') if self._current_playlist else None,
            'current_index': self._current_index,
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
            await self._play_track_url(url)

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

    # -- Playback control (source-managed track advancement) --

    async def _play_playlist(self, playlist_id, track_index=None):
        """Start playing a playlist. Stores playlist for source-managed advancement."""
        playlist = None
        for pl in self.playlists:
            if pl.get('id') == playlist_id:
                playlist = pl
                break

        if not playlist:
            log.warning("Playlist %s not found", playlist_id)
            return

        tracks = playlist.get('tracks', [])
        if not tracks:
            log.warning("Playlist %s has no tracks", playlist_id)
            return

        index = track_index if track_index is not None else 0
        if index < 0 or index >= len(tracks):
            index = 0

        self._current_playlist = playlist
        self._current_index = index

        await self._play_current_track()

    async def _play_current_track(self):
        """Play the track at _current_index in _current_playlist."""
        if not self._current_playlist:
            return

        tracks = self._current_playlist.get('tracks', [])
        if not tracks or self._current_index >= len(tracks):
            log.info("End of playlist reached")
            await self._stop()
            return

        track = tracks[self._current_index]
        url = track.get('url')

        if not url:
            log.warning("Track %s has no stream URL, skipping",
                        track.get('name', '?'))
            # Try next track
            if self._current_index + 1 < len(tracks):
                self._current_index += 1
                await self._play_current_track()
            return

        # Pre-broadcast metadata for instant artwork
        await self.broadcast("media_update", {
            "title": track.get("name", ""),
            "artist": track.get("artist", ""),
            "artwork": track.get("image", ""),
            "state": "PLAYING",
        })
        log.info("Playing [%d/%d] %s - %s",
                 self._current_index + 1, len(tracks),
                 track.get('artist', '?'), track.get('name', '?'))

        ok = await self.player_play(url=url)
        if ok:
            self.state = "playing"
            self.now_playing = {
                'title': track.get('name'),
                'artist': track.get('artist'),
                'image': track.get('image'),
                'index': self._current_index,
                'total': len(tracks),
            }
            await self.register("playing", auto_power=True)
            self._start_polling()
        else:
            log.error("Player service failed to start track")

    async def _play_track_url(self, url):
        """Play a specific track by its stream URL (standalone, no playlist context)."""
        log.info("Play track URL %s", url)
        ok = await self.player_play(url=url)
        if ok:
            self.state = "playing"
            self._current_playlist = None
            self._current_index = 0
            await self.register("playing", auto_power=True)
            self._start_polling()

    async def _toggle(self):
        if self.state == "playing":
            await self._pause()
        elif self.state == "paused":
            await self._resume()
        elif self.state == "stopped" and self.playlists:
            first = self.playlists[0]
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
        """Source-managed: advance to next track in playlist."""
        if self._current_playlist:
            tracks = self._current_playlist.get('tracks', [])
            if self._current_index + 1 < len(tracks):
                self._current_index += 1
                await self._play_current_track()
            else:
                log.info("Already at last track")
        else:
            # No playlist context — delegate to player
            if await self.player_next():
                await asyncio.sleep(0.5)
                await self._poll_now_playing()

    async def _prev(self):
        """Source-managed: go to previous track in playlist."""
        if self._current_playlist:
            if self._current_index > 0:
                self._current_index -= 1
                await self._play_current_track()
            else:
                log.info("Already at first track")
        else:
            # No playlist context — delegate to player
            if await self.player_prev():
                await asyncio.sleep(0.5)
                await self._poll_now_playing()

    async def _stop(self):
        await self.player_stop()
        self.state = "stopped"
        self.now_playing = None
        self._current_playlist = None
        self._current_index = 0
        self._stop_polling()
        await self.register("available")

    async def _refresh_playlists(self):
        """Re-fetch playlists by running fetch.py."""
        self._fetching_playlists = True
        t0 = time.monotonic()
        try:
            token_file = _find_token_file()
            if not os.path.exists(token_file):
                log.error("Cannot refresh playlists - no token file")
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
        """Clear Plex tokens and playlists."""
        log.info("Logging out of Plex")

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
        self._current_playlist = None
        self._current_index = 0

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
        log.info("Plex logged out - ready for new setup")

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
        """Poll player state. Auto-advance to next track when current finishes."""
        try:
            state = await self.player_state()
            if state == "playing" and self.state != "playing":
                self.state = "playing"
                await self.register("playing")
            elif state == "stopped" and self.state == "playing":
                # Track finished — auto-advance if we have a playlist
                if self._current_playlist:
                    tracks = self._current_playlist.get('tracks', [])
                    if self._current_index + 1 < len(tracks):
                        log.info("Track finished, advancing to next")
                        self._current_index += 1
                        await self._play_current_track()
                        return
                    else:
                        log.info("Playlist finished")
                self.state = "stopped"
                self.now_playing = None
                await self.register("available")
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
        if self._fetching_playlists and not self.playlists:
            return web.json_response({
                'loading': True,
            }, headers=self._cors_headers())

        if self._should_refresh() and not self._fetching_playlists:
            self._fetching_playlists = True
            log.info("Playlist view opened - refreshing in background")
            asyncio.create_task(self._refresh_playlists())

        return web.json_response(
            self.playlists,
            headers=self._cors_headers())

    async def _handle_setup(self, request):
        """Serve the Plex setup info page."""
        html = '''<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BeoSound 5c - Plex Setup</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Helvetica Neue',-apple-system,sans-serif;background:#000;color:#fff;padding:20px;line-height:1.7}
.container{max-width:500px;margin:0 auto}
.header{text-align:center;margin-bottom:30px;padding-bottom:20px;border-bottom:1px solid #333}
h1{font-size:24px;font-weight:300;letter-spacing:2px;margin-bottom:8px}
.subtitle{color:#666;font-size:14px}
.step{background:#111;border-radius:8px;padding:20px;margin-bottom:16px;border:1px solid #222}
.step-number{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border:2px solid #E5A00D;color:#E5A00D;border-radius:50%;font-weight:600;font-size:14px;margin-right:12px}
.step-title{font-size:16px;font-weight:500;margin-bottom:12px;display:flex;align-items:center}
.step-content{color:#999;font-size:14px;margin-left:40px}
.step-content p{margin-bottom:8px}
.submit-btn{display:block;width:100%;padding:14px;margin-top:20px;background:#E5A00D;border:none;border-radius:4px;color:#000;font-size:16px;font-weight:600;cursor:pointer;text-align:center;text-decoration:none}
.submit-btn:hover{background:#d4920c}
.submit-btn:disabled{background:#333;color:#666;cursor:not-allowed}
.note{background:#0a0a0a;border:1px solid #222;border-radius:4px;padding:12px;margin:12px 0;font-size:13px;color:#666}
.status{text-align:center;margin-top:20px;color:#666;font-size:14px;display:none}
.status.show{display:block}
.login-url{font-family:'SF Mono',Monaco,Consolas,monospace;font-size:15px;color:#fff;word-break:break-all;text-align:center;padding:16px;background:#111;border:1px solid #333;border-radius:4px;margin:12px 0;display:none}
.ok-container{display:none;text-align:center;margin-top:30px}
.ok{width:80px;height:80px;border:3px solid #E5A00D;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 20px;font-size:36px;color:#E5A00D}
</style></head><body>
<div class="container">
<div class="header"><h1>PLEX SETUP</h1><div class="subtitle">BeoSound 5c</div></div>
<div id="setup-form">
<div class="step">
    <div class="step-title"><span class="step-number">1</span>Connect your Plex account</div>
    <div class="step-content">
        <p>Start the login process below, then open the link on your phone to authorize.</p>
        <button id="start-btn" class="submit-btn">Start Plex Login</button>
        <div id="login-url" class="login-url"></div>
        <div id="status" class="status"></div>
    </div>
</div>
</div>
<div id="ok-container" class="ok-container">
    <div class="ok">&#10003;</div>
    <h1 style="font-size:24px;font-weight:300;margin-bottom:20px;letter-spacing:1px">Connected to Plex</h1>
    <p style="color:#999">Playlists are loading now.<br>You can close this page.</p>
    <p class="note" style="margin-top:30px">The BeoSound 5c screen will update automatically.</p>
</div>
</div>

<script>
const BASE_URL = window.location.origin;
const startBtn = document.getElementById('start-btn');
const statusEl = document.getElementById('status');
const loginUrlEl = document.getElementById('login-url');

startBtn.addEventListener('click', async () => {
    startBtn.disabled = true;
    statusEl.textContent = 'Starting login...';
    statusEl.classList.add('show');

    try {
        const resp = await fetch(BASE_URL + '/start-login', { method: 'POST' });
        const data = await resp.json();
        if (data.error) {
            statusEl.textContent = 'Error: ' + data.error;
            startBtn.disabled = false;
            return;
        }

        loginUrlEl.innerHTML = '<a href="' + data.url + '" target="_blank" style="color:#E5A00D">' + data.url + '</a>';
        loginUrlEl.style.display = 'block';
        statusEl.textContent = 'Open the link above on your phone and log in. Waiting...';

        // Poll for completion
        const pollInterval = setInterval(async () => {
            try {
                const check = await fetch(BASE_URL + '/check-login', { method: 'POST' });
                const result = await check.json();
                if (result.status === 'ok') {
                    clearInterval(pollInterval);
                    document.getElementById('setup-form').style.display = 'none';
                    document.getElementById('ok-container').style.display = 'block';
                } else if (result.status === 'expired') {
                    clearInterval(pollInterval);
                    statusEl.textContent = 'Login expired. Please try again.';
                    startBtn.disabled = false;
                    loginUrlEl.style.display = 'none';
                }
            } catch (e) { /* keep polling */ }
        }, 3000);
    } catch (e) {
        statusEl.textContent = 'Error: ' + e.message;
        startBtn.disabled = false;
    }
});
</script>
</body></html>'''
        return web.Response(text=html, content_type='text/html')

    async def _handle_start_login(self, request):
        """Start the Plex PIN-based OAuth flow."""
        try:
            loop = asyncio.get_running_loop()
            pinlogin, oauth_url = await loop.run_in_executor(
                None, self.auth.start_oauth)
            self._pending_login = pinlogin

            log.info("Plex OAuth started - URL: %s", oauth_url)
            return web.json_response({
                'url': oauth_url,
            }, headers=self._cors_headers())
        except Exception as e:
            log.error("Failed to start Plex login: %s", e)
            return web.json_response(
                {'error': str(e)},
                status=500, headers=self._cors_headers())

    async def _handle_check_login(self, request):
        """Check if the Plex OAuth login has completed."""
        if not self._pending_login:
            return web.json_response(
                {'status': 'no_pending'},
                headers=self._cors_headers())

        pinlogin = self._pending_login

        try:
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(
                None, self.auth.check_login, pinlogin)
        except Exception as e:
            self._pending_login = None
            log.error("Plex login check failed: %s", e)
            return web.json_response(
                {'status': 'expired', 'error': str(e)},
                headers=self._cors_headers())

        if success:
            self._pending_login = None
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

            log.info("Plex login successful")
            return web.json_response(
                {'status': 'ok'},
                headers=self._cors_headers())
        else:
            # Still pending — checkLogin returns False if not yet authorized
            return web.json_response(
                {'status': 'pending'},
                headers=self._cors_headers())

    async def _handle_logout(self, request):
        """HTTP endpoint for logout - called from system.html."""
        await self._logout()
        return web.json_response(
            {'status': 'ok', 'message': 'Logged out'},
            headers=self._cors_headers())


if __name__ == '__main__':
    service = PlexService()
    asyncio.run(service.run())
