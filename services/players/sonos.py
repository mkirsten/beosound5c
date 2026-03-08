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
from lib.player_base import PlayerBase

# Configuration
SONOS_IP = cfg("player", "ip", default="192.168.0.190")
POLL_INTERVAL = 0.5  # seconds between change checks (fast for responsive track changes)
PREFETCH_COUNT = 5  # number of upcoming tracks to prefetch

# Thread pool for blocking SoCo calls
executor = ThreadPoolExecutor(max_workers=6)

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

    def get_artwork_url(self):
        """Get the artwork URL for the currently playing track."""
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
        urls = self.get_queue_artwork_urls(count=count)
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
        # Broadcast suppression during Spotify track switches
        self._suppress_until_track = None   # Spotify track ID to wait for
        self._suppress_set_time = 0.0       # monotonic time when suppression was set
        self._last_play_was_radio = False    # only suppress monitor for radio plays
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
                   radio=False) -> bool:
        """Play content on the Sonos speaker.

        uri: Spotify share link (https://open.spotify.com/...) or spotify: URI
        url: generic stream URL for play_uri
        track_uri: Spotify track URI (spotify:track:xxx) to start at within
                   a playlist — the queue is searched by URI since ordering
                   may differ between Spotify Web API and Sonos SMAPI.
        meta: optional dict with display metadata (title, artist, album,
              artwork_url, track_number) — rendered in Sonos controller UI.
        radio: if True, use x-rincon-mp3radio:// scheme for Sonos streaming.
        """
        self._last_play_was_radio = radio
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()

            # Suppress monitor broadcasts while the queue is being rebuilt
            if track_uri and ":" in track_uri:
                suppress_id = track_uri.split(":")[-1]
                self._suppress_until_track = suppress_id
                self._suppress_set_time = time.monotonic()
                logger.info("Suppressing broadcasts until track %s appears", suppress_id[:12])

            if uri:
                # Convert spotify: URIs to share links
                if uri.startswith("spotify:"):
                    parts = uri.split(":")
                    if len(parts) == 3:
                        uri = f"https://open.spotify.com/{parts[1]}/{parts[2]}"

                # Use ShareLink for Spotify / Apple Music / TIDAL URLs
                if "open.spotify.com" in uri or "music.apple.com" in uri or "tidal.com" in uri:
                    share_link = ShareLinkPlugin(coordinator)
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
            return True
        except Exception as e:
            logger.error("Previous track failed: %s", e)
            return False

    async def stop(self) -> bool:
        try:
            loop = asyncio.get_running_loop()
            coordinator = self.sonos_viewer.get_coordinator()
            await loop.run_in_executor(None, coordinator.pause)
            logger.info("Stopped (paused)")
            return True
        except Exception as e:
            logger.error("Stop failed: %s", e)
            return False

    async def get_capabilities(self) -> list:
        return ["spotify", "url_stream"]

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

    # ── PlayerBase hooks ──

    async def on_start(self):
        logger.info("Starting media server for Sonos at %s", SONOS_IP)
        self._monitor_task = asyncio.create_task(self.monitor_sonos())
        # One-time network discovery, then start polling default player
        asyncio.create_task(self._startup_and_monitor())

    async def on_ws_connect(self, ws):
        """Media updates now flow through the router — no-op."""

    async def on_stop(self):
        if self._default_player_task:
            self._default_player_task.cancel()
            try:
                await self._default_player_task
            except (asyncio.CancelledError, Exception):
                pass
            self._default_player_task = None

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
                "http://localhost:8770/router/source",
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
        """One-time multicast discovery — returns {player_name: ip}."""
        try:
            devices = soco.discover(timeout=5)
        except Exception as e:
            logger.warning("Sonos network discovery failed: %s", e)
            return {}
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
        """Check all cached devices for playback state — parallel queries."""
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

        # Query all devices in parallel
        futures = {executor.submit(_check_one, n, ip): n
                   for n, ip in self._sonos_devices.items()}

        seen_coordinators = set()
        results = []
        for future in as_completed(futures):
            result = future.result()
            if result and result["ip"] not in seen_coordinators:
                seen_coordinators.add(result["ip"])
                results.append(result)

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
        """GET /player/network — check all known devices on demand."""
        loop = asyncio.get_running_loop()
        devices = await loop.run_in_executor(executor, self._check_all_devices_sync)
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

        while self.running:
            try:
                loop = asyncio.get_running_loop()

                # Get current track info (automatically uses coordinator)
                track_info = await loop.run_in_executor(
                    executor, self.sonos_viewer.get_current_track_info)

                # Check playback state for wake trigger
                coordinator = self.sonos_viewer.get_coordinator()
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

                    # Detect state transitions
                    if state == 'playing' and self._current_playback_state in ('paused', 'stopped', None):
                        logger.info(f"Playback started (was: {self._current_playback_state}), triggering wake")
                        await self.trigger_wake()
                        # External playback? Clear any stale active source so
                        # transport commands route directly to the player.
                        if self.seconds_since_command() > 3.0:
                            logger.info("External playback detected, clearing active source")
                            asyncio.create_task(
                                self.notify_router_playback_override(force=True))
                    elif state == 'stopped' and self._current_playback_state == 'playing':
                        if self.seconds_since_command() > 3.0:
                            logger.info("External stop detected")
                            asyncio.create_task(
                                self.notify_router_playback_override(force=True))

                    self._current_playback_state = state

                    # Report volume changes to the router (base deduplicates)
                    # Always read from the local speaker, never the coordinator
                    try:
                        local = self.sonos_viewer.sonos
                        vol = await loop.run_in_executor(
                            executor, lambda: local.volume) if local else None
                        if vol is not None:
                            await self.report_volume_to_router(vol)
                    except Exception as e:
                        logger.debug(f"Could not check Sonos volume: {e}")

                except Exception as e:
                    logger.debug(f"Could not get transport state: {e}")

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

                    # Check broadcast suppression (during track-switch queue rebuild)
                    suppress = False
                    if self._suppress_until_track:
                        elapsed = time.monotonic() - self._suppress_set_time
                        if elapsed > 3.0:
                            logger.info("Broadcast suppression expired (%.1fs)", elapsed)
                            self._suppress_until_track = None
                        elif self._suppress_until_track in track_id:
                            logger.info("Expected track appeared, clearing suppression")
                            self._suppress_until_track = None
                        else:
                            suppress = True

                    # Suppress monitor broadcasts when a radio source just
                    # commanded playback — it pre-broadcasts its own metadata
                    # (e.g. SR artwork) and the monitor's Sonos-sourced data
                    # would overwrite it.  Only for radio — other sources
                    # (Spotify, Plex) rely on the monitor for their metadata.
                    if (not suppress and self._last_play_was_radio
                            and self.seconds_since_command() <= 3.0):
                        suppress = True

                    # Only broadcast if there are actual changes
                    if track_changed or position_jumped:
                        reason = 'track_change' if track_changed else 'external_control'

                        if suppress:
                            logger.debug("Suppressing broadcast during track switch")
                        else:
                            logger.info(f"Detected change: {reason}")
                            media_data = await self.fetch_media_data()
                            if media_data:
                                await self.broadcast_media_update(media_data, reason)

                        self._current_track_id = track_id

                        if track_changed:
                            asyncio.create_task(self.sonos_viewer.prefetch_upcoming_artwork(count=PREFETCH_COUNT))
                    else:
                        if track_changed:
                            self._current_track_id = track_id
                            if not suppress:
                                await self.fetch_media_data()
                            asyncio.create_task(self.sonos_viewer.prefetch_upcoming_artwork(count=PREFETCH_COUNT))

                    # External track change? Clear active source so transport
                    # commands route directly to the player.
                    # This also fires on Sonos auto-next-track when the queue
                    # was built by a BS5c source (e.g. Plex) more than 3s ago.
                    # That's intentional — once the queue is just running, the
                    # player owns the metadata and the UI should show Sonos data
                    # via DEFAULT_PLAYING_PRESET rather than stale source info.
                    if track_changed and self.seconds_since_command() > 3.0:
                        asyncio.create_task(
                            self.notify_router_playback_override(force=True)
                        )

                    self._current_position = position

                await asyncio.sleep(POLL_INTERVAL)

            except Exception as e:
                logger.error(f"Error in Sonos monitoring: {e}")
                await asyncio.sleep(POLL_INTERVAL)

    async def fetch_media_data(self):
        """Fetch current media data including artwork."""
        try:
            loop = asyncio.get_running_loop()

            track_info = await loop.run_in_executor(
                executor, self.sonos_viewer.get_current_track_info)
            if not track_info:
                logger.debug("No track info available")
                return None

            artwork_url = self.sonos_viewer.get_artwork_url()
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
