#!/usr/bin/env python3
"""
BeoSound 5c Spotify Source (beo-source-spotify)

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

# Shared library (services/) — must come first so ``lib`` is importable
# by sibling modules that now import from it (e.g. spotify_tokens).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# Sibling imports (this directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spotify_auth import SpotifyAuth, RemoteSpotifyAuth, missing_scopes
from spotify_tokens import load_tokens, save_tokens, delete_tokens
from pkce import (
    generate_code_verifier,
    generate_code_challenge,
    build_auth_url,
    exchange_code,
)

from lib.config import cfg
from lib.source_base import SourceBase
from lib.digit_playlists import DigitPlaylistMixin
from lib.spotify_canvas import SpotifyCanvasClient, normalize_spotify_track_uri

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-source-spotify')

# Configuration
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PLAYLISTS_FILE = os.path.join(
    os.getenv('BS5C_BASE_PATH', PROJECT_ROOT),
    'web', 'json', 'spotify_playlists.json')

POLL_INTERVAL = 3  # seconds between now-playing polls
PLAYLIST_REFRESH_COOLDOWN = 5 * 60  # don't re-sync if last sync was <5 min ago
NIGHTLY_REFRESH_HOUR = 2  # refresh playlists at 2am
FETCH_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fetch.py')

# Persistence for the last-played playlist/track so we can resume on a fresh
# source activation after a service or device restart.
LAST_PLAYED_PATH_PROD = os.path.join(
    os.getenv('BS5C_CONFIG_DIR', '/etc/beosound5c'), 'spotify_last_played.json')
LAST_PLAYED_PATH_DEV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'spotify_last_played.json')

# OAuth setup
SPOTIFY_SCOPES = ('playlist-read-private playlist-read-collaborative '
                  'user-library-read '
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


class SpotifyService(DigitPlaylistMixin, SourceBase):
    """Main Spotify source service."""

    id = "spotify"
    name = "Spotify"
    port = 8771
    DIGIT_PLAYLISTS_FILE = os.path.join(
        os.getenv('BS5C_BASE_PATH', PROJECT_ROOT),
        'web', 'json', 'digit_playlists.json')
    action_map = {
        "play": "play",
        "pause": "pause",
        "go": "toggle",
        "next": "next",
        "prev": "prev",
        "right": "next",
        "left": "prev",
        "up": "next",
        "down": "prev",
        "stop": "stop",
        # Beo4 RANDOM key (0xC1) toggles shuffle while Spotify is active
        "random": "shuffle",
        "0": "digit", "1": "digit", "2": "digit",
        "3": "digit", "4": "digit", "5": "digit",
        "6": "digit", "7": "digit", "8": "digit",
        "9": "digit",
    }

    def __init__(self):
        super().__init__()
        token_master = cfg("spotify", "token_master", default="")
        if token_master:
            log.info("Using remote token master: %s", token_master)
            self.auth = RemoteSpotifyAuth(token_master)
        else:
            self.auth = SpotifyAuth()
        self.playlists = []
        self.state = "stopped"  # stopped | playing | paused
        self.now_playing = None  # current track metadata
        self._poll_task = None
        self._refresh_task = None
        self._nightly_task = None
        self._pkce_state = {}  # Single dict, not per-session — fine for single-user device
        self._fetching_playlists = False  # True while initial fetch is running
        self._last_playlist_id = None  # last playlist we queued on the player
        self._last_track_uri = None   # last Spotify track URI seen on player
        self._track_advanced_at = -10.0 # monotonic time of last _advance_track_uri call
        self._track_gen = 0           # incremented on every track change; background tasks abort if stale
        self._last_play_time = 0      # monotonic time of last play command (debounce)
        self._last_refresh = 0  # monotonic timestamp of last completed refresh
        self._last_refresh_wall = None  # wall-clock datetime of last completed refresh
        self._last_refresh_duration = None  # seconds the last refresh took
        self._display_name = None  # Spotify display name from /v1/me
        self._canvas = SpotifyCanvasClient()  # reads SPOTIFY_SP_DC from env
        self._shuffle_on = False  # our view of the player's shuffle state

    async def on_start(self):
        # Load credentials (may fail — setup flow will handle it)
        has_creds = self.auth.load(config_client_id=cfg("spotify", "client_id", default=""))

        if has_creds:
            self._load_playlists()
            self._load_last_played()
            self._detect_player()

            # Fetch display name from Spotify profile
            self._spawn(self._fetch_display_name(), name="fetch_display_name")

            # Pre-warm canvas client (TOTP secrets + web token) in background
            if self._canvas.configured:
                self._spawn(self._warmup_canvas(), name="warmup_canvas")

            # Check if a player service supports Spotify (Sonos natively,
            # or local player with go-librespot)
            caps = await self.player_capabilities()
            if "spotify" in caps:
                log.info("Player service available with Spotify support")
            else:
                log.warning("Player service does not support Spotify playback")
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
            await self.auth.start_keepalive()

    async def on_stop(self):
        self.auth.stop_keepalive()
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

    # ── Last-played persistence ──
    # Keeps {playlist_id, track_uri} on disk so `activate_playback` can
    # resume where the user left off after a service/device restart.

    def _last_played_path(self) -> str:
        if os.path.exists(os.path.dirname(LAST_PLAYED_PATH_PROD)):
            return LAST_PLAYED_PATH_PROD
        return LAST_PLAYED_PATH_DEV

    def _load_last_played(self):
        path = self._last_played_path()
        try:
            with open(path) as f:
                data = json.load(f)
            self._last_playlist_id = data.get("playlist_id") or None
            self._last_track_uri = data.get("track_uri") or None
            log.info("Loaded last played: playlist=%s track=%s",
                     self._last_playlist_id, self._last_track_uri)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Failed to load last played: %s", e)

    def _save_last_played(self):
        if not self._last_playlist_id and not self._last_track_uri:
            return
        path = self._last_played_path()
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({
                    "playlist_id": self._last_playlist_id,
                    "track_uri": self._last_track_uri,
                }, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            log.warning("Failed to save last played: %s", e)

    async def _warmup_canvas(self):
        """Pre-warm canvas client (TOTP secrets + web token) so first track is fast."""
        try:
            if await self._canvas.warmup():
                log.info("Canvas client warmed up")
            else:
                log.warning("Canvas client warmup failed — canvas won't be available")
        except Exception as e:
            log.warning("Canvas warmup error: %s", e)

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
        app.router.add_get('/token', self._handle_token)
        app.router.add_get('/canvas', self._handle_canvas)
        app.router.add_get('/setup-status', self._handle_setup_status)
        app.router.add_get('/setup', self._handle_setup)
        app.router.add_get('/start-auth', self._handle_start_auth)
        app.router.add_get('/callback', self._handle_callback)
        app.router.add_post('/logout', self._handle_logout)

    async def _handle_canvas(self, request):
        """GET /canvas?track_id=<id> — return canvas URL for a track.

        Accepts ``track_id`` (bare 22-char id, the canonical form used
        by ``endpoints.spotify_canvas_url``) or ``uri`` (legacy: any
        Spotify URI in any format). Both are normalized to
        ``spotify:track:<id>`` before lookup, so callers don't have to
        care whether they have a Sonos-wrapped URI, a web URL, or a
        bare id.

        Historical bug: this handler used to read ``uri`` while the
        URL builder sent ``track_id``, so player-originated canvas
        injection silently returned empty for every external/Sonos
        playback start. The mismatch is fixed here and locked by
        ``test_canvas_endpoint.py``.
        """
        raw = request.query.get("track_id") or request.query.get("uri", "")
        if not raw or not self._canvas.configured:
            return web.json_response(
                {"canvas_url": ""}, headers=self._cors_headers())
        track_uri = normalize_spotify_track_uri(raw)
        if not track_uri:
            return web.json_response(
                {"canvas_url": ""}, headers=self._cors_headers())
        try:
            url = await self._canvas.get_canvas_url(track_uri)
            return web.json_response(
                {"canvas_url": url or ""}, headers=self._cors_headers())
        except Exception as e:
            log.warning("Canvas lookup failed for %s: %s", track_uri, e)
            return web.json_response(
                {"canvas_url": ""}, headers=self._cors_headers())

    async def handle_status(self) -> dict:
        return {
            'state': self.state,
            'now_playing': self.now_playing,
            'playlist_count': len(self.playlists),
            'has_credentials': self.auth.is_configured,
            'needs_reauth': self.auth.revoked,
            'reauth_recommended': self.auth.reauth_recommended,
            'auth_age_days': self.auth.auth_age_days,
            'display_name': self._display_name,
            'last_refresh': self._last_refresh_wall.isoformat() if self._last_refresh_wall else None,
            'last_refresh_duration': self._last_refresh_duration,
            'digit_playlists': self._get_digit_names(),
            'fetching': self._fetching_playlists,
            'shuffle': self._shuffle_on,
        }

    async def handle_resync(self) -> dict:
        if self.auth.is_configured:
            state = self.state if self.state in ('playing', 'paused') else 'available'
            await self.register(state)
            await self._resync_media()
            return {'status': 'ok', 'resynced': True}
        return {'status': 'ok', 'resynced': False}

    async def handle_activate(self, data):
        """Source button pressed — resume or start, never pause.
        Plays from cache even if OAuth token is revoked — go-librespot
        has its own auth. Only blocks if there are no playlists at all."""
        if self.auth.revoked:
            if not self.playlists:
                log.warning("Activate: Spotify needs re-authentication and no cached playlists")
                await self.register("available")
                return
            log.info("Activate: OAuth revoked, playing from cache (%d playlists)",
                     len(self.playlists))
        # Base: pre-broadcast cached metadata + register + activate_playback
        await super().handle_activate(data)

    async def activate_playback(self):
        # Check if the player still has our Spotify content — if so, resume
        # to preserve queue position (user may have skipped tracks).
        # If another source took over, re-queue from last known track.
        player_uri = await self.player_track_uri()
        if player_uri and player_uri.startswith("spotify:"):
            log.info("Activate: player has Spotify content, resuming")
            self._last_track_uri = player_uri
            if not self._last_playlist_id and self.playlists:
                self._last_playlist_id = self.playlists[0]['id']
            self._save_last_played()
            await self.player_resume()
            self.state = "playing"
            self._start_polling()
        elif self._last_playlist_id:
            # Player was taken over — resume from last known track position
            track_index = self._find_track_index(
                self._last_playlist_id, self._last_track_uri)
            log.info("Activate: re-queuing %s from track %d (uri=%s)",
                     self._last_playlist_id, track_index,
                     self._last_track_uri or "none")
            await self._play_playlist(self._last_playlist_id, track_index=track_index)
        elif self.playlists:
            log.info("Activate: no history, starting first playlist")
            await self._play_playlist(self.playlists[0]['id'], track_index=0)

    def _find_track_index(self, playlist_id, track_uri):
        """Find the index of a track URI in a playlist. Returns 0 if not found."""
        if not track_uri:
            return 0
        for pl in self.playlists:
            if pl.get('id') == playlist_id:
                for i, track in enumerate(pl.get('tracks', [])):
                    if track.get('uri') == track_uri:
                        return i
                break
        return 0

    def _advance_track_uri(self, direction: int):
        """Update _last_track_uri by moving direction (+1/-1) in the playlist.

        Called immediately on next/prev so the position is tracked locally
        without waiting for a player poll (which may lag behind)."""
        if not self._last_playlist_id or not self._last_track_uri:
            return
        for pl in self.playlists:
            if pl.get('id') == self._last_playlist_id:
                tracks = pl.get('tracks', [])
                if not tracks:
                    return
                for i, track in enumerate(tracks):
                    if track.get('uri') == self._last_track_uri:
                        new_idx = i + direction
                        if 0 <= new_idx < len(tracks):
                            self._last_track_uri = tracks[new_idx].get('uri')
                        else:
                            # Beyond our local cache — clear so we don't
                            # broadcast stale metadata; the player's event
                            # will provide the correct track info.
                            self._last_track_uri = None
                        self._track_advanced_at = time.monotonic()
                        self._track_gen += 1
                        self._save_last_played()
                        return
                break

    def _lookup_track_meta(self, track_uri):
        """Return the cached track dict for a URI, or None."""
        if not track_uri or not self._last_playlist_id:
            return None
        for pl in self.playlists:
            if pl.get('id') == self._last_playlist_id:
                for track in pl.get('tracks', []):
                    if track.get('uri') == track_uri:
                        return track
                break
        return None

    async def _resolve_and_broadcast(self, track_uri, reason, canvas_url=""):
        """Broadcast media for track_uri using best available metadata.
        Tries: playlist cache → live player → _last_media fallback.

        ``track_uri`` is forwarded to the router so it can stamp a
        normalized ``track_id`` on the outgoing payload — this is what
        keeps the UI's canvas-vs-artwork cycle from going "no opinion"
        on source rebroadcasts (e.g. resync on activate after
        switching back to Spotify from another source)."""
        meta = self._lookup_track_meta(track_uri)
        if meta:
            await self.post_media_update(
                title=meta.get("name", ""),
                artist=meta.get("artist", ""),
                album=meta.get("album", ""),
                artwork=meta.get("image", ""),
                state="playing", reason=reason, canvas_url=canvas_url,
                track_uri=track_uri,
            )
            return
        fresh = await self._player_get("media")
        if fresh and fresh.get("title"):
            await self.post_media_update(
                title=fresh.get("title", ""),
                artist=fresh.get("artist", ""),
                album=fresh.get("album", ""),
                artwork=fresh.get("artwork", ""),
                state="playing", reason=reason, canvas_url=canvas_url,
                track_uri=track_uri,
            )
            return
        if self._last_media:
            media = {k: v for k, v in self._last_media.items() if k != "canvas_url"}
            await self.post_media_update(
                **media, state="playing", reason=reason, canvas_url=canvas_url,
                track_uri=track_uri,
            )

    async def _broadcast_current_track(self):
        """Pre-broadcast metadata for the current _last_track_uri.
        Broadcasts immediately with cached canvas, then fetches fresh canvas
        in background and re-broadcasts if found."""
        track_uri = self._last_track_uri
        cached_canvas = ""
        if track_uri:
            cached_canvas = self._canvas.get_cached(track_uri) or ""
        await self._resolve_and_broadcast(track_uri, "track_change", cached_canvas)
        if self._canvas.configured and track_uri:
            gen = self._track_gen
            self._spawn(
                self._fetch_and_broadcast_canvas(track_uri, gen),
                name="canvas_fetch")

    async def _fetch_and_broadcast_canvas(self, track_uri, gen):
        """Fetch canvas URL and re-broadcast media if found.
        gen: the _track_gen snapshot at task creation — aborts if stale."""
        try:
            url = await asyncio.wait_for(
                self._canvas.get_canvas_url(track_uri), timeout=5.0)
            if url:
                # Validate the track hasn't changed while we were fetching
                if self._track_gen == gen:
                    await self._resolve_and_broadcast(track_uri, "update", url)
                else:
                    log.debug("Canvas skipped — track generation changed (%d → %d)",
                              gen, self._track_gen)
        except asyncio.TimeoutError:
            log.warning("Canvas fetch timed out for %s", track_uri)
        except Exception as e:
            log.warning("Canvas fetch error: %s", e)

    async def handle_command(self, cmd, data) -> dict:
        # Captured by _handle_command_route before yielding — immune to
        # concurrent overwrites of self._action_ts.
        action_ts = data.get('_action_ts')

        if cmd == 'digit':
            digit = data.get('action', '0')
            playlist = self._get_digit_playlist(digit)
            if playlist:
                log.info("Digit %s -> playlist %s", digit, playlist.get('id'))
                await self._play_playlist(playlist['id'], action_ts=action_ts)
            else:
                log.info("No playlist mapped to digit %s", digit)

        elif cmd == 'play_playlist':
            playlist_id = data.get('playlist_id', '')
            track_index = data.get('track_index')
            shuffle = data.get('shuffle', False)
            if shuffle and track_index is None:
                # Shuffle: pick a random starting track
                for pl in self.playlists:
                    if pl.get('id') == playlist_id:
                        tracks = pl.get('tracks', [])
                        if tracks:
                            import random
                            track_index = random.randint(0, len(tracks) - 1)
                        break
            await self._play_playlist(playlist_id, track_index, action_ts=action_ts)

        elif cmd == 'play_track':
            uri = data.get('uri', '')
            await self._play_track(uri, action_ts=action_ts)

        elif cmd == 'shuffle':
            target = not self._shuffle_on
            if await self.player_set_shuffle(target):
                self._shuffle_on = target
                log.info("Shuffle toggled %s", "on" if target else "off")
            else:
                log.warning("Player does not support shuffle (or not active)")

        elif cmd == 'play_track_radio':
            uri = data.get('uri') or data.get('track_uri') or ''
            await self._play_track_radio(uri, action_ts=action_ts)

        elif cmd == 'play_index':
            index = data.get('index', 0)
            if self._last_playlist_id:
                log.info("play_index %d in playlist %s", index, self._last_playlist_id)
                # Reset debounce so play_index always works
                self._last_play_time = 0
                await self._play_playlist(self._last_playlist_id, track_index=index, action_ts=action_ts)
            else:
                log.warning("play_index but no active playlist")

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

    async def _play_playlist(self, playlist_id, track_index=None, action_ts=None):
        """Start playing a playlist, optionally at a specific track."""
        now = time.monotonic()
        if now - self._last_play_time < 2 and self._last_playlist_id == playlist_id:
            log.debug("Debounced duplicate play for %s", playlist_id)
            return
        self._last_play_time = now
        # Default to first track when none specified (GO on playlist)
        if track_index is None:
            track_index = 0
        # Look up the track's Spotify URI so the player can find it in the queue
        track_uri = None
        track_meta = None
        all_track_uris = None
        for pl in self.playlists:
            if pl.get('id') == playlist_id:
                tracks = pl.get('tracks', [])
                if 0 <= track_index < len(tracks):
                    track_meta = tracks[track_index]
                    track_uri = track_meta.get('uri', '')
                # Collect track URIs for individual queueing — avoids the
                # disruptive backfill that removes the playing track
                all_track_uris = [t['uri'] for t in tracks if t.get('uri')]
                break
        # Playlist URL for librespot context; track_uris handles Sonos queueing
        # For real Spotify playlists, pass the playlist URI so librespot
        # queues the entire playlist (preventing autoplay from kicking in
        # after the first track).  Liked Songs uses spotify:collection:tracks.
        if playlist_id and playlist_id.startswith(('liked', 'collection')):
            play_uri = "spotify:collection:tracks"
        elif playlist_id:
            play_uri = f"https://open.spotify.com/playlist/{playlist_id}"
        else:
            play_uri = self._spotify_uri_to_url(track_uri) if track_uri else None
        log.info("Play playlist %s (track_index=%s, track_uri=%s, track_uris=%s)",
                 playlist_id, track_index, track_uri,
                 f"{len(all_track_uris)} tracks" if all_track_uris else "none")
        # Register as playing FIRST so router accepts the media update
        self.state = "playing"
        self._last_playlist_id = playlist_id
        self._last_track_uri = track_uri
        self._track_gen += 1
        self._save_last_played()
        await self.register("playing", auto_power=True)
        # Pre-broadcast metadata for instant PLAYING view (source is now active)
        # Canvas is fetched in background to avoid blocking the UI transition
        if track_meta:
            cached_canvas = self._canvas.get_cached(track_uri) or "" if track_uri else ""
            await self.post_media_update(
                title=track_meta.get("name", ""),
                artist=track_meta.get("artist", ""),
                album=track_meta.get("album", ""),
                artwork=track_meta.get("image", ""),
                state="playing",
                reason="track_change",
                canvas_url=cached_canvas,
            )
            if self._canvas.configured and track_uri:
                self._spawn(
                    self._fetch_and_broadcast_canvas(track_uri, self._track_gen),
                    name="canvas_fetch")
        self._start_polling()
        ok = await self.player_play(
            uri=play_uri, track_uri=track_uri, track_uris=all_track_uris,
            action_ts=action_ts)
        if not ok:
            log.error("Player service failed to start playlist")

    async def _play_track(self, uri, action_ts=None):
        """Play a specific track."""
        url = self._spotify_uri_to_url(uri)
        log.info("Play track %s", url)
        # Register as playing BEFORE player call so UI transitions immediately
        self.state = "playing"
        await self.register("playing", auto_power=True)
        self._start_polling()
        ok = await self.player_play(uri=url, action_ts=action_ts)
        if not ok:
            log.error("Player service failed to start track")

    async def _play_track_radio(self, track_uri, action_ts=None):
        """Start track radio seeded by *track_uri*.

        Sonos uses an SMAPI station container URI; the local player passes
        ``spotify:station:track:<id>`` to go-librespot. Both paths go via
        player_play_track_radio — the player decides per its capabilities.
        """
        if not track_uri or "spotify:track:" not in track_uri:
            log.warning("play_track_radio: invalid track_uri %r", track_uri)
            return
        log.info("Play track radio seeded by %s", track_uri)
        self.state = "playing"
        self._last_track_uri = track_uri
        self._track_gen += 1
        await self.register("playing", auto_power=True)
        self._start_polling()
        ok = await self.player_play_track_radio(track_uri, action_ts=action_ts)
        if not ok:
            log.error("Player service failed to start track radio")

    async def get_queue(self, start=0, max_items=50) -> dict:
        """Return tracks from the last played Spotify playlist.
        Used when Spotify+local (source is queue authority for local player)."""
        if not self._last_playlist_id:
            return {"tracks": [], "current_index": -1, "total": 0}
        playlist = None
        for pl in self.playlists:
            if pl.get('id') == self._last_playlist_id:
                playlist = pl
                break
        if not playlist:
            return {"tracks": [], "current_index": -1, "total": 0}
        all_tracks = playlist.get('tracks', [])
        current_index = self._find_track_index(
            self._last_playlist_id, self._last_track_uri)
        end = min(start + max_items, len(all_tracks))
        tracks = []
        for i in range(start, end):
            t = all_tracks[i]
            tracks.append({
                "id": f"q:{i}",
                "title": t.get("name", ""),
                "artist": t.get("artist", ""),
                "album": "",
                "artwork": t.get("image", ""),
                "index": i,
                "current": i == current_index,
            })
        return {
            "tracks": tracks,
            "current_index": current_index,
            "total": len(all_tracks),
        }

    async def _toggle(self):
        if self.state == "playing":
            await self._pause()
        elif self.state == "paused":
            await self._resume()
        elif self.state == "stopped" and self.playlists:
            # Resume from last known position, or start first playlist
            if self._last_playlist_id:
                track_index = self._find_track_index(
                    self._last_playlist_id, self._last_track_uri)
                await self._play_playlist(self._last_playlist_id,
                                          track_index=track_index)
            else:
                await self._play_playlist(self.playlists[0]['id'])

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
            self._advance_track_uri(1)
            await self._broadcast_current_track()
            await asyncio.sleep(0.5)
            await self._poll_now_playing()
        else:
            log.warning("player_next() failed — command dropped")

    async def _prev(self):
        if await self.player_prev():
            self._advance_track_uri(-1)
            await self._broadcast_current_track()
            await asyncio.sleep(0.5)
            await self._poll_now_playing()
        else:
            log.warning("player_prev() failed — command dropped")

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
            # Callers (e.g. _handle_playlists) may have optimistically set
            # the fetching flag before spawning us — clear it or the UI
            # reports "loading" forever.
            self._fetching_playlists = False
            return  # don't bother — token is dead
        if getattr(self, '_refresh_running', False):
            log.info("Playlist refresh already running — skipping duplicate")
            return
        self._refresh_running = True
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
                # Log fetch.py output for diagnostics
                fetch_out = stdout.decode().strip()
                if fetch_out:
                    for line in fetch_out.splitlines()[-20:]:
                        log.info("fetch: %s", line.strip())
                old_playlists = self.playlists
                self._load_playlists()
                # Don't clobber a working cache with empty results (API/token error)
                if not self.playlists and old_playlists:
                    log.warning("Refresh returned 0 playlists — keeping %d cached", len(old_playlists))
                    self.playlists = old_playlists
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
            # wait_for cancels the communicate() await but does NOT kill the
            # child — an orphan with a full stdout pipe blocks forever and
            # its late JSON write races the next refresh.  Reap it.
            log.error("Playlist refresh timed out — killing fetch subprocess")
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        except Exception as e:
            log.error("Playlist refresh failed: %s", e)
        finally:
            self._fetching_playlists = False
            self._refresh_running = False

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

        self.auth.stop_keepalive()
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
        Also caches the current Spotify track URI for resume-from-position.
        """
        try:
            state = await self.player_state()
            if state == "playing":
                # Cache current track URI for resume-from-position on activate
                uri = await self.player_track_uri()
                if uri and uri.startswith("spotify:"):
                    if uri != self._last_track_uri:
                        # Ignore stale player reports right after a local
                        # next/prev advance — the player hasn't caught up yet
                        grace = time.monotonic() - self._track_advanced_at < 5.0
                        if grace:
                            log.debug("Poll ignoring stale player URI %s (advanced to %s %.1fs ago)",
                                      uri, self._last_track_uri,
                                      time.monotonic() - self._track_advanced_at)
                        else:
                            # Genuine auto-advance at end of song
                            self._last_track_uri = uri
                            self._track_gen += 1
                            self._save_last_played()
                            await self._broadcast_current_track()
                if self.state != "playing":
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

    async def _handle_setup_status(self, request):
        """Return auth status for both Web API and Spotify Connect."""
        spotify_status = await self.player_spotify_status()
        # If the player doesn't have go-librespot (e.g. Sonos), it's "remote"
        is_local = spotify_status.get("available", False)
        player_type = "local" if is_local else "remote"
        web_api_ready = self.auth.is_configured and not self.auth.revoked
        spotify_connect_ready = spotify_status.get("authenticated", False) if is_local else True
        return web.json_response({
            "setup_status": {
                "player_type": player_type,
                "web_api_ready": web_api_ready,
                "spotify_connect_ready": spotify_connect_ready,
                "setup_url": self._build_setup_url(),
            }
        }, headers=self._cors_headers())

    async def _handle_token(self, request):
        """Serve current access token to follower devices."""
        if not self.auth.is_configured:
            return web.json_response({"error": "not configured"}, status=503)
        try:
            token = await self.auth.get_token()
            remaining = max(0, int(self.auth._token_expiry - time.monotonic()))
            return web.json_response({
                "access_token": token,
                "expires_in": remaining,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=503)

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
            self._fetching_playlists = True  # set before spawn to prevent double-trigger
            log.info("Playlist view opened — refreshing in background")
            self._spawn(self._refresh_playlists(), name="refresh_playlists")

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
                <div class="step-title"><span class="step-number">1</span>Verify redirect URI</div>
                <div class="step-content">
                    <p>Make sure this redirect URI is registered in your
                    <a href="https://developer.spotify.com/dashboard" target="_blank">Spotify app settings</a>:</p>
                    <div class="uri-box">{redirect_uri}</div>
                </div>
            </div>
            <div class="step">
                <div class="step-title"><span class="step-number">2</span>{heading}</div>
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

            granted_scope = token_data.get('scope')
            if granted_scope:
                log.info("OAuth: Spotify granted scopes: %s", granted_scope)
                missing = missing_scopes(granted_scope, SPOTIFY_SCOPES)
                if missing:
                    log.warning("OAuth: user did not grant %s — some "
                                "features will be unavailable. Fix: revoke "
                                "at https://www.spotify.com/account/apps, "
                                "then re-auth via /setup",
                                missing)

            # Save tokens — try file first, fall back to in-memory only.
            # authorized_at anchors Spotify's 6-month refresh-token expiry
            # window, which runs from this original grant (not from
            # subsequent rotations).
            authorized_at = time.time()
            try:
                await loop.run_in_executor(
                    None, lambda: save_tokens(
                        client_id, rt, scope=granted_scope,
                        authorized_at=authorized_at))
                log.info("OAuth: tokens saved to disk")
            except Exception as e:
                log.warning("OAuth: could not save tokens to disk (%s) — using in-memory", e)

            # Load auth directly (works even if file save failed)
            self.auth.set_credentials(
                client_id, rt,
                access_token=token_data.get('access_token'),
                expires_in=token_data.get('expires_in', 3600),
                scope=granted_scope,
                authorized_at=authorized_at)
            self._detect_player()

            # Register as available now that we have credentials
            await self.register("available")

            self._spawn(self._fetch_display_name(), name="fetch_display_name")

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

            # Start keepalive to prevent PKCE refresh token expiry
            await self.auth.start_keepalive()

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
