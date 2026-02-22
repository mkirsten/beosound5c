#!/usr/bin/env python3
"""
BeoSound 5c USB File Source (beo-source-usb)

Browses and plays audio files from local USB storage (e.g. the original
BeoMaster 5 internal SATA drive connected via USB adapter).

Port: 8773
"""

import asyncio
import json
import os
import subprocess
import sys
import logging
import random
from pathlib import Path

from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.audio_outputs import AudioOutputs
from lib.config import cfg
from lib.source_base import SourceBase
from lib.watchdog import watchdog_loop

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-usb')

AUDIO_EXTENSIONS = {'.flac', '.mp3', '.wma', '.aac', '.wav', '.m4a', '.ogg', '.opus'}
ARTWORK_NAMES = ['folder', 'cover', 'front']
ARTWORK_EXTS = ['.jpg', '.jpeg', '.png']


class FileBrowser:
    """Stateless directory listing for configured root paths."""

    def __init__(self, root_paths):
        self.roots = []
        for p in root_paths:
            rp = Path(p).resolve()
            if rp.is_dir():
                self.roots.append(rp)
                log.info("Root path: %s", rp)
            else:
                log.warning("Root path not found: %s", p)

    @property
    def available(self):
        return len(self.roots) > 0

    def list_directory(self, rel_path=""):
        """List a directory relative to the roots.

        Single root → top level shows root contents directly.
        Multiple roots → top level shows each root as a virtual folder.
        """
        if not rel_path:
            if len(self.roots) == 1:
                return self._list_real_dir(self.roots[0], "", None)
            # Multiple roots: virtual top level
            items = []
            for root in self.roots:
                items.append({
                    "type": "folder",
                    "name": root.name,
                    "path": root.name,
                    "artwork": self._find_artwork(root) is not None,
                })
            return {
                "path": "",
                "parent": None,
                "name": "USB",
                "artwork": False,
                "items": items,
            }

        resolved = self._resolve_path(rel_path)
        if not resolved:
            return None

        real_path, root = resolved
        # Build parent path
        if real_path == root:
            parent = "" if len(self.roots) > 1 else None
        else:
            parent_real = real_path.parent
            parent = self._to_rel_path(parent_real, root)

        return self._list_real_dir(real_path, rel_path, parent)

    def _list_real_dir(self, dir_path, rel_path, parent):
        """List contents of an actual filesystem directory."""
        folders = []
        files = []
        audio_index = 0

        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: e.name.lower())
        except OSError as e:
            log.error("Cannot list %s: %s", dir_path, e)
            return None

        for entry in entries:
            if entry.name.startswith('.'):
                continue
            if entry.is_dir():
                child_rel = f"{rel_path}/{entry.name}" if rel_path else entry.name
                folders.append({
                    "type": "folder",
                    "name": entry.name,
                    "path": child_rel,
                    "artwork": self._find_artwork(entry) is not None,
                })
            elif entry.is_file() and entry.suffix.lower() in AUDIO_EXTENSIONS:
                child_rel = f"{rel_path}/{entry.name}" if rel_path else entry.name
                files.append({
                    "type": "file",
                    "name": entry.name,
                    "path": child_rel,
                    "index": audio_index,
                })
                audio_index += 1

        return {
            "path": rel_path,
            "parent": parent,
            "name": dir_path.name if rel_path else "USB",
            "artwork": self._find_artwork(dir_path) is not None,
            "items": folders + files,
        }

    def _find_artwork(self, dir_path):
        """Find artwork image in directory (case-insensitive)."""
        try:
            names = {e.name.lower(): e for e in dir_path.iterdir() if e.is_file()}
        except OSError:
            return None
        for art_name in ARTWORK_NAMES:
            for ext in ARTWORK_EXTS:
                key = f"{art_name}{ext}"
                if key in names:
                    return names[key]
        return None

    def find_artwork_path(self, rel_path):
        """Resolve a relative path to an artwork file. Returns Path or None."""
        if not rel_path:
            if len(self.roots) == 1:
                return self._find_artwork(self.roots[0])
            return None
        resolved = self._resolve_path(rel_path)
        if not resolved:
            return None
        real_path, _ = resolved
        if real_path.is_dir():
            return self._find_artwork(real_path)
        return None

    def get_audio_files(self, rel_path):
        """Get sorted list of audio file Paths in a directory."""
        resolved = self._resolve_path(rel_path)
        if not resolved:
            return []
        real_path, _ = resolved
        if not real_path.is_dir():
            real_path = real_path.parent
        try:
            entries = sorted(real_path.iterdir(), key=lambda e: e.name.lower())
        except OSError:
            return []
        return [e for e in entries if e.is_file() and e.suffix.lower() in AUDIO_EXTENSIONS]

    def get_folder_for_file(self, rel_path):
        """Given a file's rel_path, return the folder rel_path."""
        parts = rel_path.rsplit('/', 1)
        return parts[0] if len(parts) > 1 else ""

    def _resolve_path(self, rel_path):
        """Resolve a relative path to (real_path, root). Prevents traversal."""
        if not rel_path:
            return None

        # For multi-root, first component selects root
        if len(self.roots) > 1:
            parts = rel_path.split('/', 1)
            root_name = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            for root in self.roots:
                if root.name == root_name:
                    target = (root / rest).resolve() if rest else root
                    if target.is_relative_to(root):
                        return (target, root) if target.exists() else None
                    return None
            return None

        # Single root
        root = self.roots[0]
        target = (root / rel_path).resolve()
        if not target.is_relative_to(root):
            return None
        return (target, root) if target.exists() else None

    def _to_rel_path(self, real_path, root):
        """Convert a real path back to a relative path string."""
        try:
            rel = real_path.relative_to(root)
            if len(self.roots) > 1:
                return f"{root.name}/{rel}" if str(rel) != '.' else root.name
            return str(rel) if str(rel) != '.' else ""
        except ValueError:
            return ""


class FilePlayer:
    """Controls audio file playback via mpv (modeled on CDPlayer)."""

    PAUSE_TIMEOUT = 300  # 5 minutes

    def __init__(self):
        self.process = None
        self.current_track = 0
        self.total_tracks = 0
        self.state = 'stopped'  # stopped | playing | paused
        self.shuffle = False
        self.repeat = False
        self.folder_path = ""
        self.folder_name = ""
        self.tracks = []  # list of Path objects
        self._ipc_socket = '/tmp/beo-usb-mpv.sock'
        self._play_order = []
        self._watcher_task = None
        self._stopped_explicitly = False
        self._pause_timer = None
        self._on_track_end = None
        self._on_pause_timeout = None

    def load_folder(self, folder_rel_path, browser):
        """Build playlist from audio files in folder."""
        self.tracks = browser.get_audio_files(folder_rel_path)
        self.total_tracks = len(self.tracks)
        self.folder_path = folder_rel_path
        self.folder_name = Path(folder_rel_path).name if folder_rel_path else "USB"
        self.current_track = 0
        if self.shuffle:
            self._rebuild_play_order()
        log.info("Loaded folder: %s (%d tracks)", self.folder_name, self.total_tracks)

    async def play_track(self, index):
        """Play a track by index (0-based)."""
        if index < 0 or index >= self.total_tracks:
            return
        self._stopped_explicitly = True
        await self.stop()
        self._stopped_explicitly = False
        self.current_track = index
        self._cancel_pause_timer()
        try:
            env = os.environ.copy()
            env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
            filepath = str(self.tracks[index])
            self.process = subprocess.Popen([
                'mpv',
                '--ao=pulse',
                filepath,
                '--no-video', '--no-terminal',
                f'--input-ipc-server={self._ipc_socket}',
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            self.state = 'playing'
            self._watcher_task = asyncio.create_task(self._watch_process())
            log.info("Playing [%d/%d] %s", index + 1, self.total_tracks, self.tracks[index].name)
        except Exception as e:
            log.error("Playback failed: %s", e)
            self.state = 'stopped'

    async def _watch_process(self):
        """Poll mpv process; fire track-end callback on natural exit."""
        try:
            while self.process and self.process.poll() is None:
                await asyncio.sleep(0.25)
            if not self._stopped_explicitly and self.state == 'playing':
                self.process = None
                self.state = 'stopped'
                log.info("Track %d ended naturally", self.current_track)
                if self._on_track_end:
                    await self._on_track_end()
        except asyncio.CancelledError:
            pass

    async def play(self):
        if self.state == 'paused':
            self._cancel_pause_timer()
            await self._mpv_command('cycle', 'pause')
            self.state = 'playing'
        elif self.state == 'stopped' and self.total_tracks > 0:
            await self.play_track(0)

    async def pause(self):
        if self.state == 'playing':
            await self._mpv_command('cycle', 'pause')
            self.state = 'paused'
            self._start_pause_timer()

    async def toggle_playback(self):
        if self.state == 'playing':
            await self.pause()
        else:
            await self.play()

    async def next_track(self):
        if self.shuffle and self._play_order:
            idx = self._play_order.index(self.current_track) if self.current_track in self._play_order else -1
            if idx < len(self._play_order) - 1:
                await self.play_track(self._play_order[idx + 1])
            elif self.repeat:
                self._rebuild_play_order()
                await self.play_track(self._play_order[0])
        elif self.current_track < self.total_tracks - 1:
            await self.play_track(self.current_track + 1)
        elif self.repeat:
            await self.play_track(0)

    async def prev_track(self):
        if self.shuffle and self._play_order:
            idx = self._play_order.index(self.current_track) if self.current_track in self._play_order else 0
            if idx > 0:
                await self.play_track(self._play_order[idx - 1])
        elif self.current_track > 0:
            await self.play_track(self.current_track - 1)

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        if self.shuffle:
            self._rebuild_play_order()
        log.info("Shuffle: %s", 'on' if self.shuffle else 'off')

    def toggle_repeat(self):
        self.repeat = not self.repeat
        log.info("Repeat: %s", 'on' if self.repeat else 'off')

    def _rebuild_play_order(self):
        self._play_order = list(range(self.total_tracks))
        random.shuffle(self._play_order)
        if self.current_track in self._play_order:
            self._play_order.remove(self.current_track)
            self._play_order.insert(0, self.current_track)

    def _start_pause_timer(self):
        self._cancel_pause_timer()
        loop = asyncio.get_event_loop()
        self._pause_timer = loop.call_later(
            self.PAUSE_TIMEOUT, lambda: asyncio.ensure_future(self._pause_timeout()))

    def _cancel_pause_timer(self):
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

    async def _pause_timeout(self):
        log.info("Pause timeout — stopping playback")
        await self.stop()
        if self._on_pause_timeout:
            await self._on_pause_timeout()

    async def stop(self):
        self._cancel_pause_timer()
        if self._watcher_task:
            self._watcher_task.cancel()
            self._watcher_task = None
        if self.process:
            self.process.terminate()
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.process.wait, 2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        self.state = 'stopped'

    async def _mpv_command(self, *args):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._mpv_command_sync, *args)

    def _mpv_command_sync(self, *args):
        import socket as sock
        s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
        try:
            s.connect(self._ipc_socket)
            cmd = json.dumps({'command': list(args)}) + '\n'
            s.sendall(cmd.encode())
        except Exception as e:
            log.error("mpv IPC error: %s", e)
        finally:
            s.close()

    def get_status(self):
        return {
            'state': self.state,
            'current_track': self.current_track,
            'total_tracks': self.total_tracks,
            'track_name': self.tracks[self.current_track].name if self.tracks and self.current_track < len(self.tracks) else '',
            'folder_name': self.folder_name,
            'folder_path': self.folder_path,
            'shuffle': self.shuffle,
            'repeat': self.repeat,
        }


class USBService(SourceBase):
    """USB file source — browse directories and play audio files."""

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
        paths_str = os.getenv('USB_MUSIC_PATHS', '/mnt/usb-music')
        self.root_paths = [p.strip() for p in paths_str.split(',') if p.strip()]
        self.browser = FileBrowser(self.root_paths)
        self.player = FilePlayer()
        self.audio = AudioOutputs()

    async def on_start(self):
        self.player._on_track_end = self._on_track_end
        self.player._on_pause_timeout = self._on_pause_timeout

        if self.browser.available:
            await self.register('available')
        else:
            log.warning("No root paths available — registering as gone")
            await self.register('gone')

        asyncio.create_task(self._set_default_airplay())

        # Start systemd watchdog heartbeat
        asyncio.create_task(watchdog_loop())

    async def on_stop(self):
        await self.player.stop()

    async def _set_default_airplay(self):
        """Set the default audio output to the local Sonos AirPlay sink."""
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

    def add_routes(self, app):
        app.router.add_get('/browse', self._handle_browse)
        app.router.add_get('/artwork', self._handle_artwork)

    async def handle_status(self) -> dict:
        return {
            'source': self.id,
            'available': self.browser.available,
            'roots': [str(r) for r in self.browser.roots],
            'playback': self.player.get_status(),
        }

    async def handle_resync(self) -> dict:
        if self.browser.available:
            state = self.player.state if self.player.state in ('playing', 'paused') else 'available'
            await self.register(state)
            if self.player.state in ('playing', 'paused'):
                await self._broadcast_update()
            return {'status': 'ok', 'resynced': True}
        return {'status': 'ok', 'resynced': False}

    async def handle_command(self, cmd, data) -> dict:
        if cmd == 'toggle':
            path = data.get('path')
            if self.player.state == 'stopped' and path:
                folder = self.browser.get_folder_for_file(path) if not Path(path).suffix == '' else path
                self.player.load_folder(folder, self.browser)
                await self.player.play_track(0)
                await self.register('playing')
            else:
                await self.player.toggle_playback()
                if self.player.state == 'playing':
                    await self.register('playing')
                else:
                    await self.register('paused')
            await self._broadcast_update()

        elif cmd == 'play_file':
            path = data.get('path', '')
            index = data.get('index', 0)
            folder = self.browser.get_folder_for_file(path)
            # Only reload folder if different
            if folder != self.player.folder_path:
                self.player.load_folder(folder, self.browser)
            await self.player.play_track(index)
            await self.register('playing')
            await self._broadcast_update()

        elif cmd == 'next':
            await self.player.next_track()
            if self.player.state == 'playing':
                await self._broadcast_update()

        elif cmd == 'prev':
            await self.player.prev_track()
            if self.player.state == 'playing':
                await self._broadcast_update()

        elif cmd == 'stop':
            await self.player.stop()
            await self.register('available')
            await self._broadcast_update()

        elif cmd == 'toggle_shuffle':
            self.player.toggle_shuffle()
            await self._broadcast_update()

        elif cmd == 'toggle_repeat':
            self.player.toggle_repeat()
            await self._broadcast_update()

        else:
            return {'status': 'error', 'message': f'Unknown: {cmd}'}

        return {'playback': self.player.get_status()}

    # ── HTTP API ──

    async def _handle_browse(self, request):
        path = request.query.get('path', '')
        result = self.browser.list_directory(path)
        if result is None:
            return web.json_response(
                {'error': 'Path not found'}, status=404,
                headers=self._cors_headers())
        return web.json_response(result, headers=self._cors_headers())

    async def _handle_artwork(self, request):
        path = request.query.get('path', '')
        artwork = self.browser.find_artwork_path(path)
        if artwork and artwork.is_file():
            ext = artwork.suffix.lower()
            ct = 'image/jpeg' if ext in ('.jpg', '.jpeg') else 'image/png'
            return web.Response(
                body=artwork.read_bytes(),
                content_type=ct,
                headers={
                    **self._cors_headers(),
                    'Cache-Control': 'public, max-age=3600',
                })
        return web.Response(status=404, headers=self._cors_headers())

    # ── Callbacks ──

    async def _on_track_end(self):
        old = self.player.current_track
        await self.player.next_track()
        if self.player.state == 'playing':
            log.info("Auto-advance: track %d → %d", old, self.player.current_track)
            await self._broadcast_update()
        else:
            log.info("Reached end of folder, deactivating USB source")
            await self.register('available')
            await self._broadcast_update()

    async def _on_pause_timeout(self):
        log.info("Pause timeout — deactivating USB source")
        await self.register('available')
        await self._broadcast_update()

    # ── Broadcast ──

    async def _broadcast_update(self):
        status = self.player.get_status()
        tracks_list = [
            {'name': t.name, 'index': i}
            for i, t in enumerate(self.player.tracks)
        ]
        artwork_url = None
        if self.player.folder_path:
            artwork_url = f"http://localhost:{self.port}/artwork?path={self.player.folder_path}"

        await self.broadcast('usb_update', {
            'state': status['state'],
            'current_track': status['current_track'],
            'total_tracks': status['total_tracks'],
            'track_name': status['track_name'],
            'folder_name': status['folder_name'],
            'folder_path': status['folder_path'],
            'artwork': self.browser.find_artwork_path(self.player.folder_path) is not None,
            'artwork_url': artwork_url,
            'tracks': tracks_list,
            'shuffle': status['shuffle'],
            'repeat': status['repeat'],
        })


if __name__ == '__main__':
    service = USBService()
    asyncio.run(service.run())
