#!/usr/bin/env python3
"""
BeoSound 5c Spotify Source (beo-spotify)

Provides Spotify playback via the Web API with PKCE authentication.
Plays on the configured player service (Sonos, BlueSound, etc.) via its
HTTP API.

Port: 8771
"""

import asyncio
import json
import logging
import os
import ssl
import sys
import time
from datetime import datetime, timedelta

from aiohttp import web

# Sibling imports (this directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auth import SpotifyAuth
from tokens import load_tokens, save_tokens, delete_tokens
from pkce import (
    generate_code_verifier,
    generate_code_challenge,
    build_auth_url,
    exchange_code,
)

# Shared library (services/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib.config import cfg
from lib.source_base import SourceBase
from playlist_lookup import get_playlist_uri, DIGIT_PLAYLISTS_FILE

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-spotify')

# Configuration
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLAYLISTS_FILE = os.path.join(
    os.getenv('BS5C_BASE_PATH', PROJECT_ROOT),
    'web', 'json', 'spotify_playlists.json')

POLL_INTERVAL = 3  # seconds between now-playing polls
PLAYLIST_REFRESH_COOLDOWN = 5 * 60  # don't re-sync if last sync was <5 min ago
NIGHTLY_REFRESH_HOUR = 2  # refresh playlists at 2am
FETCH_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fetch.py')

# OAuth setup
SPOTIFY_SCOPES = ('playlist-read-private playlist-read-collaborative '
                  'user-read-playback-state user-modify-playback-state '
                  'user-read-currently-playing streaming')
SSL_PORT = 8772
SSL_CERT = os.path.join(os.getenv('BS5C_CONFIG_DIR', '/etc/beosound5c'), 'ssl', 'cert.pem')
SSL_KEY = os.path.join(os.getenv('BS5C_CONFIG_DIR', '/etc/beosound5c'), 'ssl', 'key.pem')


def _get_local_ip():
    """Get the local IP address (for OAuth redirect URI)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


class SpotifyService(SourceBase):
    """Main Spotify source service."""

    id = "spotify"
    name = "Spotify"
    port = 8771
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
        self.auth = SpotifyAuth()
        self.playlists = []
        self.state = "stopped"  # stopped | playing | paused
        self.now_playing = None  # current track metadata
        self._poll_task = None
        self._refresh_task = None
        self._nightly_task = None
        self._pkce_state = {}  # Single dict, not per-session — fine for single-user device
        self._fetching_playlists = False  # True while initial fetch is running
        self._last_refresh = 0  # monotonic timestamp of last completed refresh
        self._last_refresh_wall = None  # wall-clock datetime of last completed refresh
        self._last_refresh_duration = None  # seconds the last refresh took
        self._display_name = None  # Spotify display name from /v1/me

    async def on_start(self):
        # Load credentials (may fail — setup flow will handle it)
        has_creds = self.auth.load()

        if has_creds:
            self._load_playlists()
            self.player = "remote"

            # Fetch display name from Spotify profile
            asyncio.create_task(self._fetch_display_name())

            # Check if a player service is available (Sonos, BlueSound, etc.)
            caps = await self.player_capabilities()
            if "spotify" in caps:
                log.info("Player service available with Spotify support — using player API")
            else:
                log.warning("No player service available — local playback will be "
                            "supported via go-librespot in a future update")
        else:
            log.info("No Spotify credentials — waiting for setup via /setup")

        # Always register so SPOTIFY appears in menu (even without creds)
        await self.register("available")

        # Start HTTPS site for OAuth callback (Spotify requires HTTPS for non-localhost)
        if os.path.isfile(SSL_CERT) and os.path.isfile(SSL_KEY):
            try:
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_ctx.load_cert_chain(SSL_CERT, SSL_KEY)
                ssl_site = web.TCPSite(self._runner, "0.0.0.0", SSL_PORT, ssl_context=ssl_ctx)
                await ssl_site.start()
                log.info("HTTPS API on port %d (for OAuth callback)", SSL_PORT)
            except Exception as e:
                log.warning("Could not start HTTPS site: %s", e)
        else:
            log.info("No SSL cert found — HTTPS callback not available")

        log.info("Spotify source ready (%s)",
                 "player service" if has_creds else "awaiting setup")

        # Initial playlist sync + nightly refresh at 2am
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

    async def _fetch_display_name(self):
        """Fetch Spotify display name from /v1/me."""
        try:
            token = await self.auth.get_token()
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    'https://api.spotify.com/v1/me',
                    headers={'Authorization': f'Bearer {token}'},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._display_name = data.get('display_name') or data.get('id')
                        log.info("Spotify display name: %s", self._display_name)
        except Exception as e:
            log.warning("Could not fetch Spotify display name: %s", e)

    # -- SourceBase hooks --

    def add_routes(self, app):
        app.router.add_get('/playlists', self._handle_playlists)
        app.router.add_get('/setup', self._handle_setup)
        app.router.add_get('/start-auth', self._handle_start_auth)
        app.router.add_get('/callback', self._handle_callback)
        app.router.add_post('/logout', self._handle_logout)

    async def handle_status(self) -> dict:
        # Load digit playlists mapping
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
            'display_name': self._display_name,
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
            uri = get_playlist_uri(digit)
            if uri:
                playlist_id = uri.split(':')[-1]
                log.info("Digit %s -> playlist %s", digit, playlist_id)
                await self._play_playlist(playlist_id)
            else:
                log.info("No playlist mapped to digit %s", digit)

        elif cmd == 'play_playlist':
            playlist_id = data.get('playlist_id', '')
            track_index = data.get('track_index')
            await self._play_playlist(playlist_id, track_index)

        elif cmd == 'play_track':
            uri = data.get('uri', '')
            await self._play_track(uri)

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

    # -- Playback control --

    @staticmethod
    def _spotify_uri_to_url(uri):
        """Convert spotify:type:id to https://open.spotify.com/type/id."""
        parts = uri.split(':')
        if len(parts) == 3 and parts[0] == 'spotify':
            return f"https://open.spotify.com/{parts[1]}/{parts[2]}"
        return uri

    async def _play_playlist(self, playlist_id, track_index=None):
        """Start playing a playlist, optionally at a specific track."""
        url = f"https://open.spotify.com/playlist/{playlist_id}"
        # Look up the track's Spotify URI so the player can find it in the queue
        track_uri = None
        track_meta = None
        if track_index is not None:
            for pl in self.playlists:
                if pl.get('id') == playlist_id:
                    tracks = pl.get('tracks', [])
                    if 0 <= track_index < len(tracks):
                        track_meta = tracks[track_index]
                        track_uri = track_meta.get('uri', '')
                    break
        # Pre-broadcast the selected track's metadata so the UI shows
        # correct artwork immediately, before the Sonos queue rebuilds.
        if track_uri and track_meta:
            await self.broadcast("media_update", {
                "title": track_meta.get("name", ""),
                "artist": track_meta.get("artist", ""),
                "artwork": track_meta.get("image", ""),
                "state": "PLAYING",
            })
            log.info("Pre-broadcast metadata for %s", track_meta.get("name", "?"))

        log.info("Play playlist %s (track_index=%s, track_uri=%s)",
                 playlist_id, track_index, track_uri)
        ok = await self.player_play(uri=url, track_uri=track_uri)
        if ok:
            self.state = "playing"
            await self.register("playing")
            self._start_polling()
        else:
            log.error("Player service failed to start playlist")

    async def _play_track(self, uri):
        """Play a specific track."""
        url = self._spotify_uri_to_url(uri)
        log.info("Play track %s", url)
        ok = await self.player_play(uri=url)
        if ok:
            self.state = "playing"
            await self.register("playing")
            self._start_polling()

    async def _toggle(self):
        if self.state == "playing":
            await self._pause()
        elif self.state == "paused":
            await self._resume()
        elif self.state == "stopped" and self.playlists:
            # Play first playlist
            await self._play_playlist(self.playlists[0]['id'])

    async def _resume(self):
        if await self.player_resume():
            self.state = "playing"
            await self.register("playing")
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
        """Re-fetch playlists by running fetch.py (incremental sync with tracks).

        Passes the service's access token to the subprocess so it doesn't need
        to independently refresh the PKCE token (which would race and revoke it).
        """
        if self.auth.revoked:
            return  # don't bother — token is dead
        self._fetching_playlists = True
        t0 = time.monotonic()
        try:
            # Get a valid access token to pass to the subprocess
            try:
                token = await self.auth.get_token()
            except Exception:
                log.error("Cannot refresh playlists — token refresh failed")
                return

            log.info("Starting playlist refresh via fetch.py")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, FETCH_SCRIPT,
                '--output', PLAYLISTS_FILE,
                '--access-token', token,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                self._load_playlists()
                self._last_refresh = time.monotonic()
                self._last_refresh_wall = datetime.now()
                self._last_refresh_duration = round(time.monotonic() - t0, 1)
                log.info("Playlist refresh complete (%d playlists, %.1fs)",
                         len(self.playlists), self._last_refresh_duration)
            else:
                err_msg = (stdout.decode() + stderr.decode())[-500:]
                log.error("fetch.py failed (rc=%d): %s",
                          proc.returncode, err_msg)
        except asyncio.TimeoutError:
            log.error("Playlist refresh timed out")
        except Exception as e:
            log.error("Playlist refresh failed: %s", e)
        finally:
            self._fetching_playlists = False

    async def _delayed_refresh(self, delay):
        """Refresh playlists after a delay. Used on startup and after OAuth."""
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._refresh_playlists()
        except asyncio.CancelledError:
            return

    async def _nightly_refresh_loop(self):
        """Sleep until 2am, refresh playlists, repeat daily."""
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
        """True if enough time has passed since last refresh."""
        return time.monotonic() - self._last_refresh > PLAYLIST_REFRESH_COOLDOWN

    async def _logout(self):
        """Clear Spotify tokens and playlists, return to setup mode."""
        log.info("Logging out of Spotify")

        # Stop background tasks
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None
        if self._nightly_task:
            self._nightly_task.cancel()
            self._nightly_task = None
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        # Clear in-memory state
        self.auth.clear()
        self.playlists = []
        self.state = "stopped"
        self.now_playing = None
        self._fetching_playlists = False

        # Delete token file
        try:
            path = delete_tokens()
            if path:
                log.info("Deleted token file: %s", path)
        except Exception as e:
            log.warning("Could not delete token file: %s", e)

        # Delete playlist file
        try:
            if os.path.exists(PLAYLISTS_FILE):
                os.unlink(PLAYLISTS_FILE)
                log.info("Deleted playlist file: %s", PLAYLISTS_FILE)
        except Exception as e:
            log.warning("Could not delete playlist file: %s", e)

        await self.register("available")
        log.info("Spotify logged out — ready for new setup")

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
        """Poll Spotify for now-playing info while active."""
        try:
            while self.state in ("playing", "paused"):
                await self._poll_now_playing()
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            return

    async def _poll_now_playing(self):
        """Poll transport state from player service for router registration.

        The player service handles artwork/metadata broadcasting to the UI —
        we only track play-state here so the router knows we're active.
        """
        try:
            state = await self.player_state()
            if state == "playing" and self.state != "playing":
                self.state = "playing"
                await self.register("playing")
            elif state != "playing" and self.state == "playing":
                # Maps "stopped" to "paused" intentionally — lets user resume
                # without re-navigating to Spotify after queue ends
                self.state = "paused"
                await self.register("paused")
        except Exception as e:
            log.warning("Player state poll error: %s", e)

    # -- Extra routes --

    def _build_setup_url(self):
        """Build the setup page URL — HTTPS if cert exists, else HTTP."""
        local_ip = _get_local_ip()
        if os.path.isfile(SSL_CERT) and os.path.isfile(SSL_KEY):
            return f'https://{local_ip}:{SSL_PORT}/setup'
        return f'http://{local_ip}:{self.port}/setup'

    async def _handle_playlists(self, request):
        if not self.auth.is_configured:
            return web.json_response({
                'setup_needed': True,
                'setup_url': self._build_setup_url(),
            }, headers=self._cors_headers())
        if self.auth.revoked:
            return web.json_response({
                'needs_reauth': True,
                'setup_url': self._build_setup_url(),
            }, headers=self._cors_headers())
        if self._fetching_playlists and not self.playlists:
            return web.json_response({
                'loading': True,
            }, headers=self._cors_headers())

        # Trigger background refresh if >5 min since last sync
        if self._should_refresh() and not self._fetching_playlists:
            self._fetching_playlists = True  # set before create_task to prevent double-trigger
            log.info("Playlist view opened — refreshing in background")
            asyncio.create_task(self._refresh_playlists())

        return web.json_response(
            self.playlists,
            headers=self._cors_headers())

    # -- OAuth Setup routes --

    def _load_client_id(self):
        """Get client_id from token store or config."""
        tokens = load_tokens()
        if tokens and tokens.get('client_id'):
            return tokens['client_id']
        return cfg("spotify", "client_id", default="")

    def _ssl_available(self):
        return os.path.isfile(SSL_CERT) and os.path.isfile(SSL_KEY)

    def _require_https(self, request):
        """If SSL is available and request came over HTTP, redirect to HTTPS."""
        if self._ssl_available() and request.scheme == 'http':
            url = f'https://{request.host.split(":")[0]}:{SSL_PORT}{request.path_qs}'
            raise web.HTTPFound(url)

    async def _handle_setup(self, request):
        """Serve the Spotify OAuth setup page (opened on phone via QR)."""
        self._require_https(request)
        client_id = self._load_client_id()
        redirect_uri = self._build_redirect_uri()
        is_reconnect = self.auth.revoked

        if client_id:
            label = "Reconnect to Spotify" if is_reconnect else "Connect to Spotify"
            heading = "Reconnect your Spotify account" if is_reconnect else "Connect your Spotify account"
            desc = ("Your Spotify session has expired. Tap below to reconnect."
                    if is_reconnect else
                    "Tap the button below to authorize BeoSound 5c to access your Spotify playlists.")
            cred_html = f'''
            <div class="step">
                <div class="step-title"><span class="step-number">1</span>{heading}</div>
                <div class="step-content">
                    <p>{desc}</p>
                    <a href="/start-auth?client_id={client_id}" class="submit-btn">{label}</a>
                </div>
            </div>'''
        else:
            cred_html = f'''
            <div class="step">
                <div class="step-title"><span class="step-number">1</span>Create a Spotify App</div>
                <div class="step-content">
                    <p>Go to the <a href="https://developer.spotify.com/dashboard" target="_blank">Spotify Developer Dashboard</a> and create a new app.</p>
                    <p>Set the Redirect URI to:</p>
                    <div class="uri-box" id="redirect-uri">{redirect_uri}</div>
                    <p style="margin-top:8px">Under "Which API/SDKs are you planning to use?", select <strong>Web API</strong>.</p>
                </div>
            </div>
            <div class="step">
                <div class="step-title"><span class="step-number">2</span>Enter Client ID</div>
                <div class="step-content">
                    <form action="/start-auth" method="GET">
                        <label for="client_id">Client ID</label>
                        <input type="text" id="client_id" name="client_id" required placeholder="e.g. a1b2c3d4e5f6...">
                        <button type="submit" class="submit-btn">Connect to Spotify</button>
                    </form>
                </div>
            </div>'''

        html = f'''<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BeoSound 5c - Spotify Setup</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Helvetica Neue',-apple-system,sans-serif;background:#000;color:#fff;padding:20px;line-height:1.7}}
.container{{max-width:500px;margin:0 auto}}
.header{{text-align:center;margin-bottom:30px;padding-bottom:20px;border-bottom:1px solid #333}}
h1{{font-size:24px;font-weight:300;letter-spacing:2px;margin-bottom:8px}}
.subtitle{{color:#666;font-size:14px}}
.step{{background:#111;border-radius:8px;padding:20px;margin-bottom:16px;border:1px solid #222}}
.step-number{{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border:2px solid #1ED760;color:#1ED760;border-radius:50%;font-weight:600;font-size:14px;margin-right:12px}}
.step-title{{font-size:16px;font-weight:500;margin-bottom:12px;display:flex;align-items:center}}
.step-content{{color:#999;font-size:14px;margin-left:40px}}
.step-content p{{margin-bottom:8px}}
a{{color:#999;text-decoration:underline}}a:hover{{color:#fff}}
.uri-box{{background:#000;border:1px solid #333;border-radius:4px;padding:12px;margin:12px 0;font-family:monospace;font-size:12px;word-break:break-all}}
input[type="text"]{{width:100%;padding:12px;margin:8px 0;background:#000;border:1px solid #333;border-radius:4px;color:#fff;font-size:14px}}
input:focus{{outline:none;border-color:#1ED760}}
label{{display:block;margin-top:12px;color:#666;font-size:13px;text-transform:uppercase;letter-spacing:.5px}}
.submit-btn{{display:block;width:100%;padding:14px;margin-top:20px;background:#1ED760;border:none;border-radius:4px;color:#000;font-size:16px;font-weight:600;cursor:pointer;text-align:center;text-decoration:none}}
.submit-btn:hover{{background:#1db954}}
.note{{background:#0a0a0a;border:1px solid #222;border-radius:4px;padding:12px;margin:12px 0;font-size:13px;color:#666}}
</style></head><body>
<div class="container">
<div class="header"><h1>SPOTIFY SETUP</h1><div class="subtitle">BeoSound 5c</div></div>
<div class="note">No secret keys needed. This uses PKCE authentication.</div>
{cred_html}
</div></body></html>'''
        return web.Response(text=html, content_type='text/html')

    def _build_redirect_uri(self):
        """Build the OAuth redirect URI — HTTPS if cert exists, else HTTP."""
        local_ip = _get_local_ip()
        if os.path.isfile(SSL_CERT) and os.path.isfile(SSL_KEY):
            return f'https://{local_ip}:{SSL_PORT}/callback'
        return f'http://{local_ip}:{self.port}/callback'

    async def _handle_start_auth(self, request):
        """Start PKCE auth flow — generate verifier, redirect to Spotify."""
        self._require_https(request)
        client_id = request.query.get('client_id', '').strip()
        if not client_id:
            return web.Response(text='Client ID is required', status=400)

        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)
        redirect_uri = self._build_redirect_uri()

        self._pkce_state = {
            'client_id': client_id,
            'code_verifier': verifier,
            'redirect_uri': redirect_uri,
        }

        auth_url = build_auth_url(client_id, redirect_uri, challenge, SPOTIFY_SCOPES)
        log.info("OAuth: redirecting to Spotify (redirect_uri=%s)", redirect_uri)
        raise web.HTTPFound(auth_url)

    async def _handle_callback(self, request):
        """Handle OAuth callback from Spotify — exchange code, save tokens."""
        error = request.query.get('error')
        if error:
            return web.Response(text=f'Spotify authorization failed: {error}', status=400)

        code = request.query.get('code', '')
        if not code or not self._pkce_state:
            setup_url = self._build_setup_url()
            return web.Response(
                text=f'Session expired. <a href="{setup_url}">Try again</a>',
                content_type='text/html', status=400)

        client_id = self._pkce_state['client_id']
        verifier = self._pkce_state['code_verifier']
        redirect_uri = self._pkce_state['redirect_uri']
        self._pkce_state = {}

        try:
            log.info("OAuth: exchanging authorization code")
            loop = asyncio.get_running_loop()
            token_data = await loop.run_in_executor(
                None, exchange_code, code, client_id, verifier, redirect_uri)

            rt = token_data.get('refresh_token')
            if not rt:
                return web.Response(text='No refresh token received', status=500)

            # Save tokens — try file first, fall back to in-memory only
            try:
                await loop.run_in_executor(None, save_tokens, client_id, rt)
                log.info("OAuth: tokens saved to disk")
            except Exception as e:
                log.warning("OAuth: could not save tokens to disk (%s) — using in-memory", e)

            # Load auth directly (works even if file save failed)
            self.auth.set_credentials(
                client_id, rt,
                access_token=token_data.get('access_token'),
                expires_in=token_data.get('expires_in', 3600))
            self.player = "remote"

            # Register as available now that we have credentials
            await self.register("available")

            # Fetch display name
            asyncio.create_task(self._fetch_display_name())

            # Kick off playlist refresh in background (no initial delay)
            self._fetching_playlists = True
            if self._refresh_task:
                self._refresh_task.cancel()
            self._refresh_task = asyncio.create_task(
                self._delayed_refresh(delay=0))

            # Start nightly refresh if not already running
            if not self._nightly_task or self._nightly_task.done():
                self._nightly_task = asyncio.create_task(
                    self._nightly_refresh_loop())

            html = '''<!DOCTYPE html><html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BeoSound 5c - Connected</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Helvetica Neue',sans-serif;background:#000;color:#fff;padding:20px;text-align:center}
.container{max-width:500px;margin:50px auto}
.ok{width:80px;height:80px;border:3px solid #1ED760;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 30px;font-size:36px;color:#1ED760}
h1{font-size:24px;font-weight:300;margin-bottom:20px;letter-spacing:1px}
.note{color:#666;font-size:14px;margin-top:30px}
</style></head><body>
<div class="container">
<div class="ok">&#10003;</div>
<h1>Connected to Spotify</h1>
<p style="color:#999">Playlists are loading now.<br>You can close this page.</p>
<p class="note">The BeoSound 5c screen will update automatically.</p>
</div></body></html>'''
            return web.Response(text=html, content_type='text/html')

        except Exception as e:
            log.error("OAuth callback failed: %s", e)
            return web.Response(text=f'Setup failed: {e}', status=500)

    async def _handle_logout(self, request):
        """HTTP endpoint for logout — called from system.html."""
        await self._logout()
        return web.json_response(
            {'status': 'ok', 'message': 'Logged out'},
            headers=self._cors_headers())


if __name__ == '__main__':
    service = SpotifyService()
    asyncio.run(service.run())
