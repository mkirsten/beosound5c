#!/usr/bin/env python3
"""
BeoSound 5c Spotify Source (beo-spotify)

Provides Spotify playback via the Web API with PKCE authentication.
Plays on Sonos via native queue (SoCo ShareLink) for best quality.
Falls back to librespot for non-Sonos outputs.

Port: 8771
"""

import asyncio
import json
import logging
import os
import ssl
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

from aiohttp import web, ClientSession

# Shared library
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.config import cfg
from lib.source_base import SourceBase

# Spotify tools — project root is 3 levels up from services/sources/spotify.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS_DIR = os.path.join(PROJECT_ROOT, 'tools', 'spotify')
sys.path.insert(0, TOOLS_DIR)
from pkce import (
    refresh_access_token,
    generate_code_verifier,
    generate_code_challenge,
    build_auth_url,
    exchange_code,
)
from token_store import load_tokens, save_tokens, _find_store_path

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-spotify')

# Configuration
SONOS_IP = cfg("sonos", "ip", default="")
VOLUME_TYPE = cfg("volume", "type", default="sonos")
PLAYLISTS_FILE = os.path.join(
    os.getenv('BS5C_BASE_PATH', PROJECT_ROOT),
    'web', 'json', 'spotify_playlists.json')

SPOTIFY_API = "https://api.spotify.com/v1"
POLL_INTERVAL = 3  # seconds between now-playing polls
PLAYLIST_REFRESH_INTERVAL = 30 * 60  # 30 minutes
FETCH_SCRIPT = os.path.join(TOOLS_DIR, 'fetch_playlists.py')

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


class SpotifyAuth:
    """Manages Spotify access tokens with automatic refresh."""

    def __init__(self):
        self._access_token = None
        self._token_expiry = 0
        self._client_id = None
        self._refresh_token = None
        self._client_secret = None  # legacy fallback from env vars

    def load(self):
        """Load credentials from token store, with env var fallback."""
        tokens = load_tokens()
        if tokens and tokens.get('client_id') and tokens.get('refresh_token'):
            pass  # Use token store
        elif tokens is not None:
            # Token file exists but incomplete — treat as needs-setup
            log.info("Token file exists but incomplete — waiting for setup")
            return False
        else:
            # No token file — try legacy env vars
            cid = os.environ.get('SPOTIFY_CLIENT_ID', '')
            rt = os.environ.get('SPOTIFY_REFRESH_TOKEN', '')
            if cid and rt:
                log.info("Using legacy env var credentials")
                tokens = {'client_id': cid, 'refresh_token': rt}
            else:
                log.warning("No Spotify tokens found — run setup_spotify.py first")
                return False
        self._client_id = tokens.get('client_id', '')
        self._refresh_token = tokens.get('refresh_token', '')
        self._client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET', '')
        if not self._client_id or not self._refresh_token:
            log.warning("Incomplete Spotify tokens")
            return False
        log.info("Spotify credentials loaded (client_id: %s...)", self._client_id[:8])
        return True

    async def get_token(self):
        """Get a valid access token, refreshing if needed."""
        if self._access_token and time.monotonic() < self._token_expiry:
            return self._access_token
        return await self._refresh()

    async def _refresh(self):
        """Refresh the access token. Tries PKCE first, then with client_secret."""
        if not self._client_id or not self._refresh_token:
            raise RuntimeError("No Spotify credentials")

        loop = asyncio.get_event_loop()

        # Try PKCE refresh first (no client_secret)
        try:
            result = await loop.run_in_executor(
                None, refresh_access_token, self._client_id, self._refresh_token, None)
        except urllib.error.HTTPError as e:
            if e.code == 400 and self._client_secret:
                log.info("PKCE refresh failed, trying with client_secret")
                result = await loop.run_in_executor(
                    None, refresh_access_token, self._client_id, self._refresh_token,
                    self._client_secret)
            else:
                raise

        self._access_token = result['access_token']
        self._token_expiry = time.monotonic() + result.get('expires_in', 3600) - 300

        # Persist rotated refresh token
        new_rt = result.get('refresh_token')
        if new_rt and new_rt != self._refresh_token:
            self._refresh_token = new_rt
            await loop.run_in_executor(
                None, save_tokens, self._client_id, new_rt)
            log.info("Refresh token rotated")

        log.info("Access token refreshed (expires in %ds)", result.get('expires_in', 0))
        return self._access_token

    @property
    def is_configured(self):
        return bool(self._client_id and self._refresh_token)


class SpotifyAPI:
    """Thin wrapper around Spotify Web API calls."""

    def __init__(self, auth: SpotifyAuth, session: ClientSession):
        self.auth = auth
        self.session = session

    async def _headers(self):
        token = await self.auth.get_token()
        return {"Authorization": f"Bearer {token}"}

    async def get(self, path, params=None):
        url = f"{SPOTIFY_API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = await self._headers()
        async with self.session.get(url, headers=headers, timeout=10) as resp:
            if resp.status == 204:
                return None
            if resp.status != 200:
                body = await resp.text()
                log.warning("Spotify GET %s -> %d: %s", path, resp.status, body[:200])
                return None
            return await resp.json()

    async def put(self, path, json_data=None):
        url = f"{SPOTIFY_API}{path}"
        headers = await self._headers()
        headers["Content-Type"] = "application/json"
        async with self.session.put(url, headers=headers, json=json_data, timeout=10) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                log.warning("Spotify PUT %s -> %d: %s", path, resp.status, body[:200])
                return False
            return True

    async def post(self, path, json_data=None):
        url = f"{SPOTIFY_API}{path}"
        headers = await self._headers()
        headers["Content-Type"] = "application/json"
        async with self.session.post(url, headers=headers, json=json_data, timeout=10) as resp:
            if resp.status not in (200, 204):
                body = await resp.text()
                log.warning("Spotify POST %s -> %d: %s", path, resp.status, body[:200])
                return False
            return True

    async def get_devices(self):
        data = await self.get("/me/player/devices")
        return data.get("devices", []) if data else []

    async def get_currently_playing(self):
        return await self.get("/me/player/currently-playing")

    async def start_playback(self, device_id=None, context_uri=None, uris=None, offset=None):
        body = {}
        if context_uri:
            body["context_uri"] = context_uri
        if uris:
            body["uris"] = uris
        if offset is not None:
            body["offset"] = offset
        params = f"?device_id={device_id}" if device_id else ""
        return await self.put(f"/me/player/play{params}", body or None)

    async def pause_playback(self, device_id=None):
        params = f"?device_id={device_id}" if device_id else ""
        return await self.put(f"/me/player/pause{params}")

    async def next_track(self, device_id=None):
        params = f"?device_id={device_id}" if device_id else ""
        return await self.post(f"/me/player/next{params}")

    async def previous_track(self, device_id=None):
        params = f"?device_id={device_id}" if device_id else ""
        return await self.post(f"/me/player/previous{params}")


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
    }

    def __init__(self):
        super().__init__()
        self.auth = SpotifyAuth()
        self.api = None  # set after session is created
        self.soco = None  # SoCo instance for Sonos playback
        self.soco = None  # SoCo instance for transport controls (Sonos path)
        self.playlists = []
        self.state = "stopped"  # stopped | playing | paused
        self.now_playing = None  # current track metadata
        self._poll_task = None
        self._refresh_task = None
        self._target_device_id = None  # Spotify Connect device_id (librespot only)
        self._use_soco = False  # True = Sonos native queue via SoCo, False = Spotify Web API
        self._pkce_state = {}  # Temporary state during OAuth flow
        self._fetching_playlists = False  # True while initial fetch is running

    async def on_start(self):
        self.api = SpotifyAPI(self.auth, self._http_session)

        # Load credentials (may fail — setup flow will handle it)
        has_creds = self.auth.load()

        if has_creds:
            # Load playlists from file
            self._load_playlists()

            # Init Sonos (SoCo) if configured — primary playback path
            if SONOS_IP:
                try:
                    import soco
                    self.soco = soco.SoCo(SONOS_IP)
                    self._use_soco = True
                    self.player = "sonos"
                    log.info("Sonos connected: %s (%s) — direct SoCo playback",
                             self.soco.player_name, SONOS_IP)
                except Exception as e:
                    log.warning("Sonos init failed, will try Spotify Web API: %s", e)

            # Non-Sonos: discover Spotify Connect devices (librespot etc.)
            if not self._use_soco:
                self.player = "local"
                await self._discover_devices()
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
                 "Sonos/SoCo" if self._use_soco else
                 "awaiting setup" if not has_creds else "librespot")

        # Start periodic playlist refresh in background
        if self.auth.is_configured:
            self._refresh_task = asyncio.create_task(self._playlist_refresh_loop())

    async def on_stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
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

    async def _discover_devices(self):
        """Discover Spotify Connect devices and select the best target."""
        if not self.auth.is_configured:
            return

        try:
            devices = await self.api.get_devices()
            log.info("Spotify devices: %s", [d.get('name') for d in devices])

            for d in devices:
                name = d.get("name", "").lower()
                dtype = d.get("type", "").lower()

                # Match Sonos speaker
                if self.soco and (dtype == "speaker" or "sonos" in name):
                    self._sonos_device_id = d["id"]
                    log.info("Found Sonos device: %s (id: %s)", d["name"], d["id"][:8])

                # Match librespot
                if "beosound" in name or dtype == "computer":
                    self._librespot_device_id = d["id"]
                    log.info("Found librespot device: %s (id: %s)", d["name"], d["id"][:8])

            # Select target: Sonos first, librespot fallback
            if self._sonos_device_id and self.soco:
                self._target_device_id = self._sonos_device_id
                self._use_soco = True
            elif self._librespot_device_id:
                self._target_device_id = self._librespot_device_id
                self._use_soco = False
            else:
                log.warning("No suitable Spotify Connect device found")

        except Exception as e:
            log.warning("Device discovery failed: %s", e)

    # ── SourceBase hooks ──

    def add_routes(self, app):
        app.router.add_get('/playlists', self._handle_playlists)
        app.router.add_get('/setup', self._handle_setup)
        app.router.add_get('/start-auth', self._handle_start_auth)
        app.router.add_get('/callback', self._handle_callback)
        app.router.add_post('/logout', self._handle_logout)

    async def handle_status(self) -> dict:
        return {
            'state': self.state,
            'now_playing': self.now_playing,
            'playlist_count': len(self.playlists),
            'target': 'sonos' if self._use_soco else 'librespot',
            'has_credentials': self.auth.is_configured,
        }

    async def handle_resync(self) -> dict:
        if self.auth.is_configured:
            state = self.state if self.state in ('playing', 'paused') else 'available'
            await self.register(state)
            if self.now_playing:
                await self._broadcast_update()
            return {'status': 'ok', 'resynced': True}
        return {'status': 'ok', 'resynced': False}

    async def handle_command(self, cmd, data) -> dict:
        if cmd == 'play_playlist':
            playlist_id = data.get('playlist_id', '')
            track_index = data.get('track_index')
            await self._play_playlist(playlist_id, track_index)

        elif cmd == 'play_track':
            uri = data.get('uri', '')
            context_uri = data.get('context_uri')
            await self._play_track(uri, context_uri)

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

    # ── Playback control ──

    @staticmethod
    def _spotify_uri_to_url(uri):
        """Convert spotify:type:id to https://open.spotify.com/type/id."""
        parts = uri.split(':')
        if len(parts) == 3 and parts[0] == 'spotify':
            return f"https://open.spotify.com/{parts[1]}/{parts[2]}"
        return uri

    async def _play_playlist(self, playlist_id, track_index=None):
        """Start playing a playlist, optionally at a specific track."""
        if self._use_soco:
            await self._soco_play_playlist(playlist_id, track_index)
        else:
            await self._api_play_playlist(playlist_id, track_index)

    async def _soco_play_playlist(self, playlist_id, track_index=None):
        """Play a Spotify playlist on Sonos native queue via SoCo ShareLink."""
        from soco.plugins.sharelink import ShareLinkPlugin

        url = f"https://open.spotify.com/playlist/{playlist_id}"
        log.info("SoCo: play playlist %s (track_index=%s)", playlist_id, track_index)

        loop = asyncio.get_event_loop()
        share_link = ShareLinkPlugin(self.soco)
        await loop.run_in_executor(None, self.soco.clear_queue)
        await loop.run_in_executor(None, share_link.add_share_link_to_queue, url)
        await loop.run_in_executor(None, self.soco.play_from_queue, track_index or 0)

        self.state = "playing"
        await self.register("playing")
        self._start_polling()

    async def _api_play_playlist(self, playlist_id, track_index=None):
        """Play a Spotify playlist via Web API (librespot path)."""
        context_uri = f"spotify:playlist:{playlist_id}"
        offset = {"position": track_index} if track_index is not None else None

        if not self._target_device_id:
            await self._discover_devices()

        ok = await self.api.start_playback(
            device_id=self._target_device_id,
            context_uri=context_uri,
            offset=offset)

        if ok:
            self.state = "playing"
            await self.register("playing")
            self._start_polling()
            await self._poll_now_playing()
        else:
            log.error("Failed to start playlist playback")

    async def _play_track(self, uri, context_uri=None):
        """Play a specific track, optionally within a context."""
        if self._use_soco:
            await self._soco_play_track(uri)
            return

        if not self._target_device_id:
            await self._discover_devices()

        if context_uri:
            ok = await self.api.start_playback(
                device_id=self._target_device_id,
                context_uri=context_uri,
                offset={"uri": uri})
        else:
            ok = await self.api.start_playback(
                device_id=self._target_device_id,
                uris=[uri])

        if ok:
            self.state = "playing"
            await self.register("playing")
            self._start_polling()
            await self._poll_now_playing()

    async def _soco_play_track(self, uri):
        """Play a single Spotify track on Sonos native queue via SoCo ShareLink."""
        from soco.plugins.sharelink import ShareLinkPlugin

        url = self._spotify_uri_to_url(uri)
        log.info("SoCo: play track %s", url)

        loop = asyncio.get_event_loop()
        share_link = ShareLinkPlugin(self.soco)
        await loop.run_in_executor(None, self.soco.clear_queue)
        await loop.run_in_executor(None, share_link.add_share_link_to_queue, url)
        await loop.run_in_executor(None, self.soco.play_from_queue, 0)

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
        if self._use_soco and self.soco:
            try:
                self.soco.play()
                self.state = "playing"
                await self.register("playing")
                self._start_polling()
                return
            except Exception as e:
                log.warning("SoCo play failed, trying Web API: %s", e)

        if not self._target_device_id:
            await self._discover_devices()
        ok = await self.api.start_playback(device_id=self._target_device_id)
        if ok:
            self.state = "playing"
            await self.register("playing")
            self._start_polling()

    async def _pause(self):
        if self._use_soco and self.soco:
            try:
                self.soco.pause()
                self.state = "paused"
                await self.register("paused")
                await self._broadcast_update()
                return
            except Exception as e:
                log.warning("SoCo pause failed, trying Web API: %s", e)

        ok = await self.api.pause_playback(device_id=self._target_device_id)
        if ok:
            self.state = "paused"
            await self.register("paused")
            await self._broadcast_update()

    async def _next(self):
        if self._use_soco and self.soco:
            try:
                self.soco.next()
                await asyncio.sleep(0.5)
                await self._poll_now_playing()
                return
            except Exception as e:
                log.warning("SoCo next failed, trying Web API: %s", e)

        ok = await self.api.next_track(device_id=self._target_device_id)
        if ok:
            await asyncio.sleep(0.5)
            await self._poll_now_playing()

    async def _prev(self):
        if self._use_soco and self.soco:
            try:
                self.soco.previous()
                await asyncio.sleep(0.5)
                await self._poll_now_playing()
                return
            except Exception as e:
                log.warning("SoCo previous failed, trying Web API: %s", e)

        ok = await self.api.previous_track(device_id=self._target_device_id)
        if ok:
            await asyncio.sleep(0.5)
            await self._poll_now_playing()

    async def _stop(self):
        if self._use_soco and self.soco:
            try:
                self.soco.pause()
            except Exception:
                pass
        else:
            await self.api.pause_playback(device_id=self._target_device_id)

        self.state = "stopped"
        self._stop_polling()
        await self.register("available")
        await self._broadcast_update()

    async def _refresh_playlists(self):
        """Re-fetch playlists by running fetch_playlists.py (incremental sync with tracks)."""
        self._fetching_playlists = True
        try:
            log.info("Starting playlist refresh via fetch_playlists.py")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, FETCH_SCRIPT, '--output', PLAYLISTS_FILE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                self._load_playlists()
                log.info("Playlist refresh complete (%d playlists)", len(self.playlists))
            else:
                log.error("fetch_playlists.py failed (rc=%d): %s",
                          proc.returncode, stderr.decode()[-500:])
        except asyncio.TimeoutError:
            log.error("Playlist refresh timed out")
        except Exception as e:
            log.error("Playlist refresh failed: %s", e)
        finally:
            self._fetching_playlists = False

    async def _playlist_refresh_loop(self):
        """Periodically refresh playlists in the background."""
        try:
            # Initial refresh shortly after startup
            await asyncio.sleep(10)
            await self._refresh_playlists()
            # Then every 30 minutes
            while True:
                await asyncio.sleep(PLAYLIST_REFRESH_INTERVAL)
                await self._refresh_playlists()
        except asyncio.CancelledError:
            return

    async def _logout(self):
        """Clear Spotify tokens and playlists, return to setup mode."""
        log.info("Logging out of Spotify")

        # Stop background tasks
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        # Clear in-memory state
        self.auth._client_id = None
        self.auth._refresh_token = None
        self.auth._access_token = None
        self.auth._token_expiry = 0
        self.playlists = []
        self.state = "stopped"
        self.now_playing = None
        self._fetching_playlists = False

        # Delete token file
        try:
            token_path = _find_store_path()
            if os.path.exists(token_path):
                os.unlink(token_path)
                log.info("Deleted token file: %s", token_path)
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

    # ── Now-playing polling ──

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
        """Fetch currently playing info and broadcast to UI."""
        if self._use_soco and self.soco:
            await self._poll_now_playing_soco()
        else:
            await self._poll_now_playing_api()

    async def _poll_now_playing_soco(self):
        """Poll transport state from Sonos for router registration.

        beo-sonos handles artwork/metadata broadcasting to the UI — we only
        track play-state here so the router knows we're active.
        """
        try:
            loop = asyncio.get_event_loop()
            transport = await loop.run_in_executor(
                None, self.soco.get_current_transport_info)

            transport_state = transport.get("current_transport_state", "")
            is_playing = transport_state == "PLAYING"

            # Update state if changed externally
            if is_playing and self.state != "playing":
                self.state = "playing"
                await self.register("playing")
            elif not is_playing and self.state == "playing":
                self.state = "paused"
                await self.register("paused")

        except Exception as e:
            log.warning("SoCo state poll error: %s", e)

    async def _poll_now_playing_api(self):
        """Poll now-playing from Spotify Web API (librespot path).

        Broadcasts media_update in beo-sonos–compatible format so the
        standard PLAYING view can display artwork and metadata.
        """
        try:
            data = await self.api.get_currently_playing()
            if not data or not data.get("item"):
                return

            item = data["item"]
            is_playing = data.get("is_playing", False)

            # Update state if it changed externally
            if is_playing and self.state != "playing":
                self.state = "playing"
                await self.register("playing")
            elif not is_playing and self.state == "playing":
                self.state = "paused"
                await self.register("paused")

            state_str = "PLAYING" if is_playing else "PAUSED_PLAYBACK"
            artwork_url = (item.get("album", {}).get("images", [{}])[0]
                           .get("url", ""))

            new_playing = {
                "track": item.get("name", ""),
                "artist": ", ".join(a["name"] for a in item.get("artists", [])),
                "album": item.get("album", {}).get("name", ""),
                "artwork": artwork_url,
                "uri": item.get("uri", ""),
                "duration_ms": item.get("duration_ms", 0),
                "progress_ms": data.get("progress_ms", 0),
                "is_playing": is_playing,
                "context_uri": (data.get("context") or {}).get("uri", ""),
            }

            # Only broadcast on change
            if (not self.now_playing or
                    self.now_playing.get("uri") != new_playing["uri"] or
                    self.now_playing.get("is_playing") != new_playing["is_playing"]):
                self.now_playing = new_playing
                # Broadcast as media_update in beo-sonos format for PLAYING view
                await self.broadcast("media_update", {
                    "title": new_playing["track"],
                    "artist": new_playing["artist"],
                    "album": new_playing["album"],
                    "artwork": artwork_url,
                    "state": state_str,
                    "uri": new_playing["uri"],
                })
                log.info("Now playing: %s — %s", new_playing["artist"], new_playing["track"])
            else:
                self.now_playing["progress_ms"] = new_playing["progress_ms"]

        except Exception as e:
            log.warning("Now-playing poll error: %s", e)

    async def _broadcast_update(self):
        """Broadcast state to UI.

        SoCo path: no-op — beo-sonos handles artwork/metadata for PLAYING view.
        Librespot path: sends media_update in beo-sonos format.
        """
        if self._use_soco:
            return  # beo-sonos handles UI updates

        np = self.now_playing or {}
        state_str = "PLAYING" if self.state == "playing" else (
            "PAUSED_PLAYBACK" if self.state == "paused" else "STOPPED")
        await self.broadcast("media_update", {
            "title": np.get("track", ""),
            "artist": np.get("artist", ""),
            "album": np.get("album", ""),
            "artwork": np.get("artwork", ""),
            "state": state_str,
            "uri": np.get("uri", ""),
        })

    # ── Extra routes ──

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
        if self._fetching_playlists and not self.playlists:
            return web.json_response({
                'loading': True,
            }, headers=self._cors_headers())
        return web.json_response(
            self.playlists,
            headers=self._cors_headers())

    # ── OAuth Setup routes ──

    def _load_client_id(self):
        """Get client_id from token store or config."""
        tokens = load_tokens()
        if tokens and tokens.get('client_id'):
            return tokens['client_id']
        return cfg("spotify", "client_id", default="")

    async def _handle_setup(self, request):
        """Serve the Spotify OAuth setup page (opened on phone via QR)."""
        client_id = self._load_client_id()
        redirect_uri = self._build_redirect_uri()

        if client_id:
            cred_html = f'''
            <div class="step">
                <div class="step-title"><span class="step-number">1</span>Connect your Spotify account</div>
                <div class="step-content">
                    <p>Tap the button below to authorize BeoSound 5c to access your Spotify playlists.</p>
                    <a href="/start-auth?client_id={client_id}" class="submit-btn">Connect to Spotify</a>
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
            return web.Response(text='Missing code or expired session. Please try again.',
                                status=400)

        client_id = self._pkce_state['client_id']
        verifier = self._pkce_state['code_verifier']
        redirect_uri = self._pkce_state['redirect_uri']
        self._pkce_state = {}

        try:
            log.info("OAuth: exchanging authorization code")
            loop = asyncio.get_event_loop()
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
            self.auth._client_id = client_id
            self.auth._refresh_token = rt
            self.auth._access_token = token_data.get('access_token')
            self.auth._token_expiry = time.monotonic() + token_data.get('expires_in', 3600) - 300
            self.api = SpotifyAPI(self.auth, self._http_session)

            # Register as available now that we have credentials
            await self.register("available")

            # Kick off playlist refresh in background
            self._fetching_playlists = True
            if self._refresh_task:
                self._refresh_task.cancel()
            self._refresh_task = asyncio.create_task(self._post_setup_refresh())

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

    async def _post_setup_refresh(self):
        """After OAuth setup, refresh playlists then start the regular loop."""
        try:
            await self._refresh_playlists()
            while True:
                await asyncio.sleep(PLAYLIST_REFRESH_INTERVAL)
                await self._refresh_playlists()
        except asyncio.CancelledError:
            return


if __name__ == '__main__':
    service = SpotifyService()
    asyncio.run(service.run())
