#!/usr/bin/env python3
"""
BeoSound 5c CD Service (beo-cd)

Monitors USB CD/DVD drive, reads disc metadata from MusicBrainz,
manages playback via mpv, and discovers AirPlay speakers.
"""

import asyncio
import json
import os
import subprocess
import sys
import logging
from pathlib import Path
from aiohttp import web, ClientSession

# Optional imports with graceful fallback
try:
    import discid
    HAS_DISCID = True
except ImportError:
    HAS_DISCID = False

try:
    import musicbrainzngs
    HAS_MB = True
    musicbrainzngs.set_useragent("BeoSound5c", "1.0", "https://github.com/beosound5c")
except ImportError:
    HAS_MB = False

try:
    from zeroconf import ServiceBrowser, Zeroconf, ServiceStateChange
    HAS_ZEROCONF = True
except ImportError:
    HAS_ZEROCONF = False

# Shared library
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.audio_outputs import AudioOutputs
from lib.config import cfg
from lib.source_base import SourceBase
from lib.watchdog import watchdog_loop

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-cd')

# Configuration
CDROM_DEVICE = cfg("cd", "device", default="/dev/sr0")
BS5C_BASE_PATH = os.getenv('BS5C_BASE_PATH', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CD_CACHE_DIR = os.path.join(BS5C_BASE_PATH, 'web/assets/cd-cache')
POLL_INTERVAL = 2  # seconds


class CDDrive:
    """Monitors CD/DVD drive presence and disc insertion/ejection."""

    def __init__(self, device_path=CDROM_DEVICE):
        self.device_path = device_path
        self.drive_connected = False
        self.disc_inserted = False
        self._poll_task = None

    async def start_polling(self, on_drive_change, on_disc_change):
        self._on_drive_change = on_drive_change
        self._on_disc_change = on_disc_change
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self):
        while True:
            try:
                drive_present = Path(self.device_path).exists()
                disc_present = False

                if drive_present and HAS_DISCID:
                    # Audio CDs can't be probed with dd — use discid TOC read
                    try:
                        await asyncio.get_event_loop().run_in_executor(
                            None, lambda: discid.read(self.device_path)
                        )
                        disc_present = True
                    except Exception:
                        disc_present = False

                # Drive state change
                if drive_present != self.drive_connected:
                    self.drive_connected = drive_present
                    log.info(f"Drive {'connected' if drive_present else 'disconnected'}")
                    await self._on_drive_change(drive_present)

                # Disc state change
                if disc_present != self.disc_inserted:
                    self.disc_inserted = disc_present
                    log.info(f"Disc {'inserted' if disc_present else 'ejected'}")
                    await self._on_disc_change(disc_present)

            except Exception as e:
                log.error(f"Poll error: {e}")

            await asyncio.sleep(POLL_INTERVAL)

    async def eject(self):
        """Eject the disc."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: subprocess.run(['eject', self.device_path], timeout=5)
            )
            log.info("Disc ejected")
        except Exception as e:
            log.error(f"Eject failed: {e}")


class CDMetadata:
    """Fetches CD metadata from MusicBrainz + Cover Art Archive."""

    def __init__(self, device_path=CDROM_DEVICE, cache_dir=CD_CACHE_DIR):
        self.device_path = device_path
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_disc = None  # last discid.Disc object (for TOC offsets)

    async def lookup(self):
        """Read disc TOC and query MusicBrainz. Returns metadata dict or None."""
        if not HAS_DISCID:
            log.warning("python-discid not installed — skipping metadata lookup")
            return None

        try:
            disc = await asyncio.get_event_loop().run_in_executor(
                None, lambda: discid.read(self.device_path)
            )
            self.last_disc = disc
            disc_id = disc.id
            log.info(f"Disc ID: {disc_id}, tracks: {len(disc.tracks)}")

            if not HAS_MB:
                log.warning("musicbrainzngs not installed — using fallback metadata")
                return self._fallback_metadata(disc)

            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: musicbrainzngs.get_releases_by_discid(
                        disc_id, includes=['artists', 'recordings']
                    )
                )
            except Exception as e:
                log.warning(f"MusicBrainz lookup failed: {e}")
                return self._fallback_metadata(disc)

            if 'disc' not in result:
                log.warning(f"No MusicBrainz match for disc {disc_id}")
                return self._fallback_metadata(disc)

            release_list = result['disc']['release-list']
            release = release_list[0]
            artist = release.get('artist-credit-phrase', 'Unknown Artist')
            title = release.get('title', 'Unknown Album')
            date = release.get('date', '')[:4]
            release_id = release.get('id', '')

            tracks = []
            for medium in release.get('medium-list', []):
                for track in medium.get('track-list', []):
                    rec = track.get('recording', {})
                    length_ms = int(rec.get('length', 0) or 0)
                    mins = length_ms // 60000
                    secs = (length_ms % 60000) // 1000
                    tracks.append({
                        'num': int(track.get('position', 0)),
                        'title': rec.get('title', f'Track {track.get("position", "?")}'),
                        'duration': f'{mins}:{secs:02d}'
                    })

            artwork_path = await self._fetch_artwork(release_id, disc_id)
            back_artwork_path = await self._fetch_artwork(release_id, disc_id, 'back')

            # Build alternatives list (all releases except the chosen one)
            alternatives = []
            for rel in release_list[1:]:
                alt_artist = rel.get('artist-credit-phrase', 'Unknown Artist')
                alt_title = rel.get('title', 'Unknown Album')
                alt_date = rel.get('date', '')[:4]
                alternatives.append({
                    'release_id': rel.get('id', ''),
                    'artist': alt_artist,
                    'title': alt_title,
                    'year': alt_date
                })

            metadata = {
                'disc_id': disc_id,
                'release_id': release_id,
                'title': title,
                'artist': artist,
                'year': date,
                'album': f'{title} ({date})' if date else title,
                'tracks': tracks,
                'track_count': len(tracks) or len(disc.tracks),
                'artwork': artwork_path,
                'back_artwork': back_artwork_path,
                'alternatives': alternatives
            }
            log.info(f"Metadata: {artist} — {title} ({date}), {len(tracks)} tracks, "
                     f"{len(alternatives)} alternatives, back={'yes' if back_artwork_path else 'no'}")
            return metadata

        except Exception as e:
            log.error(f"Metadata lookup failed: {e}")
            return None

    def _fallback_metadata(self, disc):
        """Basic metadata from TOC when MusicBrainz has no match."""
        tracks = [{'num': i, 'title': f'Track {i}', 'duration': ''}
                  for i in range(1, len(disc.tracks) + 1)]
        return {
            'disc_id': disc.id,
            'release_id': '',
            'title': 'Unknown Album',
            'artist': 'Unknown Artist',
            'year': '',
            'album': 'Unknown Album',
            'tracks': tracks,
            'track_count': len(disc.tracks),
            'artwork': None,
            'back_artwork': None,
            'alternatives': []
        }

    async def _fetch_artwork(self, release_id, disc_id, side='front'):
        """Download cover art from Cover Art Archive. Returns web-relative path."""
        suffix = '' if side == 'front' else f'-{side}'
        cached = self.cache_dir / f'{disc_id}{suffix}.jpg'
        if cached.exists():
            return f'assets/cd-cache/{disc_id}{suffix}.jpg'

        try:
            async with ClientSession() as session:
                url = f'https://coverartarchive.org/release/{release_id}/{side}-1200'
                async with session.get(url, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        cached.write_bytes(data)
                        log.info(f"Artwork ({side}) cached: {cached}")
                        return f'assets/cd-cache/{disc_id}{suffix}.jpg'
                    else:
                        log.debug(f"No {side} artwork (HTTP {resp.status})")
                        return None
        except Exception as e:
            log.warning(f"Artwork ({side}) fetch failed: {e}")
            return None



class CDPlayer:
    """Controls CD playback via mpv with gapless chapter-based navigation.

    Launches mpv with cdda:// (whole disc) plus a generated chapters file
    from the disc TOC. Track changes use chapter seeking for gapless audio.
    """

    PAUSE_TIMEOUT = 300  # 5 minutes
    CHAPTERS_FILE = '/tmp/beo-cd-chapters.txt'

    def __init__(self, device_path=CDROM_DEVICE):
        self.device_path = device_path
        self.process = None
        self.current_track = 0
        self.total_tracks = 0
        self.track_offsets = []  # start time in seconds for each track (index 0 = track 1)
        self.state = 'stopped'  # stopped | playing | paused
        self.shuffle = False
        self.repeat = False
        self._ipc_socket = '/tmp/beo-cd-mpv.sock'
        self._play_order = []  # shuffled track order
        self._ipc_task = None
        self._ipc_reader = None
        self._ipc_writer = None
        self._pause_timer = None
        self._pending_track = None  # track we're seeking to (suppress stale events)
        self._volume = 100.0  # track mpv volume internally (avoids IPC read races)
        # Callbacks — set by CDService
        self._on_track_change = None  # track changed during playback (UI update)
        self._on_disc_end = None      # disc finished or shuffle order exhausted
        self._on_pause_timeout = None
        self._on_before_play = None

    # ── mpv lifecycle ──

    def _mpv_running(self):
        return self.process is not None and self.process.poll() is None

    def _write_chapters_file(self):
        """Generate OGM-style chapters file from disc TOC offsets."""
        if not self.track_offsets:
            return None
        with open(self.CHAPTERS_FILE, 'w') as f:
            for i, offset in enumerate(self.track_offsets):
                h = int(offset // 3600)
                m = int((offset % 3600) // 60)
                s = offset % 60
                f.write(f"CHAPTER{i+1:02d}={h:02d}:{m:02d}:{s:06.3f}\n")
                f.write(f"CHAPTER{i+1:02d}NAME=Track {i+1}\n")
        log.info(f"Chapters file written: {len(self.track_offsets)} tracks")
        return self.CHAPTERS_FILE

    async def _launch_mpv(self, start_track=1):
        """Launch mpv with cdda:// and a chapters file for track seeking."""
        if self._on_before_play:
            await self._on_before_play()

        try:
            os.unlink(self._ipc_socket)
        except FileNotFoundError:
            pass

        env = os.environ.copy()
        env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
        cmd = [
            'mpv', '--ao=pulse',
            f'--cdrom-device={self.device_path}',
            'cdda://',
            '--no-video', '--no-terminal',
            '--gapless-audio=yes',
            f'--input-ipc-server={self._ipc_socket}',
            f'--start=#{start_track}',
        ]
        chapters_file = self._write_chapters_file()
        if chapters_file:
            cmd.append(f'--chapters-file={chapters_file}')

        self.process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

        # Wait for IPC socket and connect
        connected = False
        for _ in range(50):  # up to 5 s
            await asyncio.sleep(0.1)
            if self.process.poll() is not None:
                raise RuntimeError("mpv exited immediately")
            if os.path.exists(self._ipc_socket):
                try:
                    self._ipc_reader, self._ipc_writer = \
                        await asyncio.open_unix_connection(self._ipc_socket)
                    connected = True
                    break
                except (ConnectionRefusedError, FileNotFoundError):
                    continue

        if not connected:
            raise RuntimeError("Could not connect to mpv IPC")

        await self._send_ipc({'command': ['observe_property', 1, 'chapter']})
        self._ipc_task = asyncio.create_task(self._read_ipc_events())
        self.current_track = start_track
        self.state = 'playing'
        self._volume = 100.0
        self._pending_track = None
        log.info(f"mpv launched — cdda:// with {len(self.track_offsets)} chapters, start track {start_track}")

    # ── IPC communication ──

    async def _send_ipc(self, cmd_obj):
        if not self._ipc_writer:
            return
        try:
            self._ipc_writer.write(json.dumps(cmd_obj).encode() + b'\n')
            await self._ipc_writer.drain()
        except Exception as e:
            log.error(f"mpv IPC send error: {e}")

    async def _close_ipc(self):
        if self._ipc_writer:
            try:
                self._ipc_writer.close()
                await self._ipc_writer.wait_closed()
            except Exception:
                pass
        self._ipc_reader = None
        self._ipc_writer = None

    async def _read_ipc_events(self):
        """Background task — reads mpv IPC events for chapter changes."""
        try:
            while self._ipc_reader:
                line = await self._ipc_reader.readline()
                if not line:
                    break  # EOF — mpv closed
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (msg.get('event') == 'property-change'
                        and msg.get('name') == 'chapter'):
                    chapter = msg.get('data')
                    if isinstance(chapter, int) and chapter >= 0:
                        await self._handle_track_change(chapter)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.debug(f"IPC reader ended: {e}")

        # mpv exited naturally (not via stop() which cancels this task first)
        if self.state not in ('playing', 'paused'):
            return
        self.process = None
        self.state = 'stopped'
        await self._close_ipc()

        # Repeat → restart the disc
        if self.repeat:
            try:
                start = self._play_order[0] if (self.shuffle and self._play_order) else 1
                await self._launch_mpv(start_track=start)
                log.info(f"Repeat → restarted from track {start}")
                if self._on_track_change:
                    await self._on_track_change()
                return
            except Exception as e:
                log.error(f"Repeat restart failed: {e}")

        log.info(f"Disc ended after track {self.current_track}")
        if self._on_disc_end:
            await self._on_disc_end()

    # ── Track navigation ──

    async def _handle_track_change(self, pos):
        """React to mpv's playlist-pos property change."""
        new_track = pos + 1  # 0-based → 1-based

        if new_track == self.current_track:
            return  # duplicate or initial observation

        # If a seek is pending, only accept the matching track
        if self._pending_track is not None:
            if new_track == self._pending_track:
                self._pending_track = None
                self.current_track = new_track
                log.info(f"Seek confirmed → track {new_track}")
                if self._on_track_change:
                    await self._on_track_change()
            return  # ignore non-matching events while seeking

        # Auto-advance — shuffle redirect
        if self.shuffle and self._play_order:
            nxt = self._next_shuffle_track()
            if nxt is not None:
                await self._seek_track(nxt)
            elif self.repeat:
                self._rebuild_play_order()
                await self._seek_track(self._play_order[0])
            else:
                log.info("Shuffle order complete")
                await self.stop()
                if self._on_disc_end:
                    await self._on_disc_end()
            return

        # Normal sequential advance
        self.current_track = new_track
        log.info(f"Gapless → track {new_track}")
        if self._on_track_change:
            await self._on_track_change()

    async def _seek_track(self, track_num):
        """Seek to a track (1-based) in the running mpv via chapter seeking."""
        self._pending_track = track_num
        self.current_track = track_num
        await self._send_ipc({'command': ['set_property', 'chapter', track_num - 1]})

    def _next_shuffle_track(self):
        if not self._play_order:
            return None
        try:
            idx = self._play_order.index(self.current_track)
            if idx < len(self._play_order) - 1:
                return self._play_order[idx + 1]
        except ValueError:
            pass
        return None

    # ── Public controls ──

    async def play_track(self, track_num):
        """Play a specific track. Reuses running mpv when possible."""
        self._cancel_pause_timer()
        if self._mpv_running() and self._ipc_writer:
            # mpv already running — just seek to the chapter
            self.state = 'playing'
            await self._send_ipc({'command': ['set_property', 'pause', False]})
            await self._seek_track(track_num)
        else:
            # Need to (re)launch mpv
            await self.stop()
            try:
                await self._launch_mpv(start_track=track_num)
            except Exception as e:
                log.error(f"Playback failed: {e}")
                self.state = 'stopped'

    async def play(self):
        if self.state == 'paused':
            self._cancel_pause_timer()
            if self._on_before_play:
                await self._on_before_play()
            await self._send_ipc({'command': ['set_property', 'pause', False]})
            self.state = 'playing'
        elif self.state == 'stopped':
            await self.play_track(self.current_track if self.current_track > 0 else 1)

    async def pause(self):
        if self.state == 'playing':
            await self._send_ipc({'command': ['set_property', 'pause', True]})
            self.state = 'paused'
            self._start_pause_timer()

    async def toggle_playback(self):
        if self.state == 'playing':
            await self.pause()
        else:
            await self.play()

    async def next_track(self):
        if not self._mpv_running():
            return
        if self.shuffle and self._play_order:
            nxt = self._next_shuffle_track()
            if nxt is not None:
                await self._seek_track(nxt)
            elif self.repeat:
                self._rebuild_play_order()
                await self._seek_track(self._play_order[0])
        elif self.current_track < self.total_tracks:
            await self._seek_track(self.current_track + 1)
        elif self.repeat:
            await self._seek_track(1)

    async def prev_track(self):
        if not self._mpv_running():
            return
        if self.shuffle and self._play_order:
            try:
                idx = self._play_order.index(self.current_track)
                if idx > 0:
                    await self._seek_track(self._play_order[idx - 1])
            except ValueError:
                pass
        elif self.current_track > 1:
            await self._seek_track(self.current_track - 1)

    # ── Shuffle / Repeat ──

    def toggle_shuffle(self):
        import random
        self.shuffle = not self.shuffle
        if self.shuffle:
            self._rebuild_play_order()
        log.info(f"Shuffle: {'on' if self.shuffle else 'off'}")

    def toggle_repeat(self):
        self.repeat = not self.repeat
        log.info(f"Repeat: {'on' if self.repeat else 'off'}")

    def _rebuild_play_order(self):
        import random
        self._play_order = list(range(1, self.total_tracks + 1))
        random.shuffle(self._play_order)
        if self.current_track in self._play_order:
            self._play_order.remove(self.current_track)
            self._play_order.insert(0, self.current_track)

    # ── Pause timer ──

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

    # ── Stop ──

    async def stop(self):
        self._cancel_pause_timer()
        if self._ipc_task:
            self._ipc_task.cancel()
            try:
                await self._ipc_task
            except asyncio.CancelledError:
                pass
            self._ipc_task = None
        await self._close_ipc()
        if self.process:
            self.process.terminate()
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.process.wait, 2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        self.state = 'stopped'
        self._pending_track = None

    async def fade_volume(self, target, duration=0.5, steps=10):
        """Smoothly fade mpv volume to target (0-100) over duration seconds."""
        if not self._ipc_writer:
            return
        current = self._volume
        step_delay = duration / steps
        for i in range(1, steps + 1):
            vol = current + (target - current) * (i / steps)
            await self._send_ipc({'command': ['set_property', 'volume', vol]})
            await asyncio.sleep(step_delay)
        self._volume = target

    def get_status(self):
        return {
            'state': self.state,
            'current_track': self.current_track,
            'total_tracks': self.total_tracks,
            'shuffle': self.shuffle,
            'repeat': self.repeat
        }


class CDService(SourceBase):
    """Main CD service — ties drive detection, metadata, AirPlay, and playback together."""

    id = "cd"
    name = "CD"
    port = 8769
    player = "local"
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
        "info": "announce",
        "track": "announce",
        "0": "play_track", "1": "play_track", "2": "play_track",
        "3": "play_track", "4": "play_track", "5": "play_track",
        "6": "play_track", "7": "play_track", "8": "play_track",
        "9": "play_track",
    }

    def __init__(self):
        super().__init__()
        self.drive = CDDrive()
        self.metadata_lookup = CDMetadata()
        self.audio = AudioOutputs()
        self.player = CDPlayer()
        self.metadata = None
        self._all_releases = []  # full release list from MusicBrainz
        self._rip_process = None
        self._is_first_detection = True  # skip autoplay on startup with disc already in
        self._external_drive_cache = None
        self._external_drive_cache_time = 0

    # ── SourceBase hooks ──

    async def on_start(self):
        # Wire player callbacks for gapless chapter tracking, disc end, and AirPlay
        self.player._on_track_change = self._on_track_change
        self.player._on_disc_end = self._on_disc_end
        self.player._on_pause_timeout = self._on_pause_timeout
        self.player._on_before_play = self._ensure_airplay

        await self.drive.start_polling(
            on_drive_change=self._on_drive_change,
            on_disc_change=self._on_disc_change
        )

        # Clear first-detection flag after grace period — if no disc was
        # found in the first 6s, the next insertion should autoplay
        asyncio.get_event_loop().call_later(6, self._clear_first_detection)

        # Start systemd watchdog heartbeat
        asyncio.create_task(watchdog_loop())

        asyncio.create_task(self._set_default_airplay())

    async def on_stop(self):
        await self.player.stop()
        await self.drive.stop()

    def _clear_first_detection(self):
        if self._is_first_detection:
            self._is_first_detection = False
            log.info("Startup grace ended — next disc will autoplay")

    def add_routes(self, app):
        app.router.add_get('/speakers', self._handle_speakers)

    async def handle_status(self) -> dict:
        return {
            'drive_connected': self.drive.drive_connected,
            'disc_inserted': self.drive.disc_inserted,
            'metadata': self.metadata,
            'playback': self.player.get_status(),
            'audio_outputs': self.audio.get_outputs(),
            'current_sink': self.audio.current_sink,
            'has_external_drive': self._detect_external_drive(),
            'ripping': self._rip_process is not None and self._rip_process.poll() is None,
            'capabilities': {
                'discid': HAS_DISCID,
                'musicbrainz': HAS_MB,
                'zeroconf': HAS_ZEROCONF
            }
        }

    async def handle_resync(self) -> dict:
        """Re-register source state and metadata. Called by input.py on new WebSocket client."""
        if self.drive.disc_inserted:
            state = self.player.state if self.player.state in ('playing', 'paused') else 'available'
            await self.register(state)
            if self.metadata:
                await self._broadcast_cd_update()
            log.info("Resync: state=%s, metadata=%s", state, self.metadata is not None)
            return {'status': 'ok', 'resynced': True}
        return {'status': 'ok', 'resynced': False}

    async def handle_raw_action(self, action, data):
        """Handle CD button before action_map."""
        # CD button → start playback if disc present
        if action == 'cd':
            return ('_cd_button', data)
        return None

    async def handle_command(self, cmd, data) -> dict:
        if cmd == '_cd_button':
            return await self._handle_cd_button_action()
        elif cmd == 'play':
            await self.player.play()
            await self.register('playing', auto_power=True)
            await self._broadcast_cd_update()
        elif cmd == 'pause':
            await self.player.pause()
            await self.register('paused')
            await self._broadcast_cd_update()
        elif cmd == 'toggle':
            await self.player.toggle_playback()
            if self.player.state == 'playing':
                await self.register('playing', auto_power=True)
            else:
                await self.register('paused')
            await self._broadcast_cd_update()
        elif cmd == 'next':
            await self.player.next_track()
            await self._broadcast_cd_update()
        elif cmd == 'prev':
            await self.player.prev_track()
            await self._broadcast_cd_update()
        elif cmd == 'stop':
            await self.player.stop()
            await self.register('available')
            await self._broadcast_cd_update()
        elif cmd == 'play_track':
            # Track number from action ("5") or explicit track field
            track = data.get('track') or int(data.get('action', 1))
            await self.player.play_track(track)
            await self.register('playing', auto_power=True)
            await self._broadcast_cd_update()
        elif cmd == 'eject':
            await self.player.stop()
            await self.register('available')
            await self.drive.eject()
            # _on_disc_change will send 'gone' when disc is actually ejected
        elif cmd == 'set_speaker':
            await self.audio.set_output(data.get('sink', ''))
        elif cmd == 'toggle_shuffle':
            self.player.toggle_shuffle()
            await self._broadcast_cd_update()
        elif cmd == 'toggle_repeat':
            self.player.toggle_repeat()
            await self._broadcast_cd_update()
        elif cmd == 'use_release':
            await self._use_alternative_release(data.get('release_id', ''))
        elif cmd == 'import':
            await self._start_rip()
        elif cmd == 'announce':
            await self._announce_track()
        else:
            return {'status': 'error', 'message': f'Unknown: {cmd}'}

        return {'playback': self.player.get_status()}

    # ── AirPlay default ──

    async def _set_default_airplay(self):
        """Set the default audio output to the local Sonos AirPlay sink."""
        sonos_ip = cfg("player", "ip", default="")
        if not sonos_ip:
            return
        # Wait for PipeWire to discover AirPlay sinks
        for _ in range(15):
            await asyncio.sleep(2)
            sink = self.audio.find_sink(ip=sonos_ip)
            if sink:
                await self.audio.set_output(sink['name'])
                log.info(f"Default AirPlay -> {sink['label']}")
                return
        log.warning(f"Sonos AirPlay sink for {sonos_ip} not found")

    async def _ensure_airplay(self):
        """Pre-play check: ensure the AirPlay sink is still alive."""
        sonos_ip = cfg("player", "ip", default="")
        if not sonos_ip:
            return
        ok = await self.audio.ensure_output(ip=sonos_ip)
        if not ok:
            log.warning("AirPlay sink not available — audio may play to wrong output")

    # ── Drive event handlers ──

    async def _on_drive_change(self, connected):
        pass  # Status is available via /status for the debug screen

    async def _on_disc_change(self, inserted):
        if inserted:
            is_startup = self._is_first_detection
            self._is_first_detection = False
            # Register as available — router adds menu item
            navigate = not is_startup  # navigate to CD view on real insertion
            await self.register('available', navigate=navigate)
            # Fetch metadata in background (autoplay only on real insertion)
            asyncio.create_task(self._fetch_and_update_metadata(autoplay=not is_startup))
        else:
            await self.player.stop()
            self.metadata = None
            # Unregister — router removes menu item and deactivates if active
            await self.register('gone')

    async def _on_track_change(self):
        """Called when the current track changes during gapless playback."""
        await self.register('playing')  # refresh router grace period
        await self._broadcast_cd_update()

    async def _on_disc_end(self):
        """Called when disc playback reaches the end."""
        log.info("Disc ended — deactivating CD source")
        await self.register('available')
        await self._broadcast_cd_update()

    async def _on_pause_timeout(self):
        """Called when paused too long — release drive, deactivate source."""
        log.info("Pause timeout — deactivating CD source")
        await self.register('available')
        await self._broadcast_cd_update()

    async def _fetch_and_update_metadata(self, autoplay=True):
        self.metadata = await self.metadata_lookup.lookup()
        # Save track offsets from disc TOC for chapter-based seeking
        disc = self.metadata_lookup.last_disc
        if disc:
            self.player.track_offsets = [t.offset / 75.0 for t in disc.tracks]
            self.player.total_tracks = len(disc.tracks)
            log.info(f"TOC offsets saved: {len(disc.tracks)} tracks")
            # If metadata lookup failed entirely, create minimal fallback
            if not self.metadata:
                self.metadata = {
                    'disc_id': disc.id,
                    'title': 'Unknown Album',
                    'artist': 'Unknown Artist',
                    'year': '',
                    'album': 'Unknown Album',
                    'tracks': [{'num': i, 'title': f'Track {i}', 'duration': ''}
                               for i in range(1, len(disc.tracks) + 1)],
                    'track_count': len(disc.tracks),
                    'artwork': None,
                    'back_artwork': None,
                    'alternatives': []
                }
        if self.metadata:
            self.player.total_tracks = self.metadata.get('track_count', 0)
            await self._broadcast_cd_update()
            if autoplay:
                await self.player.play_track(1)
                await self.register('playing', navigate=True, auto_power=True)
                artist = self.metadata.get('artist', '')
                album = self.metadata.get('title', '')
                if album and album != 'Unknown Album':
                    tts = f"{album}, by {artist}" if artist and artist != 'Unknown Artist' else album
                else:
                    tts = "Playing a CD"
                await self._announce_track(volume=70, text=tts)
                await self._broadcast_cd_update()

    async def _broadcast_cd_update(self):
        if not self.metadata:
            return
        await self.broadcast('cd_update', {
            'title': self.metadata.get('title', 'Unknown Album'),
            'artist': self.metadata.get('artist', 'Unknown Artist'),
            'album': self.metadata.get('album', ''),
            'year': self.metadata.get('year', ''),
            'artwork': self.metadata.get('artwork'),
            'back_artwork': self.metadata.get('back_artwork'),
            'tracks': self.metadata.get('tracks', []),
            'track_count': self.metadata.get('track_count', 0),
            'current_track': self.player.current_track,
            'state': self.player.state,
            'alternatives': self.metadata.get('alternatives', []),
            'shuffle': self.player.shuffle,
            'repeat': self.player.repeat,
            'has_external_drive': self._detect_external_drive()
        })

    async def _announce_track(self, volume=100, text=None):
        """Speak text over the CD audio via TTS overlay.

        Ducks the CD stream to 60% during TTS, then ramps back up.
        If text is None, announces the current track title + artist.
        """
        if not self.metadata or self.player.state != 'playing':
            log.info("Announce skipped — no metadata or not playing")
            return

        if text is None:
            track_num = self.player.current_track
            tracks = self.metadata.get('tracks', [])
            artist = self.metadata.get('artist', 'Unknown Artist')

            if tracks and 1 <= track_num <= len(tracks):
                title = tracks[track_num - 1].get('title', f'Track {track_num}')
            else:
                title = f'Track {track_num}'

            text = f"{title}, by {artist}"
        log.info(f"Announcing: {text}")

        tts_file = '/tmp/beo-tts.mp3'
        env = os.environ.copy()
        env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')

        # Duck CD volume
        await self.player.fade_volume(60, duration=0.5)

        # Generate and play TTS
        tts_proc = None
        if HAS_EDGE_TTS:
            try:
                communicate = edge_tts.Communicate(text, voice="en-US-AndrewNeural")
                await communicate.save(tts_file)
                tts_proc = subprocess.Popen(
                    ['mpv', '--ao=pulse', f'--volume={volume}', '--no-video', '--no-terminal', tts_file],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
                )
            except Exception as e:
                log.warning(f"edge-tts failed, trying espeak-ng: {e}")

        if tts_proc is None:
            # Fallback: espeak-ng (offline, robotic but reliable)
            try:
                espeak = subprocess.Popen(
                    ['espeak-ng', '-v', 'en-us', '-s', '160', '--stdout', text],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env
                )
                tts_proc = subprocess.Popen(
                    ['mpv', '--ao=pulse', f'--volume={volume}', '--no-video', '--no-terminal', '-'],
                    stdin=espeak.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
                )
            except Exception as e:
                log.error(f"TTS announce failed: {e}")

        # Wait for TTS to finish, then restore CD volume
        if tts_proc:
            await asyncio.get_event_loop().run_in_executor(None, tts_proc.wait)
        await self.player.fade_volume(100, duration=0.8)

    def _detect_external_drive(self):
        """Check if an external USB drive is mounted (for ripping). Cached for 30s."""
        import time as _time
        now = _time.monotonic()
        if now - self._external_drive_cache_time < 30:
            return self._external_drive_cache
        try:
            result = subprocess.run(
                ['lsblk', '-nro', 'MOUNTPOINT,TRAN'],
                capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.strip().split('\n'):
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == 'usb' and parts[0].startswith('/'):
                    self._external_drive_cache = parts[0]
                    self._external_drive_cache_time = now
                    return parts[0]
        except Exception:
            pass
        self._external_drive_cache = None
        self._external_drive_cache_time = now
        return None

    # ── CD button action ──

    async def _handle_cd_button_action(self):
        """Handle CD button press from remote — start playback if disc present."""
        if not self.drive.disc_inserted:
            return {'message': 'no disc'}

        if self.player.state == 'playing':
            return {'message': 'already playing'}

        # Start playback
        await self.player.play()
        await self.register('playing', navigate=True, auto_power=True)
        await self._broadcast_cd_update()
        return {'command': 'cd', 'playback': self.player.get_status()}

    async def _use_alternative_release(self, release_id):
        """Switch metadata to an alternative MusicBrainz release."""
        if not release_id or not self.metadata or not HAS_MB:
            return
        disc_id = self.metadata.get('disc_id', '')

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: musicbrainzngs.get_release_by_id(
                    release_id, includes=['artists', 'recordings']
                )
            )
            release = result.get('release', {})
            artist = release.get('artist-credit-phrase', 'Unknown Artist')
            title = release.get('title', 'Unknown Album')
            date = release.get('date', '')[:4]

            tracks = []
            for medium in release.get('medium-list', []):
                for track in medium.get('track-list', []):
                    rec = track.get('recording', {})
                    length_ms = int(rec.get('length', 0) or 0)
                    mins = length_ms // 60000
                    secs = (length_ms % 60000) // 1000
                    tracks.append({
                        'num': int(track.get('position', 0)),
                        'title': rec.get('title', f'Track {track.get("position", "?")}'),
                        'duration': f'{mins}:{secs:02d}'
                    })

            artwork_path = await self.metadata_lookup._fetch_artwork(release_id, disc_id)
            back_artwork_path = await self.metadata_lookup._fetch_artwork(release_id, disc_id, 'back')

            # Rebuild alternatives: move current to alts, remove selected from alts
            old_alts = self.metadata.get('alternatives', [])
            new_alts = [{'release_id': self.metadata.get('release_id', ''),
                         'artist': self.metadata.get('artist', ''),
                         'title': self.metadata.get('title', ''),
                         'year': self.metadata.get('year', '')}]
            new_alts += [a for a in old_alts if a['release_id'] != release_id]

            self.metadata = {
                'disc_id': disc_id,
                'release_id': release_id,
                'title': title,
                'artist': artist,
                'year': date,
                'album': f'{title} ({date})' if date else title,
                'tracks': tracks,
                'track_count': len(tracks),
                'artwork': artwork_path,
                'back_artwork': back_artwork_path,
                'alternatives': new_alts
            }
            self.player.total_tracks = len(tracks)
            log.info(f"Switched to: {artist} — {title}")
            await self._broadcast_cd_update()

        except Exception as e:
            log.error(f"Failed to switch release: {e}")

    async def _start_rip(self):
        """Rip the CD to an external USB drive using cdparanoia + lame."""
        mount = self._detect_external_drive()
        if not mount:
            log.warning("No external drive for ripping")
            return

        if self._rip_process and self._rip_process.poll() is None:
            log.warning("Rip already in progress")
            return

        artist = (self.metadata or {}).get('artist', 'Unknown')
        album = (self.metadata or {}).get('title', 'Unknown')
        # Sanitize for filesystem
        safe = lambda s: ''.join(c if c.isalnum() or c in ' -_' else '_' for c in s).strip()
        out_dir = Path(mount) / 'Music' / safe(artist) / safe(album)
        out_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"Starting rip to: {out_dir}")
        # Use cdparanoia to rip, then encode to FLAC
        self._rip_process = subprocess.Popen(
            ['bash', '-c',
             f'cd "{out_dir}" && cdparanoia -B -d {self.drive.device_path} '
             f'&& for f in *.wav; do flac "$f" && rm "$f"; done'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    async def _handle_speakers(self, request):
        return web.json_response(
            self.audio.get_outputs(),
            headers=self._cors_headers())


if __name__ == '__main__':
    service = CDService()
    asyncio.run(service.run())
