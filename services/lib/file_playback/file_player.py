"""Local audio file playback via mpv."""

import asyncio
import json
import logging
import os
import random
import subprocess
from pathlib import Path

log = logging.getLogger('beo-usb')


class FilePlayer:
    """Controls audio file playback via mpv."""

    PAUSE_TIMEOUT = 300

    def __init__(self):
        self.process = None
        self.current_track = 0
        self.total_tracks = 0
        self.state = 'stopped'
        self.shuffle = False
        self.repeat = False
        self.folder_path = ""
        self.folder_name = ""
        self.tracks = []
        self._ipc_socket = '/tmp/beo-usb-mpv.sock'
        self._play_order = []
        self._watcher_task = None
        self._stopped_explicitly = False
        self._pause_timer = None
        self._on_track_end = None
        self._on_pause_timeout = None

    def load_tracks(self, track_paths, folder_name="", folder_path=""):
        """Load a list of file paths as the playlist."""
        self.tracks = [Path(p) if not isinstance(p, Path) else p for p in track_paths]
        self.total_tracks = len(self.tracks)
        self.folder_path = folder_path
        self.folder_name = folder_name
        self.current_track = 0
        self._tracks_meta = []  # BM5 metadata set externally after load
        if self.shuffle:
            self._rebuild_play_order()
        log.info("Loaded %d tracks: %s", self.total_tracks, folder_name)

    def load_folder(self, folder_rel_path, browser):
        """Build playlist from audio files in folder (plain browse compat)."""
        self.tracks = browser.get_audio_files(folder_rel_path)
        self.total_tracks = len(self.tracks)
        self.folder_path = folder_rel_path
        self.folder_name = Path(folder_rel_path).name if folder_rel_path else "USB"
        self.current_track = 0
        self._tracks_meta = []
        if self.shuffle:
            self._rebuild_play_order()

    async def play_track(self, index):
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
                'mpv', '--ao=pulse', filepath,
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
        loop = asyncio.get_running_loop()
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
                await asyncio.get_running_loop().run_in_executor(
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
