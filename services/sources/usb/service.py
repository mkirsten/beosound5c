#!/usr/bin/env python3
"""
BeoSound 5c USB File Source (beo-source-usb)

Browses and plays audio files from local USB storage. Supports:
- BeoMaster 5 drives (type: "bm5"): SQLite-powered library with
  Artist/Album/Genre browsing, cover art, and WMA->FLAC transcoding
- Plain USB drives: filesystem browsing as before

Port: 8773
"""

import asyncio
import logging
import os
import re
import sys
import urllib.parse
from pathlib import Path

from aiohttp import web

# Sibling imports (this directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Shared library (services/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib.audio_outputs import AudioOutputs
from lib.config import cfg
from lib.source_base import SourceBase
from lib.watchdog import watchdog_loop
from lib.file_playback import TranscodeCache, FilePlayer, RemotePlayer, AUDIO_EXTENSIONS

from bm5_library import BM5Library
from file_browser import FileBrowser
from mount_manager import MountManager

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-usb')


class USBService(SourceBase):
    """USB file source -- browse directories and play audio files."""

    id = "usb"
    name = "USB"
    port = 8773
    player = "local"
    action_map = {
        "play": "toggle",
        "pause": "toggle",
        "go": "toggle",
        "next": "next",
        "prev": "prev",
        "right": "next",
        "left": "prev",
        "stop": "stop",
    }

    def __init__(self):
        super().__init__()
        self.mount_manager = None
        self.transcode_cache = None
        self.file_player = FilePlayer()
        self.remote_player = None
        self._playback_mode = "local"  # "local" or "remote"
        self._current_mount_idx = 0
        self._current_track_meta = {}  # Rich metadata for current track
        self._device_ip = None
        self.audio = AudioOutputs()

    @property
    def _player(self):
        """Active player (FilePlayer or RemotePlayer)."""
        return self.remote_player if self._playback_mode == "remote" else self.file_player

    async def on_start(self):
        # Parse mount config
        menu = cfg("menu") or {}
        usb_cfg = None
        for v in menu.values():
            if isinstance(v, dict) and v.get("id") == "usb":
                usb_cfg = v
                break

        mounts_config = []
        if usb_cfg:
            if "mounts" in usb_cfg:
                mounts_config = usb_cfg["mounts"]
            elif "paths" in usb_cfg:
                # Backward compat: old "paths" format
                for p in usb_cfg["paths"]:
                    mounts_config.append({"name": Path(p).name, "path": p})

        if not mounts_config:
            # Fallback to env var
            paths_str = os.getenv('USB_MUSIC_PATHS', '')
            if paths_str:
                for p in paths_str.split(','):
                    p = p.strip()
                    if p:
                        mounts_config.append({"name": Path(p).name, "path": p})

        self.mount_manager = MountManager(mounts_config)
        await self.mount_manager.init()

        # Transcode cache — lossless FLAC for Bluesound, MP3 for Sonos
        player_type = cfg("player", "type", default="sonos")
        target_format = 'flac' if player_type == 'bluesound' else 'mp3'
        self.transcode_cache = TranscodeCache(target_format=target_format)
        self.transcode_cache.init()

        # Detect playback mode
        caps = await self.player_capabilities()
        if "url_stream" in caps:
            self._playback_mode = "remote"
            self.remote_player = RemotePlayer(self)
            self.remote_player._on_track_end = self._on_track_end
            self.remote_player._on_pause_timeout = self._on_pause_timeout
            self.remote_player._on_external_pause = self._on_external_pause
            self.remote_player._on_external_resume = self._on_external_resume
            self.remote_player._on_external_takeover = self._on_external_takeover
            log.info("Playback mode: remote (player supports url_stream)")
        else:
            self._playback_mode = "local"
            log.info("Playback mode: local (mpv)")

        self.file_player._on_track_end = self._on_track_end
        self.file_player._on_pause_timeout = self._on_pause_timeout

        # Determine device IP for stream URLs
        self._device_ip = await self._get_device_ip()

        if self.mount_manager.available:
            await self.register('available')
        else:
            log.warning("No mounts available — registering as gone")
            await self.register('gone')

        if self._playback_mode == "local":
            asyncio.create_task(self._set_default_airplay())

        asyncio.create_task(watchdog_loop())

    async def on_stop(self):
        await self.file_player.stop()
        if self.remote_player:
            await self.remote_player.stop()
        if self.transcode_cache:
            self.transcode_cache.cleanup()
        # Close BM5 databases
        for _, browser in (self.mount_manager.mounts if self.mount_manager else []):
            if isinstance(browser, BM5Library):
                browser.close()

    async def _get_device_ip(self):
        """Determine this device's IP address reachable by the player."""
        player_ip = cfg("player", "ip", default="")
        if not player_ip:
            return "localhost"
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((player_ip, 80))
            ip = s.getsockname()[0]
            s.close()
            log.info("Device IP for streaming: %s", ip)
            return ip
        except Exception:
            return "localhost"

    async def _set_default_airplay(self):
        sonos_ip = cfg("player", "ip", default="")
        if not sonos_ip:
            return
        for _ in range(15):
            await asyncio.sleep(2)
            sink = self.audio.find_sink(ip=sonos_ip)
            if sink:
                await self.audio.set_output(sink['name'])
                log.info("Default AirPlay -> %s", sink['label'])
                return
        log.warning("Sonos AirPlay sink for %s not found", sonos_ip)

    def build_stream_url(self, track_meta):
        """Build an HTTP stream URL for a track.
        Extension matches transcode target so the player detects the MIME type."""
        ext = self.transcode_cache.target_format if self.transcode_cache else 'mp3'
        track_id = track_meta.get('id')
        mount_idx = track_meta.get('mount_idx', self._current_mount_idx)
        if track_id:
            return f"http://{self._device_ip}:{self.port}/stream/track.{ext}?track_id={track_id}&mount={mount_idx}"
        file_path = track_meta.get('file_path')
        if file_path:
            return f"http://{self._device_ip}:{self.port}/stream/track.{ext}?path={urllib.parse.quote(file_path)}"
        return None

    def add_routes(self, app):
        app.router.add_get('/browse', self._handle_browse)
        app.router.add_get('/artwork', self._handle_artwork)
        app.router.add_get('/now_playing', self._handle_now_playing)
        app.router.add_get('/stream/{filename}', self._handle_stream)
        app.router.add_get('/stream', self._handle_stream)

    async def handle_status(self) -> dict:
        return {
            'source': self.id,
            'available': self.mount_manager.available if self.mount_manager else False,
            'mounts': [name for name, _ in (self.mount_manager.mounts if self.mount_manager else [])],
            'playback_mode': self._playback_mode,
            'playback': self._player.get_status(),
        }

    async def handle_resync(self) -> dict:
        if self.mount_manager and self.mount_manager.available:
            state = self._player.state if self._player.state in ('playing', 'paused') else 'available'
            await self.register(state)
            if self._player.state in ('playing', 'paused'):
                await self._broadcast_update()
            return {'status': 'ok', 'resynced': True}
        return {'status': 'ok', 'resynced': False}

    async def handle_command(self, cmd, data) -> dict:
        if cmd == 'toggle':
            path = data.get('path')
            if self._player.state == 'stopped' and path:
                await self._play_from_path(path, data)
            else:
                await self._player.toggle_playback()
                if self._player.state == 'playing':
                    await self.register('playing')
                else:
                    await self.register('paused')
            await self._broadcast_update()

        elif cmd == 'play_file':
            path = data.get('path', '')
            index = data.get('index', 0)
            mount_idx = data.get('mount', 0)
            self._current_mount_idx = mount_idx
            _, browser = self.mount_manager.get_mount(mount_idx) if self.mount_manager else (None, None)
            if browser and isinstance(browser, FileBrowser):
                folder = path.rsplit('/', 1)[0] if '/' in path else ""
                if self._playback_mode == "local":
                    if folder != self.file_player.folder_path:
                        self.file_player.load_folder(folder, browser)
                    await self.file_player.play_track(index)
                else:
                    # Build track list from folder for remote
                    files = browser.get_audio_files(folder)
                    tracks_meta = [{'file_path': str(f), 'title': f.stem, 'id': None, 'mount_idx': mount_idx}
                                   for f in files]
                    self.remote_player.load_tracks(tracks_meta, folder_name=Path(folder).name if folder else "USB")
                    if not await self.remote_player.play_track(index):
                        return {'status': 'error', 'message': 'Playback failed'}
            await self.register('playing')
            await self._broadcast_update()

        elif cmd == 'play_track':
            track_id = data.get('track_id')
            album_id = data.get('album_id')
            mount_idx = data.get('mount', 0)
            self._current_mount_idx = mount_idx
            if await self._play_bm5_track(track_id, album_id, mount_idx):
                await self.register('playing', auto_power=True)
                await self._broadcast_update()
            else:
                return {'status': 'error', 'message': 'Track/album not found'}

        elif cmd == 'play_album':
            album_id = data.get('album_id')
            mount_idx = data.get('mount', 0)
            self._current_mount_idx = mount_idx
            if await self._play_bm5_album(album_id, mount_idx):
                await self.register('playing', auto_power=True)
                await self._broadcast_update()
            else:
                return {'status': 'error', 'message': 'Album not found'}

        elif cmd == 'next':
            await self._player.next_track()
            if self._player.state == 'playing':
                self._update_current_track_meta()
                await self._broadcast_update()

        elif cmd == 'prev':
            await self._player.prev_track()
            if self._player.state == 'playing':
                self._update_current_track_meta()
                await self._broadcast_update()

        elif cmd == 'stop':
            await self._player.stop()
            await self.register('available')
            await self._broadcast_update()

        elif cmd == 'toggle_shuffle':
            self._player.toggle_shuffle()
            await self._broadcast_update()

        elif cmd == 'toggle_repeat':
            self._player.toggle_repeat()
            await self._broadcast_update()

        else:
            return {'status': 'error', 'message': f'Unknown: {cmd}'}

        return {'playback': self._player.get_status()}

    # -- BM5 playback helpers --

    async def _play_bm5_track(self, track_id, album_id, mount_idx):
        """Play a specific track from a BM5 library, queueing album siblings.
        Returns True if playback started successfully."""
        bm5 = self._get_bm5(mount_idx)
        if not bm5:
            return False

        # Get album tracks
        aid = album_id or self._album_id_for_track(bm5, track_id)
        if not aid:
            return False
        album_tracks = bm5.get_album_tracks(aid)
        if not album_tracks:
            return False

        # Build track metadata for the player
        tracks_meta = []
        start_index = 0
        for i, t in enumerate(album_tracks):
            fp = bm5.get_track_file_path(t['id'])
            tracks_meta.append({
                'id': str(t['id']),
                'title': t['title'] or f"Track {t['index_']}",
                'artist': t['artist'] or t['album_artist'],
                'album': t['album_title'],
                'album_id': str(t['album_id']),
                'year': t['year'],
                'genre': t.get('genre', ''),
                'track_number': t['index_'],
                'duration': t['duration'],
                'file_path': fp,
                'mount_idx': mount_idx,
            })
            if str(t['id']) == str(track_id):
                start_index = i

        if self._playback_mode == "remote":
            self.remote_player.load_tracks(
                tracks_meta,
                album_name=album_tracks[0]['album_title'],
                album_artist=album_tracks[0]['album_artist'] or '',
            )
            if not await self.remote_player.play_track(start_index):
                return False
        else:
            file_paths = [t['file_path'] for t in tracks_meta if t['file_path']]
            self.file_player.load_tracks(
                file_paths,
                folder_name=album_tracks[0]['album_title'],
            )
            # Store metadata for _update_current_track_meta on track changes
            self.file_player._tracks_meta = tracks_meta
            await self.file_player.play_track(start_index)

        self._current_track_meta = tracks_meta[start_index] if tracks_meta else {}
        return True

    async def _play_bm5_album(self, album_id, mount_idx):
        """Play an entire album from track 1."""
        return await self._play_bm5_track(None, album_id, mount_idx)

    def _album_id_for_track(self, bm5, track_id):
        """Look up the album_id for a track."""
        t = bm5.get_track(track_id)
        return str(t['album_id']) if t else None

    def _get_bm5(self, mount_idx):
        """Get the BM5Library for a mount index."""
        if not self.mount_manager:
            return None
        _, browser = self.mount_manager.get_mount(mount_idx)
        if isinstance(browser, BM5Library):
            return browser
        return None

    def _update_current_track_meta(self):
        """Update current track metadata from the active player's track list."""
        p = self._player
        if isinstance(p, RemotePlayer) and p.tracks and p.current_track < len(p.tracks):
            self._current_track_meta = p.tracks[p.current_track]
        elif isinstance(p, FilePlayer) and hasattr(p, '_tracks_meta') and p.current_track < len(p._tracks_meta):
            self._current_track_meta = p._tracks_meta[p.current_track]

    async def _play_from_path(self, path, data):
        """Play from a browse path (backward compat for plain folders)."""
        mount_idx = data.get('mount', 0)
        self._current_mount_idx = mount_idx
        _, browser = self.mount_manager.get_mount(mount_idx) if self.mount_manager else (None, None)
        if browser and isinstance(browser, FileBrowser):
            folder = path if not Path(path).suffix else path.rsplit('/', 1)[0] if '/' in path else ""
            self.file_player.load_folder(folder, browser)
            await self.file_player.play_track(0)
            await self.register('playing')

    # -- HTTP Handlers --

    async def _handle_browse(self, request):
        path = request.query.get('path', '')
        mount_idx = int(request.query.get('mount', '0'))

        if not self.mount_manager or not self.mount_manager.available:
            return web.json_response({'error': 'No mounts available'}, status=404,
                                     headers=self._cors_headers())

        # Single mount -> skip mount selector
        if len(self.mount_manager.mounts) == 1:
            mount_idx = 0

        # Root level with multiple mounts -> show mount list
        if not path and len(self.mount_manager.mounts) > 1:
            items = []
            for i, (name, browser) in enumerate(self.mount_manager.mounts):
                items.append({
                    "type": "category" if isinstance(browser, BM5Library) else "folder",
                    "name": name,
                    "id": str(i),
                    "path": str(i),
                    "icon": "database" if isinstance(browser, BM5Library) else "folder",
                    "mount": i,
                })
            return web.json_response({
                "path": "",
                "parent": None,
                "name": "USB",
                "items": items,
            }, headers=self._cors_headers())

        # Route to the right mount
        # If path starts with a number and we have multiple mounts, split mount index from path
        if len(self.mount_manager.mounts) > 1 and path:
            parts = path.split('/', 1)
            try:
                mount_idx = int(parts[0])
                path = parts[1] if len(parts) > 1 else ""
            except ValueError:
                pass

        _, browser = self.mount_manager.get_mount(mount_idx)
        if not browser:
            return web.json_response({'error': 'Mount not found'}, status=404,
                                     headers=self._cors_headers())

        result = browser.browse(path)
        if result is None:
            return web.json_response({'error': 'Path not found'}, status=404,
                                     headers=self._cors_headers())

        # Inject mount index into paths for multi-mount routing
        if len(self.mount_manager.mounts) > 1:
            result['mount'] = mount_idx
            if result.get('parent') is not None:
                if result['parent'] == '':
                    pass  # Root parent stays empty
                else:
                    result['parent'] = f"{mount_idx}/{result['parent']}"
            if result['path']:
                result['path'] = f"{mount_idx}/{result['path']}"
            for item in result.get('items', []):
                if 'path' in item and item['path']:
                    item['path'] = f"{mount_idx}/{item['path']}"
                item['mount'] = mount_idx

        return web.json_response(result, headers=self._cors_headers())

    async def _handle_artwork(self, request):
        mount_idx = int(request.query.get('mount', '0'))
        album_id = request.query.get('album_id')
        artist_id = request.query.get('artist_id')
        path = request.query.get('path', '')

        # BM5 artwork by album/artist ID
        bm5 = self._get_bm5(mount_idx)
        if bm5:
            artwork_path = None
            if album_id:
                artwork_path = bm5.get_album_artwork_path(album_id)
            elif artist_id:
                artwork_path = bm5.get_artist_artwork_path(artist_id)

            if artwork_path:
                p = Path(artwork_path)
                if p.is_file():
                    ext = p.suffix.lower()
                    ct = 'image/jpeg' if ext in ('.jpg', '.jpeg') else 'image/png'
                    return web.Response(
                        body=p.read_bytes(),
                        content_type=ct,
                        headers={**self._cors_headers(), 'Cache-Control': 'public, max-age=3600'})

        # Filesystem artwork by path
        if path:
            _, browser = self.mount_manager.get_mount(mount_idx) if self.mount_manager else (None, None)
            if isinstance(browser, FileBrowser):
                artwork = browser.find_artwork_path(path)
                if artwork and artwork.is_file():
                    ext = artwork.suffix.lower()
                    ct = 'image/jpeg' if ext in ('.jpg', '.jpeg') else 'image/png'
                    return web.Response(
                        body=artwork.read_bytes(),
                        content_type=ct,
                        headers={**self._cors_headers(), 'Cache-Control': 'public, max-age=3600'})

        return web.Response(status=404, headers=self._cors_headers())

    async def _handle_stream(self, request):
        """Serve audio files for Sonos/BlueSound consumption.
        Transcodes WMA->FLAC on the fly; serves compatible formats directly.
        Supports HTTP Range requests (required by Sonos)."""
        track_id = request.query.get('track_id')
        mount_idx = int(request.query.get('mount', '0'))
        raw_path = request.query.get('path')

        file_path = None
        if track_id:
            bm5 = self._get_bm5(mount_idx)
            if bm5:
                file_path = bm5.get_track_file_path(track_id)
        elif raw_path:
            # Validate path is within a known mount (prevent path traversal)
            resolved = Path(raw_path).resolve()
            if self.mount_manager:
                for _, browser in self.mount_manager.mounts:
                    mount_root = getattr(browser, 'mount', None) or getattr(browser, 'root', None)
                    if mount_root and resolved.is_relative_to(Path(mount_root)):
                        file_path = raw_path
                        break

        if not file_path or not Path(file_path).is_file():
            return web.Response(status=404, text="Track not found",
                                headers=self._cors_headers())

        # Transcode if needed
        streamable_path = await self.transcode_cache.get_or_transcode(file_path)
        if not streamable_path or not Path(streamable_path).is_file():
            return web.Response(status=500, text="Transcode failed",
                                headers=self._cors_headers())

        # Determine content type
        ext = Path(streamable_path).suffix.lower()
        content_types = {
            '.flac': 'audio/flac',
            '.mp3': 'audio/mpeg',
            '.ogg': 'audio/ogg',
            '.wav': 'audio/wav',
        }
        ct = content_types.get(ext, 'application/octet-stream')
        file_size = Path(streamable_path).stat().st_size

        # Handle Range requests (Sonos requires this)
        range_header = request.headers.get('Range')
        try:
            if range_header:
                # Parse "bytes=start-end"
                range_match = re.match(r'bytes=(\d+)-(\d*)', range_header)
                if range_match:
                    start = int(range_match.group(1))
                    end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
                    end = min(end, file_size - 1)
                    length = end - start + 1

                    resp = web.StreamResponse(
                        status=206,
                        headers={
                            'Content-Type': ct,
                            'Content-Length': str(length),
                            'Content-Range': f'bytes {start}-{end}/{file_size}',
                            'Accept-Ranges': 'bytes',
                            **self._cors_headers(),
                        },
                    )
                    await resp.prepare(request)
                    with open(streamable_path, 'rb') as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(65536, remaining))
                            if not chunk:
                                break
                            await resp.write(chunk)
                            remaining -= len(chunk)
                    await resp.write_eof()
                    return resp

            # Full file response
            resp = web.StreamResponse(
                headers={
                    'Content-Type': ct,
                    'Content-Length': str(file_size),
                    'Accept-Ranges': 'bytes',
                    **self._cors_headers(),
                },
            )
            await resp.prepare(request)
            with open(streamable_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    await resp.write(chunk)
            await resp.write_eof()
            return resp
        except (ConnectionResetError, ConnectionError, BrokenPipeError):
            # Sonos probes the URL then disconnects before reconnecting -- harmless
            log.debug("Stream client disconnected (probe)")
            return resp

    # -- Callbacks --

    async def _on_track_end(self):
        old = self._player.current_track
        await self._player.next_track()
        if self._player.state == 'playing':
            self._update_current_track_meta()
            log.info("Auto-advance: track %d -> %d", old, self._player.current_track)
            await self._broadcast_update()
        else:
            log.info("Reached end of playlist, deactivating USB source")
            await self.register('available')
            await self._broadcast_update()

    async def _on_pause_timeout(self):
        log.info("Pause timeout — deactivating USB source")
        await self.register('available')
        await self._broadcast_update()

    async def _on_external_pause(self):
        log.info("External pause detected — updating USB state")
        await self.register('paused')
        await self._broadcast_update()

    async def _on_external_resume(self):
        log.info("External resume detected — updating USB state")
        await self.register('playing')
        await self._broadcast_update()

    async def _on_external_takeover(self):
        log.info("External takeover detected — deactivating USB source")
        await self.register('available')
        await self._broadcast_update()

    # -- Now-playing / Broadcast --

    def _build_now_playing(self) -> dict:
        """Build the now-playing data dict used by broadcasts and the API."""
        status = self._player.get_status()

        meta = self._current_track_meta
        track_name = meta.get('title') or status.get('track_name', '')
        artist = meta.get('artist') or status.get('artist', '')
        album = meta.get('album') or status.get('album', '')
        year = meta.get('year')
        genre = meta.get('genre', '')

        artwork_url = None
        album_id = meta.get('album_id')
        if album_id:
            artwork_url = f"http://{self._device_ip}:{self.port}/artwork?mount={self._current_mount_idx}&album_id={album_id}"
        elif isinstance(self._player, FilePlayer) and self._player.folder_path:
            artwork_url = f"http://localhost:{self.port}/artwork?path={urllib.parse.quote(self._player.folder_path)}"

        tracks_list = []
        if isinstance(self._player, RemotePlayer) and self._player.tracks:
            tracks_list = [
                {'name': t.get('title', ''), 'index': i, 'id': t.get('id')}
                for i, t in enumerate(self._player.tracks)
            ]
        elif isinstance(self._player, FilePlayer) and self._player.tracks:
            tracks_list = [
                {'name': t.name, 'index': i}
                for i, t in enumerate(self._player.tracks)
            ]

        return {
            'state': status['state'],
            'current_track': status['current_track'],
            'total_tracks': status['total_tracks'],
            'track_name': track_name,
            'artist': artist,
            'album': album,
            'folder_name': status.get('folder_name', artist or album),
            'folder_path': status.get('folder_path', ''),
            'artwork': artwork_url is not None,
            'artwork_url': artwork_url,
            'year': year,
            'genre': genre,
            'tracks': tracks_list,
            'shuffle': status['shuffle'],
            'repeat': status['repeat'],
        }

    async def _handle_now_playing(self, request):
        """GET /now_playing — returns the same data as usb_update broadcasts."""
        return web.json_response(self._build_now_playing())

    async def _broadcast_update(self):
        await self.broadcast('usb_update', self._build_now_playing())


if __name__ == '__main__':
    service = USBService()
    asyncio.run(service.run())
