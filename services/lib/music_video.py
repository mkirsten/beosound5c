# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""
Music video lookup for BeoSound 5c.

Search strategy, tried in order:
  1. Public Invidious instances (fast HTTP). List is refreshed from the
     official registry lazily so we self-heal when instances die.
  2. yt-dlp ytsearch directly against YouTube (subprocess, ~3s). Last resort
     when the entire Invidious pool is unreachable — uses the same clients
     as stream resolution, so if videos play at all, search works.

Stream resolution is always via yt-dlp.

Two-tier cache: video IDs are cached permanently (avoids repeated search
hits), stream URLs are cached for 2 hours (Googlevideo auth tokens expire).
"""

import asyncio
import collections
import logging
import subprocess
import time
import urllib.parse

import aiohttp

# yt-dlp binary locations to try in order
_YTDLP_BINS = ("/usr/local/bin/yt-dlp", "/usr/bin/yt-dlp", "yt-dlp")

log = logging.getLogger("music-video")

# Live registry of public Invidious instances. Fetched lazily and refreshed
# hourly so we self-heal when individual instances go dark — which is frequent,
# since the public pool churns monthly.
INVIDIOUS_REGISTRY_URL = "https://api.invidious.io/instances.json"
INVIDIOUS_REFRESH_S = 3600          # re-fetch registry at most once per hour
INVIDIOUS_REFRESH_ON_FAIL_S = 600   # min spacing between forced refreshes after total failure

# Seed list — used only until the registry fetch succeeds. Chosen from the
# current registry as known-reachable; will be supplanted on first refresh.
INVIDIOUS_SEED = [
    "https://yt.chocolatemoo53.com",
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://inv.thepixora.com",
]

# Minimum video duration — filters out shorts and clips (seconds)
MIN_DURATION_S = 90

# Stream URL cache TTL — Googlevideo tokens expire, so re-resolve periodically
STREAM_URL_TTL_S = 7200  # 2 hours

_ID_CACHE_MAX = 500      # artist+title → youtube video_id (permanent)
_STREAM_CACHE_MAX = 200  # video_id → (url, fetched_time)


class MusicVideoClient:
    """Looks up direct music video stream URLs for artist + title pairs.

    No API key required — uses public Invidious instances for search and
    stream resolution. Designed to be instantiated once and reused across
    track changes.
    """

    def __init__(self):
        # "artist||title" → youtube video_id or "" (meaning "no video found")
        self._id_cache: collections.OrderedDict[str, str] = collections.OrderedDict()
        # video_id → (stream_url, fetched_at)
        self._stream_cache: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        # Working set of Invidious instances, head-of-list = most recently successful.
        self._instances: list[str] = list(INVIDIOUS_SEED)
        self._instances_fetched_at: float = 0.0
        self._instance_refresh_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return True  # always available — no credentials needed

    def _id_key(self, artist: str, title: str) -> str:
        return f"{artist.lower().strip()}||{title.lower().strip()}"

    def get_cached(self, artist: str, title: str) -> str | None:
        """Return cached stream URL (may be ""), or None if not yet looked up.

        Returns:
            str  — valid stream URL (cache hit, video found)
            ""   — cache hit, no video found for this track
            None — not in cache, needs a lookup
        """
        key = self._id_key(artist, title)
        video_id = self._id_cache.get(key)
        if video_id is None:
            return None  # not looked up yet
        if not video_id:
            return ""    # looked up, no video found
        cached = self._stream_cache.get(video_id)
        if cached:
            url, fetched_at = cached
            if time.time() - fetched_at < STREAM_URL_TTL_S:
                return url
        return None  # id known but stream URL expired — needs re-resolve

    async def lookup(self, artist: str, title: str,
                     session: aiohttp.ClientSession) -> str | None:
        """Return a direct video stream URL, or None if not found."""
        if not artist or not title:
            return None

        # Fast path — no lock needed for cache read
        cached = self.get_cached(artist, title)
        if cached is not None:
            return cached or None

        async with self._lock:
            # Re-check after acquiring lock
            cached = self.get_cached(artist, title)
            if cached is not None:
                return cached or None

            return await self._fetch(artist, title, session)

    async def _fetch(self, artist: str, title: str,
                     session: aiohttp.ClientSession) -> str | None:
        key = self._id_key(artist, title)

        # Step 1: search for a video ID (may already be cached if stream expired)
        video_id = self._id_cache.get(key)
        if not video_id:
            video_id = await self._search(artist, title, session)
            if video_id is None:
                # Network failure — don't cache, so the next track play retries
                return None
            # video_id == "" means "searched, no video found" — cache it to skip
            # future lookups; non-empty means found — cache the ID
            if len(self._id_cache) >= _ID_CACHE_MAX:
                self._id_cache.popitem(last=False)
            self._id_cache[key] = video_id  # "" or actual ID
            if not video_id:
                return None

        # Step 2: resolve direct stream URL
        url = await self._resolve_stream(video_id, session)
        if url:
            if len(self._stream_cache) >= _STREAM_CACHE_MAX:
                oldest = next(iter(self._stream_cache))
                del self._stream_cache[oldest]
            self._stream_cache[video_id] = (url, time.time())
            log.info("Music video found for %s – %s: youtube/%s", artist, title, video_id)
        else:
            log.info("No stream URL for youtube/%s (%s – %s)", video_id, artist, title)
        return url

    async def _refresh_instances(self, session: aiohttp.ClientSession) -> None:
        """Refresh the Invidious instance list from the public registry.

        Rate-limited by INVIDIOUS_REFRESH_S so cold paths pay at most one
        registry hit per hour. On failure, mark the refresh attempt anyway
        so we don't hammer — a later total-search-failure will force a retry.
        """
        now = time.time()
        if now - self._instances_fetched_at < INVIDIOUS_REFRESH_S:
            return
        async with self._instance_refresh_lock:
            if time.time() - self._instances_fetched_at < INVIDIOUS_REFRESH_S:
                return
            try:
                async with session.get(
                    INVIDIOUS_REGISTRY_URL,
                    timeout=aiohttp.ClientTimeout(total=8.0),
                    headers={"User-Agent": "BeoSound5c/1.0"},
                ) as resp:
                    if resp.status != 200:
                        log.debug("Invidious registry HTTP %d", resp.status)
                        self._instances_fetched_at = now - INVIDIOUS_REFRESH_S + INVIDIOUS_REFRESH_ON_FAIL_S
                        return
                    data = await resp.json()
            except Exception as e:
                log.debug("Invidious registry fetch failed: %s", e)
                self._instances_fetched_at = now - INVIDIOUS_REFRESH_S + INVIDIOUS_REFRESH_ON_FAIL_S
                return

            fresh = []
            for entry in data:
                try:
                    _, info = entry
                    if info.get("type") == "https" and info.get("uri"):
                        fresh.append(info["uri"].rstrip("/"))
                except Exception:
                    continue
            if not fresh:
                self._instances_fetched_at = now - INVIDIOUS_REFRESH_S + INVIDIOUS_REFRESH_ON_FAIL_S
                return

            # Preserve existing head-of-list order (recently-successful first),
            # then append any registry entries we didn't know about.
            known = set(self._instances)
            merged = list(self._instances) + [u for u in fresh if u not in known]
            self._instances = merged
            self._instances_fetched_at = time.time()
            log.info("Refreshed Invidious instance list: %d known, %d from registry",
                     len(merged), len(fresh))

    def _promote(self, instance: str) -> None:
        """Move instance to front of the list so future searches try it first."""
        try:
            self._instances.remove(instance)
        except ValueError:
            return
        self._instances.insert(0, instance)

    async def _search(self, artist: str, title: str,
                      session: aiohttp.ClientSession) -> str | None:
        """Search via Invidious API, return first suitable video_id or None."""
        await self._refresh_instances(session)

        q = f"{artist} {title} official music video"
        params = {"q": q, "type": "video", "fields": "videoId,title,lengthSeconds"}

        for instance in list(self._instances):
            url = f"{instance}/api/v1/search?{urllib.parse.urlencode(params)}"
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=8.0),
                    headers={"User-Agent": "BeoSound5c/1.0"},
                ) as resp:
                    if resp.status != 200:
                        log.debug("Invidious search %s → HTTP %d", instance, resp.status)
                        continue
                    results = await resp.json()
            except Exception as e:
                log.debug("Invidious search %s failed: %s", instance, e)
                continue

            if not isinstance(results, list):
                continue  # HTML error page or auth wall masquerading as JSON

            self._promote(instance)

            for item in results:
                duration = item.get("lengthSeconds", 0)
                if duration < MIN_DURATION_S:
                    continue  # skip shorts and clips
                video_id = item.get("videoId")
                if video_id:
                    log.info("Music video candidate for %s – %s: youtube/%s (%.0fs)",
                             artist, title, video_id, duration)
                    return video_id

            # Got a response but no suitable result — cache this as "no video"
            log.info("No suitable music video for %s – %s (all results too short or missing)",
                     artist, title)
            return ""  # don't try other instances for search; result set is the same

        # Total Invidious failure — force a registry refresh on next call, then
        # fall back to yt-dlp (hits YouTube directly, same clients as stream resolution).
        self._instances_fetched_at = 0.0
        log.info("All Invidious instances unreachable; falling back to yt-dlp (%s – %s)",
                 artist, title)
        vid = await self._search_ytdlp(q)
        if vid:
            log.info("Music video candidate (yt-dlp) for %s – %s: youtube/%s",
                     artist, title, vid)
            return vid
        if vid == "":
            log.info("yt-dlp search returned no suitable result for %s – %s", artist, title)
            return ""  # searched via yt-dlp, genuinely no match — cache as "no video"
        log.info("yt-dlp search also unavailable (%s – %s)", artist, title)
        return None   # both paths failed — do NOT cache; retry on next track play

    async def _search_ytdlp(self, q: str) -> str | None:
        """Search YouTube directly via yt-dlp. Returns video_id, "" (no match), or None (error).

        Used as a fallback when all Invidious instances are unreachable.
        Runs in an executor (blocking subprocess). `--flat-playlist` keeps it
        light — we get id + duration without fetching per-video metadata.
        """
        loop = asyncio.get_running_loop()
        for ytdlp in _YTDLP_BINS:
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda b=ytdlp: subprocess.run(
                        [b, f"ytsearch5:{q}",
                         "--flat-playlist",
                         "--print", "%(id)s|%(duration)s",
                         "--no-warnings", "--quiet"],
                        capture_output=True, text=True, timeout=15,
                    ),
                )
            except FileNotFoundError:
                continue  # try next path
            except Exception as e:
                log.debug("yt-dlp search %s failed: %s", ytdlp, e)
                continue
            if result.returncode != 0:
                log.debug("yt-dlp search %s exit %d: %s", ytdlp, result.returncode,
                          result.stderr.strip()[:120])
                continue
            for line in result.stdout.strip().splitlines():
                parts = line.split("|", 1)
                if len(parts) != 2:
                    continue
                vid, dur_str = parts
                try:
                    duration = float(dur_str)
                except ValueError:
                    continue  # "NA" for live streams, etc.
                if duration < MIN_DURATION_S:
                    continue
                if vid:
                    return vid
            return ""  # yt-dlp ran but no result matched — treat as "no video"
        return None  # yt-dlp not installed or every attempt raised

    async def _resolve_stream(self, video_id: str,
                               session: aiohttp.ClientSession) -> str | None:
        """Extract a direct stream URL via yt-dlp.

        Runs in a thread executor (blocking subprocess). Forces the plain
        Android client: yt-dlp's default (Android VR) hands back DASH URLs
        that googlevideo only serves for bounded Range requests under ~10 MB,
        which a <video> element can't produce — Chromium's open-ended fetch
        gets a 403 and the element reports a format error. The Android client
        returns a progressive muxed stream (itag 18, 360p) that plays from a
        plain src. Falls back through known binary locations.
        """
        loop = asyncio.get_running_loop()
        for ytdlp in _YTDLP_BINS:
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda b=ytdlp: subprocess.run(
                        [b, "--get-url",
                         "--extractor-args", "youtube:player_client=android",
                         "-f", "best[ext=mp4][height<=720]/best[height<=720]",
                         "--no-playlist", "--quiet", "--no-warnings",
                         "--", video_id],
                        capture_output=True, text=True, timeout=30,
                    ),
                )
                if result.returncode == 0:
                    url = result.stdout.strip().split("\n")[0]
                    if url:
                        log.info("yt-dlp stream resolved for youtube/%s", video_id)
                        return url
                log.debug("yt-dlp %s exit %d: %s", ytdlp, result.returncode,
                          result.stderr.strip()[:120])
            except FileNotFoundError:
                continue  # try next path
            except Exception as e:
                log.debug("yt-dlp %s failed: %s", ytdlp, e)
                continue

        log.info("yt-dlp not available or failed for youtube/%s", video_id)
        return None
