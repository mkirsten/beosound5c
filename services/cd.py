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
import signal
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

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-cd')

# Configuration
CDROM_DEVICE = os.getenv('CDROM_DEVICE', '/dev/sr0')
INPUT_WEBHOOK_URL = 'http://localhost:8767/webhook'
BS5C_BASE_PATH = os.getenv('BS5C_BASE_PATH', '/home/kirsten/beosound5c')
CD_CACHE_DIR = os.path.join(BS5C_BASE_PATH, 'web/assets/cd-cache')
HTTP_PORT = 8769
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

                if drive_present:
                    # Check if a disc is readable via cdparanoia query or dd probe
                    try:
                        result = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: subprocess.run(
                                ['dd', f'if={self.device_path}', 'of=/dev/null',
                                 'bs=2048', 'count=1'],
                                capture_output=True, timeout=5
                            )
                        )
                        disc_present = result.returncode == 0
                    except (subprocess.TimeoutExpired, Exception):
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

    def eject(self):
        """Eject the disc."""
        try:
            subprocess.run(['eject', self.device_path], timeout=5)
            log.info("Disc ejected")
        except Exception as e:
            log.error(f"Eject failed: {e}")


class CDMetadata:
    """Fetches CD metadata from MusicBrainz + Cover Art Archive."""

    def __init__(self, device_path=CDROM_DEVICE, cache_dir=CD_CACHE_DIR):
        self.device_path = device_path
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def lookup(self):
        """Read disc TOC and query MusicBrainz. Returns metadata dict or None."""
        if not HAS_DISCID:
            log.warning("python-discid not installed — skipping metadata lookup")
            return None
        if not HAS_MB:
            log.warning("musicbrainzngs not installed — skipping metadata lookup")
            return None

        try:
            disc = await asyncio.get_event_loop().run_in_executor(
                None, lambda: discid.read(self.device_path)
            )
            disc_id = disc.id
            log.info(f"Disc ID: {disc_id}, tracks: {len(disc.tracks)}")

            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: musicbrainzngs.get_releases_by_discid(
                    disc_id, includes=['artists', 'recordings']
                )
            )

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
            'title': 'Unknown Album',
            'artist': 'Unknown Artist',
            'year': '',
            'album': 'Unknown Album',
            'tracks': tracks,
            'track_count': len(disc.tracks),
            'artwork': None
        }

    async def _fetch_artwork(self, release_id, disc_id, side='front'):
        """Download cover art from Cover Art Archive. Returns web-relative path."""
        suffix = '' if side == 'front' else f'-{side}'
        cached = self.cache_dir / f'{disc_id}{suffix}.jpg'
        if cached.exists():
            return f'assets/cd-cache/{disc_id}{suffix}.jpg'

        try:
            async with ClientSession() as session:
                url = f'https://coverartarchive.org/release/{release_id}/{side}-500'
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


class AudioOutputs:
    """Lists and switches audio outputs via PipeWire/PulseAudio.

    PipeWire's RAOP module discovers AirPlay speakers automatically
    and exposes them as regular sinks alongside local outputs (HDMI, etc).
    No separate Zeroconf needed.
    """

    def __init__(self):
        self.current_sink = None

    def get_outputs(self):
        """List all available audio sinks with friendly names."""
        try:
            # Get sink list: ID, Name, Driver, SampleSpec, State
            short = subprocess.run(
                ['pactl', 'list', 'sinks', 'short'],
                capture_output=True, text=True, timeout=3
            )
            # Get descriptions via full listing
            full = subprocess.run(
                ['pactl', 'list', 'sinks'],
                capture_output=True, text=True, timeout=3
            )
            # Get default sink
            default = subprocess.run(
                ['pactl', 'get-default-sink'],
                capture_output=True, text=True, timeout=3
            ).stdout.strip()

            # Parse descriptions from full output
            descriptions = {}
            current_name = None
            for line in full.stdout.split('\n'):
                line = line.strip()
                if line.startswith('Name:'):
                    current_name = line.split(':', 1)[1].strip()
                elif line.startswith('Description:') and current_name:
                    descriptions[current_name] = line.split(':', 1)[1].strip()

            outputs = []
            for line in short.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.split('\t')
                if len(parts) < 2:
                    continue
                sink_name = parts[1]

                # Skip dummy/null sinks
                if 'null' in sink_name.lower():
                    continue

                description = descriptions.get(sink_name, sink_name)
                is_airplay = sink_name.startswith('raop_sink.')
                is_active = sink_name == default

                outputs.append({
                    'name': sink_name,
                    'label': description,
                    'type': 'airplay' if is_airplay else 'local',
                    'active': is_active
                })

            self.current_sink = default
            return outputs

        except Exception as e:
            log.error(f"Failed to list audio outputs: {e}")
            return []

    async def set_output(self, sink_name):
        """Switch default audio output and move active streams."""
        try:
            # Set as default sink
            subprocess.run(
                ['pactl', 'set-default-sink', sink_name],
                capture_output=True, timeout=3, check=True
            )
            # Move any active playback streams to the new sink
            result = subprocess.run(
                ['pactl', 'list', 'sink-inputs', 'short'],
                capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    stream_id = line.split('\t')[0]
                    subprocess.run(
                        ['pactl', 'move-sink-input', stream_id, sink_name],
                        capture_output=True, timeout=3
                    )

            self.current_sink = sink_name
            log.info(f"Audio output → {sink_name}")
            return True
        except Exception as e:
            log.error(f"Failed to set output {sink_name}: {e}")
            return False


class CDPlayer:
    """Controls CD playback via mpv."""

    def __init__(self, device_path=CDROM_DEVICE):
        self.device_path = device_path
        self.process = None
        self.current_track = 0
        self.total_tracks = 0
        self.state = 'stopped'  # stopped | playing | paused
        self.shuffle = False
        self.repeat = False
        self._ipc_socket = '/tmp/beo-cd-mpv.sock'
        self._play_order = []  # shuffled track order

    async def play_track(self, track_num):
        await self.stop()
        self.current_track = track_num
        try:
            self.process = subprocess.Popen([
                'mpv',
                '--ao=pulse',
                f'--cdrom-device={self.device_path}',
                f'cdda://{track_num}',
                '--no-video', '--no-terminal',
                f'--input-ipc-server={self._ipc_socket}',
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.state = 'playing'
            log.info(f"Playing track {track_num}")
        except Exception as e:
            log.error(f"Playback failed: {e}")
            self.state = 'stopped'

    async def play(self):
        if self.state == 'paused':
            self._mpv_command('cycle', 'pause')
            self.state = 'playing'
        elif self.state == 'stopped':
            await self.play_track(1)

    async def pause(self):
        if self.state == 'playing':
            self._mpv_command('cycle', 'pause')
            self.state = 'paused'

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
        elif self.current_track < self.total_tracks:
            await self.play_track(self.current_track + 1)
        elif self.repeat:
            await self.play_track(1)

    async def prev_track(self):
        if self.shuffle and self._play_order:
            idx = self._play_order.index(self.current_track) if self.current_track in self._play_order else 0
            if idx > 0:
                await self.play_track(self._play_order[idx - 1])
        elif self.current_track > 1:
            await self.play_track(self.current_track - 1)

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
        # Put current track first if playing
        if self.current_track in self._play_order:
            self._play_order.remove(self.current_track)
            self._play_order.insert(0, self.current_track)

    async def stop(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        self.state = 'stopped'

    def _mpv_command(self, *args):
        import socket as sock
        try:
            s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
            s.connect(self._ipc_socket)
            cmd = json.dumps({'command': list(args)}) + '\n'
            s.sendall(cmd.encode())
            s.close()
        except Exception as e:
            log.error(f"mpv IPC error: {e}")

    def get_status(self):
        return {
            'state': self.state,
            'current_track': self.current_track,
            'total_tracks': self.total_tracks,
            'shuffle': self.shuffle,
            'repeat': self.repeat
        }


class CDService:
    """Main CD service — ties drive detection, metadata, AirPlay, and playback together."""

    def __init__(self):
        self.drive = CDDrive()
        self.metadata_lookup = CDMetadata()
        self.audio = AudioOutputs()
        self.player = CDPlayer()
        self.metadata = None
        self._all_releases = []  # full release list from MusicBrainz
        self._http_session = None
        self._rip_process = None

    async def start(self):
        log.info("Starting CD service...")

        await self.drive.start_polling(
            on_drive_change=self._on_drive_change,
            on_disc_change=self._on_disc_change
        )

        # HTTP API
        app = web.Application()
        app.router.add_get('/status', self._handle_status)
        app.router.add_post('/command', self._handle_command)
        app.router.add_options('/command', self._handle_cors)
        app.router.add_get('/speakers', self._handle_speakers)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', HTTP_PORT)
        await site.start()
        log.info(f"HTTP API on port {HTTP_PORT}")

        self._http_session = ClientSession()

    async def stop(self):
        await self.player.stop()
        await self.drive.stop()
        if self._http_session:
            await self._http_session.close()

    # ── Drive event handlers ──

    async def _on_drive_change(self, connected):
        pass  # Status is available via /status for the debug screen

    async def _on_disc_change(self, inserted):
        if inserted:
            # Show CD menu item immediately (spinning disc)
            await self._send_input_command('add_menu_item', {'preset': 'cd'})
            # Fetch metadata in background
            asyncio.create_task(self._fetch_and_update_metadata())
        else:
            await self.player.stop()
            self.metadata = None
            await self._send_input_command('remove_menu_item', {'path': 'menu/cd'})

    async def _fetch_and_update_metadata(self):
        self.metadata = await self.metadata_lookup.lookup()
        if self.metadata:
            self.player.total_tracks = self.metadata.get('track_count', 0)
            await self._broadcast_cd_update()

    async def _broadcast_cd_update(self):
        if not self.metadata:
            return
        await self._send_input_command('broadcast', {
            'type': 'cd_update',
            'data': {
                'title': self.metadata.get('title', 'Unknown Album'),
                'artist': self.metadata.get('artist', 'Unknown Artist'),
                'album': self.metadata.get('album', ''),
                'artwork': self.metadata.get('artwork'),
                'back_artwork': self.metadata.get('back_artwork'),
                'tracks': self.metadata.get('tracks', []),
                'track_count': self.metadata.get('track_count', 0),
                'alternatives': self.metadata.get('alternatives', []),
                'shuffle': self.player.shuffle,
                'repeat': self.player.repeat,
                'has_external_drive': self._detect_external_drive()
            }
        })

    def _detect_external_drive(self):
        """Check if an external USB drive is mounted (for ripping)."""
        try:
            result = subprocess.run(
                ['lsblk', '-nro', 'MOUNTPOINT,TRAN'],
                capture_output=True, text=True, timeout=3
            )
            for line in result.stdout.strip().split('\n'):
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == 'usb' and parts[0].startswith('/'):
                    return parts[0]
        except Exception:
            pass
        return None

    # ── Communication with input.py ──

    async def _send_input_command(self, command, params):
        try:
            async with self._http_session.post(
                INPUT_WEBHOOK_URL,
                json={'command': command, 'params': params},
                timeout=5
            ) as resp:
                log.info(f"→ input.py: {command} (HTTP {resp.status})")
        except Exception as e:
            log.error(f"Failed to send {command}: {e}")

    # ── HTTP API handlers ──

    def _cors_headers(self):
        return {'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'}

    async def _handle_cors(self, request):
        return web.Response(headers=self._cors_headers())

    async def _handle_status(self, request):
        status = {
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
        return web.json_response(status, headers=self._cors_headers())

    async def _handle_command(self, request):
        try:
            data = await request.json()
            cmd = data.get('command', '')

            if cmd == 'play':
                await self.player.play()
            elif cmd == 'pause':
                await self.player.pause()
            elif cmd == 'toggle':
                await self.player.toggle_playback()
            elif cmd == 'next':
                await self.player.next_track()
            elif cmd == 'prev':
                await self.player.prev_track()
            elif cmd == 'stop':
                await self.player.stop()
            elif cmd == 'play_track':
                await self.player.play_track(data.get('track', 1))
            elif cmd == 'eject':
                await self.player.stop()
                self.drive.eject()
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
            else:
                return web.json_response(
                    {'status': 'error', 'message': f'Unknown: {cmd}'},
                    status=400, headers=self._cors_headers())

            return web.json_response(
                {'status': 'ok', 'command': cmd, 'playback': self.player.get_status()},
                headers=self._cors_headers())
        except Exception as e:
            return web.json_response(
                {'status': 'error', 'message': str(e)},
                status=500, headers=self._cors_headers())

    async def _use_alternative_release(self, release_id):
        """Switch metadata to an alternative MusicBrainz release."""
        if not release_id or not self.metadata:
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


async def main():
    service = CDService()
    await service.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        await service.stop()


if __name__ == '__main__':
    asyncio.run(main())
