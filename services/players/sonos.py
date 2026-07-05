#!/usr/bin/env python3
"""
BeoSound 5c Sonos Player (beo-player-sonos)

Monitors a Sonos speaker for track changes, fetches artwork, and broadcasts
updates to the UI via WebSocket (port 8766). Also reports volume changes to
the router so the volume arc stays in sync when controlled from the Sonos app.

Extends PlayerBase to expose HTTP command endpoints so sources can play
content on the Sonos without importing SoCo directly:
  POST /player/play   — play a Spotify URI or generic URL
  POST /player/pause  — pause playback
  POST /player/resume — resume playback
  POST /player/next   — skip to next track
  POST /player/prev   — go to previous track
  POST /player/stop   — stop playback
  GET  /player/state  — current playback state
  GET  /player/capabilities — what this player can play
"""

import asyncio
import re
import time
import logging
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import aiohttp
from aiohttp import web

# Import Sonos libraries
try:
    import soco
    from soco import SoCo
    from soco.exceptions import SoCoUPnPException
    from soco.plugins.sharelink import ShareLinkPlugin
    from soco.plugins.sharelink import AppleMusicShare
except ImportError:
    print("ERROR: soco library not installed. Install with: pip install soco")
    sys.exit(1)

# Patch SoCo's AppleMusicShare to support /song/ URLs (SoCo only has /album/ and /playlist/)
_orig_canonical_uri = AppleMusicShare.canonical_uri

def _patched_canonical_uri(self, uri):
    result = _orig_canonical_uri(self, uri)
    if result:
        return result
    # https://music.apple.com/se/song/clocks/1122776156
    match = re.search(r"https://music\.apple\.com/\w+/song/[^/]+/(\d+)", uri)
    if match:
        return "song:" + match.group(1)
    return None

AppleMusicShare.canonical_uri = _patched_canonical_uri

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.config import cfg
from lib.endpoints import ROUTER_SOURCE
from lib.player_base import PlayerBase
from lib.timings import USER_ACTION_HORIZON

# Configuration
SONOS_IP = cfg("player", "ip", default="192.168.0.190")
POLL_INTERVAL = 0.5  # seconds between change checks (fast for responsive track changes)
PREFETCH_COUNT = 5  # number of upcoming tracks to prefetch


from dataclasses import dataclass

@dataclass
class _SuppressState:
    """Active broadcast-suppression window set after a play command.

    Cleared early when ``expected_track`` is seen in the track URI (Spotify
    track-switch path), or unconditionally at ``until`` (timeout / radio path).
    """
    until: float                    # time.monotonic() deadline
    expected_track: str | None      # URI fragment to match; None = wait for deadline

# Thread pool for blocking SoCo calls
executor = ThreadPoolExecutor(max_workers=6)

# Separate pool for the JOIN-view network sweep.  _check_all_devices_sync
# runs as one task and fans out one future per Sonos device, blocking on
# the results — running all of that inside `executor` saturates it (outer
# task + N device probes), queueing the monitor loop's 500ms status calls
# behind an up-to-8s network check.  8 workers: 1 for the sweep itself,
# 7 for concurrent device probes.
netcheck_executor = ThreadPoolExecutor(max_workers=8)


async def _resolved(value):
    """Instantly-resolved coroutine — used as a no-op stand-in when an
    optional executor call is skipped (e.g. no coordinator available)."""
    return value

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-player-sonos')


class SonosArtworkViewer:
    """Integrated Sonos artwork viewer for direct communication with Sonos devices."""

    def __init__(self, sonos_ip, player=None):
        self.sonos_ip = sonos_ip
        self.sonos = SoCo(sonos_ip)
        self._player = player  # PlayerBase instance for artwork cache
        self._cached_coordinator = None
        self._coordinator_check_time = 0

    def get_coordinator(self):
        """Get the group coordinator for this player with caching."""
        current_time = time.time()

        # Refresh coordinator info every 30 seconds or on first call
        if (self._cached_coordinator is None or
                current_time - self._coordinator_check_time > 30):

            try:
                coordinator = self.sonos.group.coordinator

                if coordinator and coordinator.ip_address:
                    self._cached_coordinator = coordinator
                    self._coordinator_check_time = current_time

                    if hasattr(self, '_last_coordinator_ip'):
                        if self._last_coordinator_ip != coordinator.ip_address:
                            logger.info(f"Coordinator changed from {self._last_coordinator_ip} to {coordinator.ip_address}")
                    self._last_coordinator_ip = coordinator.ip_address

                    return coordinator
                else:
                    logger.debug("Coordinator not reachable, using original player")
                    self._cached_coordinator = self.sonos
                    self._coordinator_check_time = current_time
                    return self.sonos

            except Exception as e:
                logger.debug(f"Error getting coordinator, using original player: {e}")
                self._cached_coordinator = self.sonos
                self._coordinator_check_time = current_time
                return self.sonos

        return self._cached_coordinator

    def get_current_track_info(self):
        """Get current track information from Sonos player or its coordinator."""
        try:
            coordinator = self.get_coordinator()
            track_info = coordinator.get_current_track_info()

            if coordinator != self.sonos:
                logger.debug(f"Using coordinator {coordinator.ip_address} instead of {self.sonos_ip}")

            return track_info
        except Exception as e:
            logger.error(f"Error getting track info: {e}")
            return None

    def get_artwork_url(self, track_info=None):
        """Get the artwork URL for the currently playing track.

        Pass the ``track_info`` dict already in hand when available —
        re-fetching makes a second blocking SoCo round-trip, and on rapid
        skips the two snapshots can straddle a track change, pairing
        track N's title with track N+1's artwork.
        """
        if track_info is None:
            track_info = self.get_current_track_info()
        if not track_info:
            return None

        artwork_url = track_info.get('album_art', '')
        if not artwork_url:
            logger.debug("No artwork URL found for current track")
            return None

        if artwork_url.startswith('/'):
            coordinator = self.get_coordinator()
            coordinator_ip = coordinator.ip_address
            artwork_url = f"http://{coordinator_ip}:1400{artwork_url}"

        return artwork_url

    async def fetch_artwork_async(self, url, session=None):
        """Delegate to PlayerBase.fetch_artwork (shared cache + image processing)."""
        if self._player:
            return await self._player.fetch_artwork(url, session=session)
        return None

    def get_queue_artwork_urls(self, count=3):
        """Get artwork URLs for upcoming tracks in the queue."""
        try:
            coordinator = self.get_coordinator()
            if not coordinator:
                return []

            track_info = coordinator.get_current_track_info()
            if not track_info:
                return []

            current_pos_str = track_info.get('playlist_position', '0')
            try:
                current_pos = int(current_pos_str)
            except (ValueError, TypeError):
                return []

            start_index = current_pos
            queue = coordinator.get_queue(start=start_index, max_items=count)

            artwork_urls = []
            for i, item in enumerate(queue):
                album_art = getattr(item, 'album_art_uri', None)
                if album_art:
                    if album_art.startswith('/'):
                        album_art = f"http://{coordinator.ip_address}:1400{album_art}"
                    artwork_urls.append((start_index + i + 1, album_art))

            return artwork_urls

        except Exception as e:
            logger.debug(f"Error getting queue artwork URLs: {e}")
            return []

    async def prefetch_upcoming_artwork(self, count=3):
        """Prefetch artwork for upcoming tracks in background."""
        loop = asyncio.get_running_loop()
        urls = await loop.run_in_executor(executor, self.get_queue_artwork_urls, count)
        if not urls:
            logger.debug("No upcoming tracks to prefetch")
            return

        logger.info(f"Prefetching artwork for {len(urls)} upcoming tracks")

        async with aiohttp.ClientSession() as session:
            tasks = []
            for position, url in urls:
                if self._player and url in self._player._artwork_cache:
                    logger.debug(f"Track {position} artwork already cached")
                    continue
                tasks.append(self._prefetch_single(session, position, url))

            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=15.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("Prefetch timed out, some tracks may not be cached")

    async def _prefetch_single(self, session, position, url):
        """Prefetch a single artwork URL."""
        try:
            result = await self.fetch_artwork_async(url, session=session)
            if result:
                logger.debug(f"Prefetched artwork for track {position}")
            else:
                logger.debug(f"No artwork for track {position}")
        except Exception as e:
            logger.debug(f"Failed to prefetch track {position}: {e}")


class MediaServer(PlayerBase):
    id = "sonos"
    name = "Sonos"
    port = 8766

    DEFAULT_PLAYER_POLL_INTERVAL = 15  # seconds between default-player checks

    def __init__(self):
        super().__init__()
        self.sonos_viewer = SonosArtworkViewer(SONOS_IP, player=self)
        # Sonos-specific monitoring state
        self._current_track_id = None
        self._current_position = None
        self._last_update_time = 0
        # A track change whose broadcast was suppressed (window expiry) or
        # whose media fetch failed transiently.  The track id is already
        # committed at that point (dedup would swallow it forever), so the
        # monitor retries the broadcast on following polls until one lands.
        self._pending_broadcast = False
        self._pending_broadcast_attempts = 0
        # Broadcast suppression window (set by play(), cleared by monitor/playback_started).
        # Unifies the old _suppress_until_track / _suppress_set_time / _last_play_was_radio.
        self._suppress: _SuppressState | None = None
        self._queue_backfill_task: asyncio.Task | None = None
        # JOIN feature: one-time discovery map + default-player monitor
        self._sonos_devices: dict[str, str] = {}   # player_name → ip
        self._default_player_playing: bool = False
        self._default_player_task: asyncio.Task | None = None

    # ── PlayerBase abstract methods (SoCo playback commands) ──

    @staticmethod
    def _build_didl(url, meta):
        """Build a DIDL-Lite metadata string for Sonos play_uri.

        Provides title, artist, album, artwork, and track number so the
        Sonos controller app shows rich metadata instead of just the URL.
        """
        try:
            from soco.data_structures import DidlMusicTrack, to_didl_string
            kwargs = {}
            if meta.get('artist'):
                kwargs['creator'] = meta['artist']
                kwargs['artist'] = meta['artist']
            if meta.get('album'):
                kwargs['album'] = meta['album']
            if meta.get('artwork_url'):
                kwargs['album_art_uri'] = meta['artwork_url']
            if meta.get('track_number'):
                kwargs['original_track_number'] = meta['track_number']
            track = DidlMusicTrack(
                title=meta.get('title', 'Unknown'),
                parent_id='usb',
                item_id=f"usb:{meta.get('id', '0')}",
                **kwargs,
            )
            return to_didl_string(track)
        except Exception as e:
            logger.warning("Failed to build DIDL metadata: %s", e)
            return ''

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None) -> bool:
        """Play content on the Sonos speaker.

        uri: Spotify share link (https://open.spotify.com/...) or spotify: URI
        url: generic stream URL for play_uri
        track_uri: Spotify track URI (spotify:track:xxx) to start at within
                   a playlist — the queue is searched by URI since ordering
                   may differ between Spotify Web API and Sonos SMAPI.
        meta: optional dict with display metadata (title, artist, album,
              artwork_url, track_number) — rendered in Sonos controller UI.
        radio: if True, use x-rincon-mp3radio:// scheme for Sonos streaming.
        track_uris: list of spotify:track:xxx URIs to queue individually
                    (used for Liked Songs and other non-playlist collections).
        """
        if radio:
            self._suppress = _SuppressState(
                until=time.monotonic() + USER_ACTION_HORIZON,
                expected_track=None,
            )
            logger.info("Suppressing monitor broadcasts for %.0fs (radio)", USER_ACTION_HORIZON)
        elif track_uri and ":" in track_uri:
            suppress_id = track_uri.split(":")[-1]
            self._suppress = _SuppressState(
                until=time.monotonic() + USER_ACTION_HORIZON,
                expected_track=suppress_id,
            )
            logger.info("Suppressing broadcasts until track %s appears", suppress_id[:12])
        else:
            self._suppress = None
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()

            # Queue individual tracks (for Liked Songs and other non-playlist collections)
            # Check before uri path so Sonos queues all tracks, not just the first one
            if track_uris:
                if self._queue_backfill_task and not self._queue_backfill_task.done():
                    self._queue_backfill_task.cancel()
                share_link = ShareLinkPlugin(coordinator)
                # Fast-start: play first track immediately
                start_idx = 0
                if track_uri:
                    for i, tu in enumerate(track_uris):
                        if tu == track_uri:
                            start_idx = i
                            break
                first_url = track_uris[start_idx].replace("spotify:", "https://open.spotify.com/").replace(":", "/")
                try:
                    await loop.run_in_executor(None, coordinator.pause)
                except Exception:
                    pass
                await loop.run_in_executor(None, coordinator.clear_queue)
                await loop.run_in_executor(
                    None, share_link.add_share_link_to_queue, first_url)
                await loop.run_in_executor(
                    None, coordinator.play_from_queue, 0)
                logger.info("Track-queue: playing %s, backfilling %d tracks",
                            track_uris[start_idx][:40], len(track_uris) - 1)
                remaining = track_uris[start_idx + 1:] + track_uris[:start_idx]
                if remaining:
                    self._queue_backfill_task = asyncio.create_task(
                        self._backfill_track_uris(coordinator, remaining))
                return True

            if uri:
                # Convert spotify: URIs to share links
                if uri.startswith("spotify:"):
                    parts = uri.split(":")
                    if len(parts) == 3:
                        uri = f"https://open.spotify.com/{parts[1]}/{parts[2]}"

                # Use ShareLink for Spotify / Apple Music / TIDAL URLs
                if "open.spotify.com" in uri or "music.apple.com" in uri or "tidal.com" in uri:
                    share_link = ShareLinkPlugin(coordinator)

                    # Fast-start: queue single track, play immediately,
                    # backfill full playlist in background
                    if track_uri and "/playlist/" in uri:
                        # Cancel any in-flight backfill
                        if self._queue_backfill_task and not self._queue_backfill_task.done():
                            self._queue_backfill_task.cancel()
                        # Build a single-track share link
                        track_url = track_uri.replace("spotify:", "https://open.spotify.com/")
                        track_url = track_url.replace(":", "/")
                        try:
                            await loop.run_in_executor(None, coordinator.pause)
                        except Exception:
                            pass
                        await loop.run_in_executor(None, coordinator.clear_queue)
                        await loop.run_in_executor(
                            None, share_link.add_share_link_to_queue, track_url)
                        await loop.run_in_executor(
                            None, coordinator.play_from_queue, 0)
                        logger.info("Fast-start: playing %s, backfilling %s",
                                    track_uri[:30], uri)
                        # Background backfill the full playlist
                        self._queue_backfill_task = asyncio.create_task(
                            self._backfill_queue(coordinator, uri, track_uri))
                        return True

                    # Standard path: queue full playlist, find track, play
                    # Pause first to prevent auto-play when adding to empty queue
                    if track_uri:
                        try:
                            await loop.run_in_executor(None, coordinator.pause)
                        except Exception:
                            pass
                    await loop.run_in_executor(None, coordinator.clear_queue)
                    await loop.run_in_executor(
                        None, share_link.add_share_link_to_queue, uri)

                    start_index = 0
                    if track_uri:
                        start_index = await self._find_track_in_queue(
                            coordinator, track_uri, loop)

                    await loop.run_in_executor(
                        None, coordinator.play_from_queue, start_index)
                    logger.info("Playing Spotify URI: %s (queue pos %d)", uri, start_index)
                    return True

            if url:
                play_url = url
                if radio:
                    # Sonos needs x-rincon-mp3radio:// for radio streams —
                    # plain HTTP/HTTPS fails with UPnP 714 (Illegal MIME-Type).
                    # Strip scheme and use the Sonos radio protocol instead.
                    play_url = url.replace("https://", "x-rincon-mp3radio://")
                    play_url = play_url.replace("http://", "x-rincon-mp3radio://")
                didl_meta = ''
                if meta:
                    didl_meta = self._build_didl(play_url, meta)
                title = meta.get("title", "") if meta else ""
                if didl_meta:
                    await loop.run_in_executor(
                        None, lambda: coordinator.play_uri(
                            play_url, meta=didl_meta, title=title))
                else:
                    await loop.run_in_executor(
                        None, lambda: coordinator.play_uri(
                            play_url, title=title))
                logger.info("Playing URL: %s%s", url,
                            " (radio)" if radio else "")
                return True

            # No URI/URL — just resume
            return await self.resume()

        except Exception as e:
            err = str(e)
            if "800" in err and uri:
                # UPnP 800 = music service account not linked on Sonos
                if "tidal.com" in uri:
                    svc = "TIDAL"
                elif "music.apple.com" in uri:
                    svc = "Apple Music"
                elif "spotify.com" in uri:
                    svc = "Spotify"
                else:
                    svc = "the music service"
                logger.error("Play failed: %s account not linked on Sonos — "
                             "add it in the Sonos app first", svc)
            else:
                logger.error("Play failed: %s", e)
            return False

    async def pause(self) -> bool:
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()
            await loop.run_in_executor(None, coordinator.pause)
            logger.info("Paused")
            return True
        except SoCoUPnPException as e:
            if e.error_code == '701':
                logger.debug("Pause: already paused (UPnP 701)")
                return True
            logger.error("Pause failed: %s", e)
            return False
        except Exception as e:
            logger.error("Pause failed: %s", e)
            return False

    async def resume(self) -> bool:
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()
            await loop.run_in_executor(None, coordinator.play)
            logger.info("Resumed")
            return True
        except Exception as e:
            logger.error("Resume failed: %s", e)
            return False

    async def next_track(self) -> bool:
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()
            await loop.run_in_executor(None, coordinator.next)
            logger.info("Next track")
            self._spawn(self._broadcast_after_skip(), name="skip_poll")
            return True
        except Exception as e:
            logger.error("Next track failed: %s", e)
            return False

    async def prev_track(self) -> bool:
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()
            await loop.run_in_executor(None, coordinator.previous)
            logger.info("Previous track")
            self._spawn(self._broadcast_after_skip(), name="skip_poll")
            return True
        except Exception as e:
            logger.error("Previous track failed: %s", e)
            return False

    async def _broadcast_after_skip(self):
        """Proactive poll 150ms after a skip to broadcast the new track
        without waiting for the regular 500ms monitor cycle.

        Safe to run concurrently with the monitor loop: _current_track_id
        deduplication ensures only one broadcast fires per track change.
        If Sonos hasn't settled yet (still returns old URI), the check
        short-circuits and the monitor loop catches the change as normal.
        """
        await asyncio.sleep(0.15)
        try:
            loop = asyncio.get_running_loop()
            track_info = await loop.run_in_executor(
                executor, self.sonos_viewer.get_current_track_info)
            if not track_info:
                return
            track_id = track_info.get('uri', '')
            if not track_id or track_id == self._current_track_id:
                return  # Sonos hasn't settled yet; monitor loop will catch it
            if self._suppress and time.monotonic() < self._suppress.until:
                return  # Respect broadcast suppression window
            self._current_track_id = track_id
            self._current_position = None  # prevent monitor's position-jump check from
                                           # firing a concurrent broadcast while we fetch
            logger.info("Early track broadcast after skip")
            media_data = await self.fetch_media_data()
            if media_data:
                await self.broadcast_media_update(media_data, "track_change")
            else:
                # Track id is committed; without a retry the monitor's dedup
                # would swallow this track forever (same invariant as the
                # monitor loop's _pending_broadcast).
                self._pending_broadcast = True
                self._pending_broadcast_attempts = 0
        except Exception as e:
            logger.debug("Early skip broadcast failed: %s", e)
            self._pending_broadcast = True
            self._pending_broadcast_attempts = 0

    async def _retry_artwork_broadcast(self, artwork_url, track_id):
        """Single delayed retry after an artwork fetch failed at track-change
        time.  Re-fetches the artwork and rebroadcasts the full media state
        if the same track is still playing; gives up quietly otherwise."""
        await asyncio.sleep(3)
        if track_id != self._current_track_id:
            return  # track moved on — its own broadcast handles artwork
        try:
            artwork_result = await self.sonos_viewer.fetch_artwork_async(artwork_url)
        except Exception as e:
            logger.debug("Artwork retry failed: %s", e)
            return
        if not artwork_result:
            return
        media_data = await self.fetch_media_data()
        if media_data and media_data.get('artwork') and \
                track_id == self._current_track_id:
            logger.info("Artwork retry succeeded — rebroadcasting")
            await self.broadcast_media_update(media_data, "update")

    async def stop(self) -> bool:
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()
            await loop.run_in_executor(None, coordinator.pause)
            logger.info("Stopped (paused)")
            return True
        except SoCoUPnPException as e:
            if e.error_code == '701':
                # Already paused/stopped — desired state reached, not an error
                logger.debug("Stop: already paused (UPnP 701)")
                return True
            logger.error("Stop failed: %s", e)
            return False
        except Exception as e:
            logger.error("Stop failed: %s", e)
            return False

    async def set_shuffle(self, enabled: bool) -> bool:
        """Toggle shuffle via the Sonos play mode (repeat state kept off —
        BS5c doesn't manage repeat, and SHUFFLE alone implies repeat-all
        in the Sonos API, which would surprise more than it helps)."""
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()

            def _set():
                coordinator.play_mode = 'SHUFFLE_NOREPEAT' if enabled else 'NORMAL'
            await loop.run_in_executor(executor, _set)
            logger.info("Shuffle %s", "on" if enabled else "off")
            return True
        except Exception as e:
            logger.error("set_shuffle failed: %s", e)
            return False

    async def play_track_radio(self, track_uri) -> bool:
        """Start Spotify track radio (a station seeded by *track_uri*).

        Builds the Sonos SMAPI ``trackRadio:spotify:track:<id>`` container
        URI and plays it via the queue, bypassing the ShareLink plugin which
        doesn't recognise station URIs.
        """
        if not track_uri or "spotify:track:" not in track_uri:
            logger.warning("play_track_radio: invalid track_uri %r", track_uri)
            return False
        track_id = track_uri.split(":")[-1]
        self._suppress = _SuppressState(
            until=time.monotonic() + USER_ACTION_HORIZON,
            expected_track=None,
        )
        logger.info("Suppressing monitor broadcasts for %.0fs (track radio)",
                    USER_ACTION_HORIZON)
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()

            # Cancel any in-flight playlist backfill from a prior play()
            if self._queue_backfill_task and not self._queue_backfill_task.done():
                self._queue_backfill_task.cancel()

            encoded = f"trackRadio%3aspotify%3atrack%3a{track_id}"
            enqueue_uri = f"x-rincon-cpcontainer:1006206c{encoded}"
            sn = 2311  # Spotify SMAPI service number (matches SpotifyShare)
            item_id = f"1006206c{encoded}"
            metadata = (
                '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
                'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
                'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
                f'<item id="{item_id}" restricted="true">'
                '<upnp:class>object.container.playlistContainer</upnp:class>'
                '<desc id="cdudn" nameSpace="urn:schemas-rinconnetworks-com:'
                f'metadata-1-0/">SA_RINCON{sn}_X_#Svc{sn}-0-Token</desc>'
                '</item></DIDL-Lite>'
            )

            try:
                await loop.run_in_executor(None, coordinator.pause)
            except Exception:
                pass
            await loop.run_in_executor(None, coordinator.clear_queue)

            def _enqueue():
                return coordinator.avTransport.AddURIToQueue([
                    ("InstanceID", 0),
                    ("EnqueuedURI", enqueue_uri),
                    ("EnqueuedURIMetaData", metadata),
                    ("DesiredFirstTrackNumberEnqueued", 0),
                    ("EnqueueAsNext", 0),
                ])

            await loop.run_in_executor(None, _enqueue)
            await loop.run_in_executor(None, coordinator.play_from_queue, 0)
            logger.info("Playing Spotify track radio seeded by %s", track_id[:12])
            return True
        except Exception as e:
            err = str(e)
            if "800" in err:
                logger.error("Track radio failed: Spotify account not linked "
                             "on Sonos — add it in the Sonos app first")
            else:
                logger.error("Track radio failed: %s", e)
            return False

    async def get_capabilities(self) -> list:
        return ["spotify", "url_stream", "spotify_track_radio"]

    async def get_track_uri(self) -> str:
        return self._current_track_id or ""

    async def get_status(self) -> dict:
        """Rich cached status for the system panel."""
        base = await super().get_status()
        cached = self._cached_media_data or {}
        base.update({
            "speaker_name": cached.get("speaker_name", "—"),
            "speaker_ip": SONOS_IP,
            "state": self._current_playback_state or "stopped",
            "volume": cached.get("volume"),
            "current_track": {
                "title": cached.get("title", "—"),
                "artist": cached.get("artist", "—"),
                "album": cached.get("album", "—"),
            } if cached else None,
            "is_grouped": cached.get("is_grouped", False),
            "coordinator_name": cached.get("coordinator_name"),
            "artwork_cache_size": len(self._artwork_cache),
        })
        return base

    async def _backfill_queue(self, coordinator, playlist_uri, track_uri):
        """Background task: replace single-track queue with full playlist."""
        try:
            loop = asyncio.get_running_loop()
            share_link = ShareLinkPlugin(coordinator)
            # Add full playlist (appends after the single track already playing)
            await loop.run_in_executor(
                None, share_link.add_share_link_to_queue, playlist_uri)
            # Remove the duplicate first track (position 0, which is the
            # single-track we fast-started with — now duplicated in the
            # full playlist that was appended)
            await loop.run_in_executor(
                None, coordinator.remove_from_queue, 0)
            # Find and jump to the correct position in the full queue
            idx = await self._find_track_in_queue(coordinator, track_uri, loop)
            if idx > 0:
                await loop.run_in_executor(
                    None, coordinator.play_from_queue, idx)
            logger.info("Backfill complete: full playlist queued, playing at %d", idx)
        except asyncio.CancelledError:
            logger.debug("Backfill cancelled")
        except Exception as e:
            logger.warning("Backfill failed: %s", e)

    async def _backfill_track_uris(self, coordinator, track_uris):
        """Background task: queue individual track URIs after fast-start."""
        try:
            loop = asyncio.get_running_loop()
            share_link = ShareLinkPlugin(coordinator)
            queued = 0
            for tu in track_uris:
                track_url = tu.replace("spotify:", "https://open.spotify.com/").replace(":", "/")
                try:
                    await loop.run_in_executor(
                        None, share_link.add_share_link_to_queue, track_url)
                    queued += 1
                except Exception as e:
                    logger.debug("Skipping track %s: %s", tu[:30], e)
            logger.info("Track-queue backfill complete: %d/%d tracks queued",
                        queued, len(track_uris))
        except asyncio.CancelledError:
            logger.debug("Track-queue backfill cancelled")
        except Exception as e:
            logger.warning("Track-queue backfill failed: %s", e)

    async def _find_track_in_queue(self, coordinator, track_uri, loop) -> int:
        """Find a Spotify track in the Sonos queue by URI. Returns 0-based index."""
        # Extract Spotify track ID from URI (spotify:track:XXXXX)
        track_id = track_uri.split(":")[-1] if ":" in track_uri else track_uri

        def _search():
            batch = 50
            for start in range(0, 500, batch):
                items = coordinator.get_queue(start=start, max_items=batch)
                if not items:
                    break
                for i, item in enumerate(items):
                    # Sonos encodes track IDs in resource URIs as:
                    # x-sonos-spotify:spotify%3atrack%3aTRACK_ID?sid=9&...
                    for res in item.resources:
                        if track_id in res.uri:
                            return start + i
            return 0  # fallback to first track

        idx = await loop.run_in_executor(None, _search)
        logger.info("Found track %s at queue position %d", track_id[:12], idx)
        return idx

    # ── Queue support ──

    async def get_queue(self, start=0, max_items=50) -> dict:
        """Return the Sonos playback queue."""
        loop = asyncio.get_running_loop()
        try:
            def _fetch():
                coordinator = self.sonos_viewer.get_coordinator()
                track_info = coordinator.get_current_track_info()
                current_pos = 0
                if track_info:
                    try:
                        current_pos = int(track_info.get('playlist_position', '0')) - 1
                    except (ValueError, TypeError):
                        pass
                queue_items = coordinator.get_queue(start=start, max_items=max_items)
                tracks = []
                for i, item in enumerate(queue_items):
                    idx = start + i
                    art = getattr(item, 'album_art_uri', '') or ''
                    if art.startswith('/'):
                        art = f"http://{coordinator.ip_address}:1400{art}"
                    tracks.append({
                        "id": f"q:{idx}",
                        "title": getattr(item, 'title', '') or '',
                        "artist": getattr(item, 'creator', '') or '',
                        "album": getattr(item, 'album', '') or '',
                        "artwork": art,
                        "index": idx,
                        "current": idx == current_pos,
                    })
                total = max(start + len(tracks), current_pos + 1)
                return {"tracks": tracks, "current_index": current_pos, "total": total}
            return await loop.run_in_executor(None, _fetch)
        except Exception as e:
            logger.warning("get_queue failed: %s", e)
            return {"tracks": [], "current_index": -1, "total": 0}

    async def play_from_queue(self, position: int) -> bool:
        """Play a specific position in the Sonos queue."""
        loop = asyncio.get_running_loop()
        try:
            coordinator = self.sonos_viewer.get_coordinator()
            await loop.run_in_executor(None, coordinator.play_from_queue, position)
            return True
        except Exception as e:
            logger.warning("play_from_queue(%d) failed: %s", position, e)
            return False

    # ── PlayerBase hooks ──

    async def on_start(self):
        logger.info("Starting media server for Sonos at %s", SONOS_IP)
        self._monitor_task = self._spawn(self.monitor_sonos(), name="sonos_monitor")
        # One-time network discovery, then start polling default player
        self._spawn(self._startup_and_monitor(), name="sonos_startup")

    async def on_ws_connect(self, ws):
        """Media updates now flow through the router — no-op."""

    async def on_stop(self):
        for attr in ('_monitor_task', '_default_player_task', '_queue_backfill_task'):
            task = getattr(self, attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(self, attr, None)

    def add_routes(self, app):
        """Register JOIN-related endpoints."""
        app.router.add_get("/player/network", self._handle_network)
        app.router.add_post("/player/join", self._handle_join)
        app.router.add_post("/player/unjoin", self._handle_unjoin)
        app.router.add_get("/player/resync", self._handle_resync)

    # ── Network discovery (JOIN feature) ──

    async def _register_join_source(self, state: str = "available"):
        """Register or update the JOIN source on the router."""
        try:
            async with self._http_session.post(
                ROUTER_SOURCE,
                json={"id": "join", "state": state, "name": "Join"},
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as resp:
                logger.debug("JOIN source %s: HTTP %d", state, resp.status)
                return True
        except Exception as e:
            logger.debug("Could not register JOIN source: %s", e)
            return False

    async def _handle_resync(self, request) -> web.Response:
        """GET /player/resync — re-register JOIN if speakers are known."""
        if self._sonos_devices:
            ok = await self._register_join_source("available")
            if ok:
                logger.info("JOIN resync: re-registered (%d speakers)",
                            len(self._sonos_devices))
            return web.json_response({"resynced": ok},
                                     headers=self._cors_headers())
        return web.json_response({"resynced": False},
                                 headers=self._cors_headers())

    def _discover_all_sync(self) -> dict[str, str]:
        """Discover other Sonos zones — returns {player_name: ip}.

        SSDP multicast first (fast), then a unicast subnet scan as a fallback.
        Multicast is routinely dropped on WiFi (client isolation) and across
        VLANs, so discover() comes back empty on plenty of real networks even
        when other Sonos zones are present — which left JOIN permanently empty.
        The scan reaches them over unicast.
        """
        try:
            devices = soco.discover(timeout=5)
        except Exception as e:
            logger.warning("Sonos network discovery failed: %s", e)
            devices = None
        if not devices:
            try:
                from soco import discovery as _disc
                devices = _disc.scan_network(
                    multi_household=False, scan_timeout=0.5, max_threads=256)
            except Exception as e:
                logger.warning("Sonos subnet scan failed: %s", e)
                devices = None
        if not devices:
            return {}

        local_ip = self.sonos_viewer.sonos.ip_address
        result = {}
        for d in devices:
            try:
                if d.ip_address != local_ip:
                    result[d.player_name] = d.ip_address
            except Exception:
                pass
        return result

    def _check_device_playing_sync(self, ip: str) -> bool:
        """Check if a single device (or its coordinator) is playing."""
        try:
            device = SoCo(ip)
            coordinator = device.group.coordinator
            transport = coordinator.get_current_transport_info()
            state = transport.get("current_transport_state", "")
            return state in ("PLAYING", "PAUSED_PLAYBACK")
        except Exception:
            return False

    def _check_all_devices_sync(self) -> list[dict]:
        """Check all cached devices for playback state — parallel queries.

        Uses a per-future timeout so one slow/unreachable speaker doesn't
        block the entire response.  Results are cached so the /network
        endpoint can return instantly on the next poll.
        """
        DEVICE_TIMEOUT = 8  # seconds — global cap for as_completed

        local_ip = self.sonos_viewer.sonos.ip_address
        try:
            own_coord_ip = self.sonos_viewer.sonos.group.coordinator.ip_address
        except Exception:
            own_coord_ip = local_ip

        skip_ips = {local_ip, own_coord_ip}

        def _check_one(name, ip):
            """Check a single device — returns result dict or None."""
            try:
                device = SoCo(ip)
                coordinator = device.group.coordinator
                coord_ip = coordinator.ip_address
                if coord_ip in skip_ips:
                    return None

                transport = coordinator.get_current_transport_info()
                transport_state = transport.get("current_transport_state", "")

                state = "playing" if transport_state == "PLAYING" else "stopped"

                track = coordinator.get_current_track_info()
                artwork_url = track.get("album_art", "")
                if artwork_url and artwork_url.startswith("/"):
                    artwork_url = f"http://{coord_ip}:1400{artwork_url}"

                # Group members (excluding coordinator itself)
                group_members = []
                try:
                    for m in device.group.members:
                        if m.ip_address != coord_ip:
                            group_members.append(m.player_name)
                except Exception:
                    pass

                return {
                    "name": coordinator.player_name,
                    "ip": coord_ip,
                    "state": state,
                    "title": track.get("title", ""),
                    "artist": track.get("artist", ""),
                    "album": track.get("album", ""),
                    "artwork_url": artwork_url,
                    "group": group_members,
                }
            except Exception as e:
                logger.debug("Skipping device %s during check: %s", name, e)
                return None

        # Query all devices in parallel (netcheck pool — see its comment)
        futures = {netcheck_executor.submit(_check_one, n, ip): n
                   for n, ip in self._sonos_devices.items()}

        seen_coordinators = set()
        results = []
        try:
            for future in as_completed(futures, timeout=DEVICE_TIMEOUT):
                try:
                    result = future.result(timeout=0)
                except Exception:
                    continue
                if result and result["ip"] not in seen_coordinators:
                    seen_coordinators.add(result["ip"])
                    results.append(result)
        except TimeoutError:
            timed_out = [name for f, name in futures.items() if not f.done()]
            if timed_out:
                logger.debug("Network check timed out for: %s", timed_out)

        # Cancel any still-running futures (don't wait for stragglers)
        for future in futures:
            future.cancel()

        # Only overwrite cache if we got results (avoid flapping to empty)
        if results or not getattr(self, "_network_cache", None):
            self._network_cache = results
        return results

    async def _startup_and_monitor(self):
        """One-time discovery, then start the default-player polling loop."""
        loop = asyncio.get_running_loop()
        self._sonos_devices = await loop.run_in_executor(
            executor, self._discover_all_sync)
        logger.info("Discovered %d Sonos devices: %s",
                     len(self._sonos_devices), list(self._sonos_devices.keys()))

        # Show JOIN in menu when other speakers exist on the network
        if len(self._sonos_devices) > 0:
            for attempt in range(10):
                ok = await self._register_join_source("available")
                if ok:
                    logger.info("JOIN source registered (found %d other speakers)",
                                len(self._sonos_devices))
                    break
                if attempt < 9:
                    await asyncio.sleep(3)
                else:
                    logger.warning("Failed to register JOIN source after retries")

        default_player = cfg("join", "default_player", default="")
        if default_player and default_player in self._sonos_devices:
            self._default_player_task = asyncio.create_task(
                self._monitor_default_player(default_player))
        elif default_player:
            logger.warning("JOIN default_player '%s' not found on network",
                           default_player)

    async def _monitor_default_player(self, player_name: str):
        """Poll a single device to drive JOIN menu visibility."""
        ip = self._sonos_devices[player_name]
        logger.info("Monitoring default JOIN player: %s (%s)", player_name, ip)

        while self.running:
            try:
                loop = asyncio.get_running_loop()
                is_playing = await loop.run_in_executor(
                    executor, self._check_device_playing_sync, ip)

                if is_playing != self._default_player_playing:
                    self._default_player_playing = is_playing
                    state = "available" if is_playing else "gone"
                    logger.info("JOIN visibility: %s (%s)", state, player_name)
                    await self._register_join_source(state)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("Default player monitor error: %s", e)
            await asyncio.sleep(self.DEFAULT_PLAYER_POLL_INTERVAL)

    async def _handle_network(self, request) -> web.Response:
        """GET /player/network — return cached devices, refresh in background.

        First call (no cache) blocks up to DEVICE_TIMEOUT seconds.
        Subsequent calls return the cache instantly and kick off a
        background refresh (at most once per 10s) so the next poll
        gets fresh data.
        """
        REFRESH_COOLDOWN = 10  # seconds between background refreshes

        cache = getattr(self, "_network_cache", None)
        if cache is not None:
            # Return cached result immediately, refresh in background if cooldown elapsed
            now = time.time()
            last = getattr(self, "_network_refresh_time", 0)
            if now - last >= REFRESH_COOLDOWN:
                self._network_refresh_time = now
                loop = asyncio.get_running_loop()
                loop.run_in_executor(netcheck_executor, self._check_all_devices_sync)
            return web.json_response(cache, headers=self._cors_headers())

        # First call — must block (but capped at DEVICE_TIMEOUT)
        self._network_refresh_time = time.time()
        loop = asyncio.get_running_loop()
        devices = await loop.run_in_executor(netcheck_executor, self._check_all_devices_sync)
        return web.json_response(devices, headers=self._cors_headers())

    async def _handle_join(self, request) -> web.Response:
        """POST /player/join — join this speaker to another group.

        Accepts {"ip": "..."} or {"name": "..."} (resolved from device map).
        Optional media fields (title, artist, album, artwork_url) are forwarded
        to the router as an immediate media update so PLAYING shows content
        without waiting for the next poll cycle.
        """
        self._stamp_command()
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400,
                                     headers=self._cors_headers())

        target_ip = data.get("ip")
        target_name = data.get("name")
        if not target_ip and target_name:
            target_ip = self._sonos_devices.get(target_name)
            if not target_ip:
                return web.json_response(
                    {"error": f"unknown device: {target_name}"}, status=404,
                    headers=self._cors_headers())
        if not target_ip:
            return web.json_response({"error": "ip or name required"}, status=400,
                                     headers=self._cors_headers())

        loop = asyncio.get_running_loop()
        try:
            def _join():
                target = SoCo(target_ip)
                coordinator = target.group.coordinator
                self.sonos_viewer.sonos.join(coordinator)
                # Start playback if coordinator isn't already playing
                transport = coordinator.get_current_transport_info()
                if transport.get("current_transport_state") != "PLAYING":
                    coordinator.play()
                # Force-set coordinator cache — SoCo's group state may
                # not reflect the join yet, causing next/prev to fail
                self.sonos_viewer._cached_coordinator = coordinator
                self.sonos_viewer._coordinator_check_time = time.time()
                return coordinator.player_name

            joined_name = await loop.run_in_executor(executor, _join)
            logger.info("Joined group: %s (%s)", joined_name, target_ip)

            # Pre-broadcast full media data (with artwork) from the new
            # coordinator so PLAYING shows content immediately — works for
            # both the UI path and the BLUE-button shortcut.
            media_data = await self.fetch_media_data()
            if media_data:
                await self.broadcast_media_update(media_data, reason="join")

            return web.json_response({"status": "ok", "joined": joined_name},
                                     headers=self._cors_headers())
        except Exception as e:
            logger.error("Join failed: %s", e)
            return web.json_response({"error": str(e)}, status=500,
                                     headers=self._cors_headers())

    async def _handle_unjoin(self, request) -> web.Response:
        """POST /player/unjoin — leave the current group."""
        loop = asyncio.get_running_loop()
        try:
            def _unjoin():
                self.sonos_viewer.sonos.unjoin()
                self.sonos_viewer._cached_coordinator = None
                self.sonos_viewer._coordinator_check_time = 0

            await loop.run_in_executor(executor, _unjoin)
            logger.info("Left group (unjoined)")
            return web.json_response({"status": "ok"}, headers=self._cors_headers())
        except Exception as e:
            logger.error("Unjoin failed: %s", e)
            return web.json_response({"error": str(e)}, status=500,
                                     headers=self._cors_headers())

    # ── Monitoring ──

    async def _on_playback_started(self):
        """Handle paused/stopped → playing transition.

        Order matters:
          1. Clear any leftover broadcast suppression so the eager broadcast
             can't be swallowed by a stale ``_suppress`` window from a prior
             play command (e.g. radio suppress still active, or track-switch
             window waiting for a URI that will never arrive).
          2. Push a fresh media_update so the router cache (and therefore
             every connected UI client) holds the new track *before* the
             UI navigates to the playing view.
          3. Trigger wake — this navigates the UI to ``now_playing``.
             By the time the UI gets there, mediaInfo is already current.
          4. If the start was external (no recent BS5c command), clear
             any stale active source so transport commands route directly
             to the player.
        """
        logger.info("Playback started (was: %s)", self._current_playback_state)
        external = self.seconds_since_command() > USER_ACTION_HORIZON

        self._suppress = None

        # Order is critical:
        #   1. Clear the active source FIRST (if external), with
        #      push_idle=False so the router doesn't broadcast an idle
        #      media_update that would wipe the eager broadcast below.
        #      This also advances the router's action_ts watermark so the
        #      subsequent broadcast isn't rejected as stale.
        #   2. Trigger wake — turns on the screen immediately so the user
        #      gets visual feedback without waiting for artwork to load.
        #   3. Fetch + broadcast fresh media — router._state is updated and
        #      every WS client sees the new track. The UI will update as
        #      soon as metadata arrives (typically < 1s after wake).
        if external:
            logger.info("External playback detected, clearing active source")
            await self.notify_router_playback_override(force=True,
                                                       push_idle=False)

        await self.trigger_wake()

        try:
            media_data = await self.fetch_media_data()
            if media_data:
                await self.broadcast_media_update(media_data, "track_change")
        except Exception as e:
            logger.warning("Eager media broadcast on play start failed: %s", e)

    async def _on_external_track_change(self):
        """External track advance while already playing (Sonos-app skip,
        auto-next on a Plex/Spotify queue older than 3s).

        The monitor loop has just broadcast fresh media for the new track.
        We need to tell the router to drop the now-stale active source so
        transport commands route directly to the player — but
        ``push_idle`` MUST be False, otherwise the router broadcasts an
        idle media_update that wipes the fresh metadata we just pushed,
        leaving the UI with empty title/artist/album and only artwork.
        """
        await self.notify_router_playback_override(force=True,
                                                   push_idle=False)

    async def monitor_sonos(self):
        """Background task to monitor Sonos for changes."""
        logger.info(f"Starting Sonos monitoring for {SONOS_IP}")

        # Log initial coordinator info
        try:
            coordinator = self.sonos_viewer.get_coordinator()
            if coordinator.ip_address != SONOS_IP:
                logger.info(f"Player {SONOS_IP} is grouped, using coordinator {coordinator.ip_address}")
            else:
                logger.info(f"Player {SONOS_IP} is standalone or group coordinator")
        except Exception as e:
            logger.warning(f"Could not determine coordinator status: {e}")

        consecutive_failures = 0
        while self.running:
            try:
                loop = asyncio.get_running_loop()
                coordinator = self.sonos_viewer.get_coordinator()
                local = self.sonos_viewer.sonos

                # Fetch track info, transport state, and volume in parallel.
                track_info, transport_result, vol_result = await asyncio.gather(
                    loop.run_in_executor(executor, self.sonos_viewer.get_current_track_info),
                    loop.run_in_executor(executor, coordinator.get_current_transport_info)
                        if coordinator else _resolved({}),
                    loop.run_in_executor(executor, lambda: local.volume)
                        if local else _resolved(None),
                    return_exceptions=True,
                )
                transport_info = transport_result if not isinstance(transport_result, Exception) else {}
                vol = vol_result if not isinstance(vol_result, Exception) else None

                # Process transport state and detect play/stop transitions
                try:
                    playback_state = transport_info.get('current_transport_state', 'STOPPED').lower()
                    if playback_state in ('playing', 'transitioning'):
                        state = 'playing'
                    elif playback_state == 'paused_playback':
                        state = 'paused'
                    else:
                        state = 'stopped'

                    # Detect state transitions
                    prev_state = self._current_playback_state
                    if state == 'playing' and prev_state in ('paused', 'stopped', None):
                        await self._on_playback_started()
                    elif state == 'stopped' and prev_state == 'playing':
                        if self.seconds_since_command() > USER_ACTION_HORIZON:
                            logger.info("External stop detected")
                            self._spawn(
                                self.notify_router_playback_override(force=True),
                                name="playback_override")

                    self._current_playback_state = state

                    # Broadcast on pause/stop so the UI can stop canvas/video playback.
                    # Only fires when state actually changes from playing — the track
                    # didn't change so the normal track_change broadcast won't fire.
                    if prev_state == 'playing' and state in ('paused', 'stopped'):
                        cached = self._cached_media_data
                        if cached:
                            state_data = dict(cached)
                            state_data['state'] = state
                            await self.broadcast_media_update(state_data, 'state_change')
                except Exception as e:
                    logger.debug(f"Could not get transport state: {e}")

                # Report volume changes to the router (base deduplicates)
                if vol is not None:
                    try:
                        await self.report_volume_to_router(vol)
                    except Exception as e:
                        logger.debug(f"Could not report volume: {e}")

                if track_info:
                    track_id = track_info.get('uri', '')
                    position = track_info.get('position', '0:00')

                    # Check if track changed
                    track_changed = track_id != self._current_track_id

                    # Check if position jumped (indicating external control)
                    position_jumped = False
                    if self._current_position and position:
                        try:
                            current_seconds = self.time_to_seconds(self._current_position)
                            new_seconds = self.time_to_seconds(position)
                            expected_seconds = current_seconds + POLL_INTERVAL

                            if abs(new_seconds - expected_seconds) > 5:
                                position_jumped = True
                        except (ValueError, TypeError):
                            pass

                    # Check broadcast suppression (track-switch queue rebuild or radio).
                    # Check expected-track match FIRST so a track arriving right at the
                    # deadline still broadcasts rather than being swallowed.
                    suppress = False
                    if self._suppress:
                        now = time.monotonic()
                        if (self._suppress.expected_track
                                and self._suppress.expected_track in track_id):
                            logger.info("Expected track appeared, clearing suppression")
                            self._suppress = None
                            # Don't suppress — the expected track is here, broadcast it
                        elif now >= self._suppress.until:
                            elapsed = now - (self._suppress.until - USER_ACTION_HORIZON)
                            logger.info("Broadcast suppression expired (%.1fs)", elapsed)
                            self._suppress = None
                            suppress = True  # one more cycle — let next poll pick up clean state
                        else:
                            suppress = True

                    # Only broadcast if there are actual changes. The previous
                    # `else` branch also contained an `if track_changed` check
                    # that was unreachable (the outer `or` already covered it),
                    # so the branch has been removed.
                    if track_changed or position_jumped:
                        reason = 'track_change' if track_changed else 'external_control'

                        # Commit new track id BEFORE broadcasting — the
                        # router's canvas injection may call back into
                        # get_track_uri() while the broadcast POST is in
                        # flight, and must not observe the old value.
                        self._current_track_id = track_id

                        if suppress:
                            logger.debug("Suppressing broadcast during track switch")
                            # Track id is committed but nothing was sent —
                            # without a retry the UI stays on the previous
                            # track for the whole song (e.g. Spotify track
                            # relinking never matches expected_track).
                            self._pending_broadcast = True
                            self._pending_broadcast_attempts = 0
                        else:
                            logger.info(f"Detected change: {reason}")
                            media_data = await self.fetch_media_data()
                            if media_data:
                                await self.broadcast_media_update(media_data, reason)
                                self._pending_broadcast = False
                            else:
                                # Transient fetch failure — retry next poll.
                                self._pending_broadcast = True
                                self._pending_broadcast_attempts = 0

                        if track_changed:
                            self._spawn(
                                self.sonos_viewer.prefetch_upcoming_artwork(count=PREFETCH_COUNT),
                                name="prefetch_artwork")

                    # Retry a previously swallowed/failed broadcast once the
                    # suppression window is gone.  Deliberately does NOT
                    # re-run the side-effect paths (prefetch, playback
                    # override) — only the media update is owed.
                    elif self._pending_broadcast and not suppress:
                        self._pending_broadcast_attempts += 1
                        media_data = await self.fetch_media_data()
                        if media_data:
                            await self.broadcast_media_update(media_data, 'track_change')
                            self._pending_broadcast = False
                            logger.info("Recovered suppressed/failed broadcast "
                                        "(attempt %d)", self._pending_broadcast_attempts)
                        elif self._pending_broadcast_attempts >= 20:
                            logger.warning("Giving up on pending broadcast after "
                                           "%d attempts", self._pending_broadcast_attempts)
                            self._pending_broadcast = False

                    # External track change? Clear active source so transport
                    # commands route directly to the player.
                    if track_changed and self.seconds_since_command() > USER_ACTION_HORIZON:
                        self._spawn(
                            self._on_external_track_change(),
                            name="playback_override")

                    self._current_position = position

                if consecutive_failures:
                    logger.info("Sonos reachable again after %d failed polls",
                                consecutive_failures)
                    consecutive_failures = 0
                await asyncio.sleep(POLL_INTERVAL)

            except Exception as e:
                # Back off while the speaker is unreachable. With the shipped
                # placeholder player.ip (before the user configures their real
                # speaker) or a speaker that's off the network, every cycle
                # raises — at 0.5s that's ~2 error lines/second into the
                # journal forever. Log the first failure, back off to 30s,
                # and resume the fast poll as soon as a cycle succeeds.
                consecutive_failures += 1
                if consecutive_failures == 1:
                    logger.error(f"Error in Sonos monitoring: {e}")
                elif consecutive_failures % 120 == 0:
                    logger.warning("Sonos still unreachable (%d consecutive "
                                   "failed polls): %s", consecutive_failures, e)
                await asyncio.sleep(
                    min(POLL_INTERVAL * (2 ** min(consecutive_failures, 6)), 30.0))

    async def fetch_media_data(self):
        """Fetch current media data including artwork."""
        try:
            loop = asyncio.get_running_loop()

            track_info = await loop.run_in_executor(
                executor, self.sonos_viewer.get_current_track_info)
            if not track_info:
                logger.debug("No track info available")
                return None

            # Sonos surfaces "ZPSTR_BUFFERING" / "ZPSTR_CONNECTING" as title
            # (and sometimes artist/album) while a live stream is buffering or
            # negotiating with the source — particularly hits radio. Those
            # aren't real metadata; broadcasting them overwrites whatever the
            # source service (e.g. the radio service's SR programme name) had
            # already set in the router, and Sonos won't push another
            # track_change for the same live stream once buffering settles —
            # so the placeholder sticks until the next programme change.
            # Drop the update entirely; the radio source's poller (or the
            # next real Sonos track event) will keep the router state fresh.
            for _k in ("title", "artist", "album"):
                _v = track_info.get(_k, "")
                if isinstance(_v, str) and _v.startswith("ZPSTR_"):
                    logger.debug("Skipping media update (Sonos placeholder %s=%r)",
                                 _k, _v)
                    return None

            artwork_url = self.sonos_viewer.get_artwork_url(track_info)
            artwork_base64 = None
            artwork_size = None

            if artwork_url:
                try:
                    artwork_result = await self.sonos_viewer.fetch_artwork_async(artwork_url)
                    if artwork_result:
                        artwork_base64 = artwork_result['base64']
                        artwork_size = artwork_result['size']
                        logger.info(f"Artwork ready: {artwork_size}, {len(artwork_base64)} chars")
                except Exception as e:
                    logger.warning(f"Failed to fetch artwork: {e}")
                if not artwork_base64:
                    # Artwork existed but the fetch failed (CDN hiccup, cold
                    # cache).  The broadcast goes out with the placeholder and
                    # nothing rebroadcasts until the next track — schedule one
                    # delayed retry that rebroadcasts if it succeeds.
                    self._spawn(
                        self._retry_artwork_broadcast(artwork_url,
                                                      self._current_track_id),
                        name="artwork_retry")

            coordinator = self.sonos_viewer.get_coordinator()
            actual_speaker = self.sonos_viewer.sonos
            speaker_name = actual_speaker.player_name if actual_speaker else 'Unknown'
            speaker_ip = SONOS_IP

            is_grouped = False
            coordinator_name = None
            if coordinator and coordinator.ip_address != SONOS_IP:
                is_grouped = True
                coordinator_name = coordinator.player_name

            try:
                transport_info = await loop.run_in_executor(
                    executor, coordinator.get_current_transport_info) if coordinator else {}
                playback_state = transport_info.get('current_transport_state', 'STOPPED').lower()
                if playback_state in ('playing', 'transitioning'):
                    state = 'playing'
                elif playback_state == 'paused_playback':
                    state = 'paused'
                else:
                    state = 'stopped'
            except Exception:
                state = 'unknown'

            try:
                local = self.sonos_viewer.sonos
                volume = await loop.run_in_executor(
                    executor, lambda: local.volume) if local else 0
            except Exception:
                volume = 0

            media_data = {
                'title': track_info.get('title', '—'),
                'artist': track_info.get('artist', '—'),
                'album': track_info.get('album', '—'),
                'artwork': f'data:image/jpeg;base64,{artwork_base64}' if artwork_base64 else None,
                'artwork_size': artwork_size,
                'position': track_info.get('position', '0:00'),
                'duration': track_info.get('duration', '0:00'),
                'state': state,
                'volume': volume,
                'speaker_name': speaker_name,
                'speaker_ip': speaker_ip,
                'is_grouped': is_grouped,
                'coordinator_name': coordinator_name,
                'uri': track_info.get('uri', ''),
                'timestamp': int(time.time())
            }

            self._cached_media_data = media_data
            self._last_update_time = time.time()

            return media_data

        except Exception as e:
            logger.error(f"Error fetching media data: {e}")
            return None

    def time_to_seconds(self, time_str):
        """Convert time string (MM:SS or HH:MM:SS) to seconds."""
        try:
            parts = time_str.split(':')
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except (ValueError, TypeError, IndexError):
            pass
        return 0

async def main():
    """Main entry point."""
    server = MediaServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
