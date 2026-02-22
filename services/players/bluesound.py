#!/usr/bin/env python3
"""
BeoSound 5c BlueSound Player (beo-player-bluesound)

Monitors a BlueSound speaker for track changes, fetches artwork, and broadcasts
updates to the UI via WebSocket (port 8766). Also reports volume changes to
the router so the volume arc stays in sync.

BluOS HTTP API (port 11000, XML responses):
  GET /Status?etag=X&timeout=T  — long-poll for state changes
  GET /Playlist                  — queue items with artwork URLs
  GET /Play  /Pause  /Skip  /Back  /Stop  — transport controls (all GET)
  GET /Volume?level=N            — set volume (0-100)
"""

import asyncio
import logging
import os
import sys
import time
import urllib.parse
from xml.etree import ElementTree

import aiohttp

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.config import cfg
from lib.player_base import PlayerBase

# Configuration
BLUESOUND_IP = cfg("player", "ip", default="")
BLUOS_PORT = 11000
LONG_POLL_TIMEOUT = 60   # seconds — BluOS blocks until state changes or timeout
PREFETCH_COUNT = 5

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-player-bluesound')


class BluesoundPlayer(PlayerBase):
    """BlueSound player service using the BluOS HTTP/XML API."""

    id = "bluesound"
    name = "BlueSound"
    port = 8766

    def __init__(self):
        super().__init__()
        self.ip = BLUESOUND_IP
        self.base_url = f"http://{BLUESOUND_IP}:{BLUOS_PORT}"
        # BluOS-specific state
        self._etag = ""
        self._current_track_id = None

    # ── BluOS HTTP helpers ──

    async def _bluos_get(self, path: str, timeout: float = 10) -> ElementTree.Element | None:
        """GET a BluOS endpoint, parse XML response. Returns root Element or None."""
        url = f"{self.base_url}{path}"
        try:
            async with self._http_session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
                return ElementTree.fromstring(text)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.warning("BluOS request failed (%s): %s", path, e)
            return None

    def _xml_text(self, root: ElementTree.Element, tag: str, default: str = "") -> str:
        """Get text content of a child element."""
        el = root.find(tag)
        return el.text if el is not None and el.text else default

    # ── PlayerBase abstract methods ──

    async def play(self, uri=None, url=None, track_uri=None) -> bool:
        try:
            if uri:
                logger.warning("BluOS does not support Spotify URIs — ignoring uri=%s", uri)
            if url:
                encoded_url = urllib.parse.quote(url, safe="/:?&=")
                root = await self._bluos_get(f"/Play?url={encoded_url}")
                logger.info("Playing URL: %s", url)
                return root is not None
            # Resume playback
            return await self.resume()
        except Exception as e:
            logger.error("Play failed: %s", e)
            return False

    async def pause(self) -> bool:
        try:
            root = await self._bluos_get("/Pause")
            logger.info("Paused")
            return root is not None
        except Exception as e:
            logger.error("Pause failed: %s", e)
            return False

    async def resume(self) -> bool:
        try:
            root = await self._bluos_get("/Play")
            logger.info("Resumed")
            return root is not None
        except Exception as e:
            logger.error("Resume failed: %s", e)
            return False

    async def next_track(self) -> bool:
        try:
            root = await self._bluos_get("/Skip")
            logger.info("Next track")
            return root is not None
        except Exception as e:
            logger.error("Next track failed: %s", e)
            return False

    async def prev_track(self) -> bool:
        try:
            root = await self._bluos_get("/Back")
            logger.info("Previous track")
            return root is not None
        except Exception as e:
            logger.error("Previous track failed: %s", e)
            return False

    async def stop(self) -> bool:
        try:
            root = await self._bluos_get("/Stop")
            logger.info("Stopped")
            return root is not None
        except Exception as e:
            logger.error("Stop failed: %s", e)
            return False

    async def get_capabilities(self) -> list:
        return ["url_stream"]

    async def get_status(self) -> dict:
        base = await super().get_status()
        cached = self._cached_media_data or {}
        base.update({
            "speaker_ip": self.ip,
            "state": self._current_playback_state or "stopped",
            "volume": cached.get("volume"),
            "current_track": {
                "title": cached.get("title", "—"),
                "artist": cached.get("artist", "—"),
                "album": cached.get("album", "—"),
            } if cached else None,
            "artwork_cache_size": len(self._artwork_cache),
        })
        return base

    # ── PlayerBase hooks ──

    async def on_start(self):
        if not BLUESOUND_IP:
            logger.error("No BlueSound IP configured (set player.ip in config)")
            sys.exit(1)
        logger.info("Starting BlueSound player for %s", self.base_url)
        self._monitor_task = asyncio.create_task(self._monitor_bluos())

    # ── Monitoring (long-poll loop) ──

    async def _monitor_bluos(self):
        """Long-poll /Status for state changes."""
        logger.info("Starting BluOS monitoring for %s", self.base_url)

        while self.running:
            try:
                # Build long-poll URL
                path = f"/Status?timeout={LONG_POLL_TIMEOUT}"
                if self._etag:
                    path += f"&etag={self._etag}"

                root = await self._bluos_get(
                    path, timeout=LONG_POLL_TIMEOUT + 10)

                if root is None:
                    # Timeout or error — retry
                    await asyncio.sleep(1)
                    continue

                # Update etag for next long-poll
                self._etag = root.get("etag", self._etag)

                # Parse state
                raw_state = self._xml_text(root, "state", "stop")
                if raw_state in ("play", "stream"):
                    state = "playing"
                elif raw_state == "pause":
                    state = "paused"
                else:
                    state = "stopped"

                # Wake trigger on transition to playing
                if state == "playing" and self._current_playback_state in (
                    "paused", "stopped", None
                ):
                    logger.info("Playback started (was: %s), triggering wake",
                                self._current_playback_state)
                    asyncio.create_task(self.trigger_wake())

                self._current_playback_state = state

                # Volume reporting (base method deduplicates)
                vol_str = self._xml_text(root, "volume")
                if vol_str:
                    try:
                        asyncio.create_task(self.report_volume_to_router(int(vol_str)))
                    except ValueError:
                        pass

                # Track change detection
                title = self._xml_text(root, "name") or self._xml_text(root, "title1")
                artist = self._xml_text(root, "artist") or self._xml_text(root, "title2")
                album = self._xml_text(root, "album") or self._xml_text(root, "title3")
                image_url = self._xml_text(root, "image")
                song_index = self._xml_text(root, "song")
                service = self._xml_text(root, "service")
                quality = self._xml_text(root, "quality")
                secs = self._xml_text(root, "secs")
                totlen = self._xml_text(root, "totlen")

                track_id = f"{title}|{artist}|{album}"
                track_changed = track_id != self._current_track_id

                if track_changed:
                    self._current_track_id = track_id

                    # Fetch artwork
                    artwork_base64 = None
                    artwork_size = None
                    if image_url:
                        # Make URL absolute if relative
                        if image_url.startswith("/"):
                            image_url = f"{self.base_url}{image_url}"
                        result = await self.fetch_artwork(image_url, session=self._http_session)
                        if result:
                            artwork_base64 = result["base64"]
                            artwork_size = result["size"]

                    # Format position/duration
                    position = self._seconds_to_time(secs)
                    duration = self._seconds_to_time(totlen)

                    media_data = {
                        "title": title or "—",
                        "artist": artist or "—",
                        "album": album or "—",
                        "artwork": f"data:image/jpeg;base64,{artwork_base64}" if artwork_base64 else None,
                        "artwork_size": artwork_size,
                        "position": position,
                        "duration": duration,
                        "state": state,
                        "volume": int(vol_str) if vol_str else 0,
                        "speaker_ip": self.ip,
                        "service": service,
                        "quality": quality,
                        "uri": f"bluos:{song_index}" if song_index else "",
                        "timestamp": int(time.time()),
                    }

                    self._cached_media_data = media_data

                    if self._ws_clients:
                        await self.broadcast_media_update(media_data, "track_change")
                        logger.info("Track changed: %s — %s", artist, title)

                    # Prefetch upcoming artwork
                    asyncio.create_task(self._prefetch_queue_artwork())

                    # Notify router of external playback
                    asyncio.create_task(self.notify_router_playback_override(force=True))

                elif self._cached_media_data:
                    # Update position/state in cached data without full broadcast
                    self._cached_media_data["state"] = state
                    self._cached_media_data["position"] = self._seconds_to_time(secs)
                    if vol_str:
                        self._cached_media_data["volume"] = int(vol_str)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in BluOS monitoring: %s", e)
                await asyncio.sleep(2)

    # ── Queue prefetch ──

    async def _prefetch_queue_artwork(self):
        """Prefetch artwork for upcoming tracks from /Playlist."""
        try:
            root = await self._bluos_get("/Playlist")
            if root is None:
                return

            songs = root.findall(".//song")
            current_index = -1
            # Find current song index
            for i, song in enumerate(songs):
                if song.get("selected") == "selected":
                    current_index = i
                    break

            # Prefetch next N tracks
            start = max(current_index + 1, 0)
            tasks = []
            for song in songs[start:start + PREFETCH_COUNT]:
                img = song.get("image") or self._xml_text(song, "image")
                if img:
                    if img.startswith("/"):
                        img = f"{self.base_url}{img}"
                    if img not in self._artwork_cache:
                        tasks.append(self.fetch_artwork(img, session=self._http_session))

            if tasks:
                logger.info("Prefetching artwork for %d upcoming tracks", len(tasks))
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=15.0,
                )
        except asyncio.TimeoutError:
            logger.warning("Prefetch timed out")
        except Exception as e:
            logger.debug("Queue prefetch error: %s", e)

    # ── Helpers ──

    @staticmethod
    def _seconds_to_time(secs_str: str) -> str:
        """Convert seconds string to MM:SS or HH:MM:SS."""
        try:
            total = int(float(secs_str))
        except (ValueError, TypeError):
            return "0:00"
        if total >= 3600:
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            return f"{h}:{m:02d}:{s:02d}"
        return f"{total // 60}:{total % 60:02d}"

async def main():
    player = BluesoundPlayer()
    await player.run()


if __name__ == "__main__":
    asyncio.run(main())
