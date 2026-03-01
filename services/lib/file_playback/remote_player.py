"""Remote audio playback via player service (Sonos/BlueSound).

The ``service`` argument is duck-typed.  It must provide:

    service.player_play(url=..., meta=...)  -> bool
    service.player_pause()                  -> bool
    service.player_resume()                 -> bool
    service.player_stop()                   -> bool
    service.player_state()                  -> str   ('playing'|'paused'|'stopped'|'unknown')
    service.player_track_uri()              -> str   (URI of the currently playing track)
    service.build_stream_url(track_meta)    -> str|None
    service.transcode_cache                 -> TranscodeCache|None
    service._device_ip                      -> str
    service.port                            -> int

Any SourceBase subclass that adds ``build_stream_url()`` and
``transcode_cache`` satisfies this interface.
"""

import asyncio
import logging
import random
import time

log = logging.getLogger('beo-usb')


class RemotePlayer:
    """Plays audio via the player service using HTTP stream URLs."""

    POLL_INTERVAL = 2.0
    PAUSE_TIMEOUT = 300
    PAUSE_MONITOR_INTERVAL = 5.0

    def __init__(self, service):
        self.service = service  # USBService instance (for player_play etc.)
        self.state = 'stopped'
        self.current_track = 0
        self.total_tracks = 0
        self.shuffle = False
        self.repeat = False
        self.tracks = []     # list of track metadata dicts
        self.album_name = ""
        self.album_artist = ""
        self._play_order = []
        self._poll_task = None
        self._pause_timer = None
        self._pause_monitor_task = None
        self._expected_url = None  # stream URL we sent to the player
        self._on_track_end = None
        self._on_pause_timeout = None
        self._on_external_pause = None   # called when player paused externally
        self._on_external_resume = None  # called when player resumed externally (our content)
        self._on_external_takeover = None  # called when player plays different content

    def load_tracks(self, tracks_meta, album_name="", album_artist=""):
        """Load track metadata list. Each dict must have 'id', 'title', 'file_path'."""
        self.tracks = tracks_meta
        self.total_tracks = len(tracks_meta)
        self.album_name = album_name
        self.album_artist = album_artist
        self.current_track = 0
        if self.shuffle:
            self._rebuild_play_order()
        log.info("Remote: loaded %d tracks: %s", self.total_tracks, album_name)

    async def play_track(self, index):
        if index < 0 or index >= self.total_tracks:
            return False
        self.current_track = index
        self._cancel_pause_timer()
        self._stop_pause_monitor()

        track = self.tracks[index]
        stream_url = self.service.build_stream_url(track)
        if not stream_url:
            log.error("Cannot build stream URL for track %s", track.get('id'))
            return False

        # Build display metadata for Sonos/BlueSound controller
        meta = {
            'id': track.get('id', '0'),
            'title': track.get('title', ''),
            'artist': track.get('artist', self.album_artist),
            'album': self.album_name,
            'track_number': track.get('track_number', index + 1),
        }
        album_id = track.get('album_id')
        if album_id:
            meta['artwork_url'] = (
                f"http://{self.service._device_ip}:{self.service.port}"
                f"/artwork?mount={track.get('mount_idx', 0)}&album_id={album_id}"
            )

        self._expected_url = stream_url
        ok = await self.service.player_play(url=stream_url, meta=meta)
        if ok:
            self.state = 'playing'
            # Start fresh poll for this track.  If called from within the poll's
            # _on_track_end callback, skip cancelling it — it exits via break.
            current = asyncio.current_task()
            if self._poll_task and self._poll_task is not current and not self._poll_task.done():
                self._poll_task.cancel()
            self._poll_task = asyncio.create_task(self._poll_player_state())
            # Prefetch next tracks
            await self._prefetch_next(index)
            log.info("Remote playing [%d/%d] %s", index + 1, self.total_tracks,
                     track.get('title', '?'))
        return ok

    async def _prefetch_next(self, current_index):
        """Pre-transcode next 2-3 tracks."""
        paths = []
        for offset in range(1, 4):
            idx = current_index + offset
            if idx < self.total_tracks:
                fp = self.tracks[idx].get('file_path')
                if fp:
                    paths.append(fp)
        if paths and self.service.transcode_cache:
            await self.service.transcode_cache.prefetch(paths)

    async def play(self):
        if self.state == 'paused':
            self._cancel_pause_timer()
            self._stop_pause_monitor()
            ok = await self.service.player_resume()
            if ok:
                self.state = 'playing'
                self._start_poll()
        elif self.state == 'stopped' and self.total_tracks > 0:
            await self.play_track(0)

    async def pause(self):
        if self.state == 'playing':
            ok = await self.service.player_pause()
            if ok:
                self.state = 'paused'
                self._stop_poll()
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

    def _start_poll(self):
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_player_state())

    def _stop_poll(self):
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_player_state(self):
        """Poll player state to detect track end, external pause, and takeover.

        Includes an initial grace period -- Sonos needs several seconds to
        fetch and buffer the stream URL before it transitions from 'stopped'
        to 'playing'.  Without the grace period, the first poll would see
        'stopped' and falsely trigger auto-advance.

        Also checks the track URI on the player to detect when an external
        app (Sonos/BlueSound controller) has taken over the speaker with
        different content.
        """
        GRACE_PERIOD = 8.0  # seconds to wait before treating 'stopped' as track-end
        URI_CHECK_INTERVAL = 3  # check track URI every N polls
        try:
            start = time.monotonic()
            poll_count = 0
            while self.state == 'playing':
                await asyncio.sleep(self.POLL_INTERVAL)
                poll_count += 1
                state = await self.service.player_state()
                elapsed = time.monotonic() - start

                # -- Detect external pause --
                if state == 'paused' and self.state == 'playing':
                    if elapsed < GRACE_PERIOD:
                        continue  # ignore during initial buffering
                    log.info("Remote: player paused externally")
                    self.state = 'paused'
                    self._start_pause_timer()
                    if self._on_external_pause:
                        await self._on_external_pause()
                    # Monitor for resume/takeover while paused
                    self._start_pause_monitor()
                    break

                # -- Detect stopped (track end) --
                if state in ('stopped', 'unknown') and self.state == 'playing':
                    if elapsed < GRACE_PERIOD:
                        continue
                    log.info("Remote: player stopped (track ended)")
                    self.state = 'stopped'
                    if self._on_track_end:
                        await self._on_track_end()
                    break

                # -- Detect external takeover (different content on player) --
                if (state == 'playing' and self._expected_url
                        and poll_count % URI_CHECK_INTERVAL == 0
                        and elapsed >= GRACE_PERIOD):
                    track_uri = await self.service.player_track_uri()
                    if track_uri and self._expected_url not in track_uri:
                        log.info("Remote: external takeover detected "
                                 "(expected %s, got %s)",
                                 self._expected_url[:60], track_uri[:60])
                        self.state = 'stopped'
                        self._expected_url = None
                        if self._on_external_takeover:
                            await self._on_external_takeover()
                        break
        except asyncio.CancelledError:
            pass

    def _start_pause_timer(self):
        self._cancel_pause_timer()
        loop = asyncio.get_running_loop()
        self._pause_timer = loop.call_later(
            self.PAUSE_TIMEOUT, lambda: asyncio.ensure_future(self._pause_timeout_cb()))

    def _cancel_pause_timer(self):
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

    def _start_pause_monitor(self):
        self._stop_pause_monitor()
        self._pause_monitor_task = asyncio.create_task(self._monitor_paused_state())

    def _stop_pause_monitor(self):
        if self._pause_monitor_task and not self._pause_monitor_task.done():
            self._pause_monitor_task.cancel()
            self._pause_monitor_task = None

    async def _monitor_paused_state(self):
        """Monitor player while paused externally. Detects resume or takeover."""
        try:
            while self.state == 'paused':
                await asyncio.sleep(self.PAUSE_MONITOR_INTERVAL)
                state = await self.service.player_state()

                if state == 'playing':
                    track_uri = await self.service.player_track_uri()
                    if self._expected_url and track_uri and self._expected_url in track_uri:
                        log.info("Remote: external resume detected (our content)")
                        self._cancel_pause_timer()
                        self.state = 'playing'
                        self._start_poll()
                        if self._on_external_resume:
                            await self._on_external_resume()
                    else:
                        log.info("Remote: external takeover during pause")
                        self._cancel_pause_timer()
                        self.state = 'stopped'
                        self._expected_url = None
                        if self._on_external_takeover:
                            await self._on_external_takeover()
                    break
        except asyncio.CancelledError:
            pass

    async def _pause_timeout_cb(self):
        # Check if the player resumed externally before we kill it —
        # the pause monitor polls every 5s so a last-second resume could be missed
        try:
            state = await self.service.player_state()
            if state == 'playing' and self._expected_url:
                track_uri = await self.service.player_track_uri()
                if track_uri and self._expected_url in track_uri:
                    log.info("Remote: pause timeout — but player resumed our content, re-activating")
                    self._stop_pause_monitor()
                    self.state = 'playing'
                    self._start_poll()
                    if self._on_external_resume:
                        await self._on_external_resume()
                    return
        except Exception:
            pass
        log.info("Remote: pause timeout — stopping")
        await self.stop()
        if self._on_pause_timeout:
            await self._on_pause_timeout()

    async def stop(self):
        self._cancel_pause_timer()
        self._stop_poll()
        self._stop_pause_monitor()
        if self.state != 'stopped':
            # Only stop the player if it's still playing our content —
            # another source may have already started on the same speaker
            should_stop = True
            if self._expected_url:
                try:
                    track_uri = await self.service.player_track_uri()
                    if track_uri and self._expected_url not in track_uri:
                        log.info("Remote: player has different content, skipping stop")
                        should_stop = False
                except Exception:
                    pass
            if should_stop:
                await self.service.player_stop()
        self.state = 'stopped'
        self._expected_url = None

    def get_status(self):
        track = self.tracks[self.current_track] if self.tracks and self.current_track < len(self.tracks) else {}
        return {
            'state': self.state,
            'current_track': self.current_track,
            'total_tracks': self.total_tracks,
            'track_name': track.get('title', ''),
            'artist': track.get('artist', self.album_artist),
            'album': self.album_name,
            'folder_name': self.album_artist or self.album_name,
            'folder_path': '',
            'shuffle': self.shuffle,
            'repeat': self.repeat,
        }
