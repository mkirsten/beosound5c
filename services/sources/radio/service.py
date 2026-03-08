#!/usr/bin/env python3
"""
BeoSound 5c Radio Source (beo-source-radio)

Internet radio browser and player using the Radio Browser API.
Supports browsing by popular, countries, genres, languages, and favourites.
Playback works across all player types (Sonos, BlueSound, local mpv).

Port: 8779
"""

import asyncio
import json
import logging
import os
import sys
import time
import urllib.parse
from collections import OrderedDict

from aiohttp import web, ClientSession

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib.config import cfg
from lib.source_base import SourceBase

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
log = logging.getLogger('beo-radio')

FAVOURITES_PATH_PROD = "/etc/beosound5c/radio_favourites.json"
FAVOURITES_PATH_DEV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "radio_favourites.json")

# Cache TTLs in seconds
CACHE_TTL_CATEGORIES = 3600  # 1 hour for country/genre/language lists
CACHE_TTL_STATIONS = 300     # 5 minutes for station lists
CACHE_TTL_CURATED = 86400    # 24 hours for curated lists (rarely change)

# Curated station UUIDs — best quality stream picked when duplicates exist
CURATED_SVERIGE = [
    "960c660b-0601-11e8-ae97-52543be04c81",  # Sveriges Radio P1 (AAC 312k)
    "960c62c1-0601-11e8-ae97-52543be04c81",  # Sveriges Radio P2 (AAC 328k)
    "960c4e01-0601-11e8-ae97-52543be04c81",  # Sveriges Radio P3 (AAC 312k)
    "962c1da9-0601-11e8-ae97-52543be04c81",  # Sveriges Radio P4 Plus (AAC 272k)
    "96342772-0601-11e8-ae97-52543be04c81",  # Mix Megapol
    "50d2c7dd-dec7-4169-84a1-a2f1614278ad",  # Rix FM
    "96414222-0601-11e8-ae97-52543be04c81",  # NRJ Sweden
    "9642ad8b-0601-11e8-ae97-52543be04c81",  # Rockklassiker 106.7
    "168e0796-3b97-479c-949d-b1871ef07379",  # Bandit Rock
    "961d9ecf-0601-11e8-ae97-52543be04c81",  # Guldkanalen
    "2f869fa1-9f35-4ab3-a417-e8cef6880f48",  # Lugna Favoriter
    "49b761c3-0564-4501-857f-f1ee6831b387",  # Star FM
    "f4fcca1a-ba7e-11e9-acb2-52543be04c81",  # Pirate Rock
    "d077ae1e-60ef-422b-b9ac-159e56b319d4",  # Svensk Folkmusik (AkkA)
    "8b00bcfc-4d94-11ea-b877-52543be04c81",  # Retro FM Skåne
]

CURATED_DANMARK = [
    "960f5a18-0601-11e8-ae97-52543be04c81",  # DR P1
    "960f5af4-0601-11e8-ae97-52543be04c81",  # DR P2
    "b0f1b100-23b5-4c7b-bdb1-a2c68006d6bf",  # DR P3 (AAC 324k)
    "960f5358-0601-11e8-ae97-52543be04c81",  # DR P4 København
    "9610bcba-0601-11e8-ae97-52543be04c81",  # DR P5
    "9610bd91-0601-11e8-ae97-52543be04c81",  # DR P6 BEAT
    "9298e58e-3dd2-418c-bd39-6798f59b8b10",  # DR P8 Jazz (AAC 324k)
    "9610c1ca-0601-11e8-ae97-52543be04c81",  # DR Nyheder
    "963cba5e-0601-11e8-ae97-52543be04c81",  # Radio Soft
    "1eb0a70c-2cc1-11e9-a35e-52543be04c81",  # Nova 100% Dansk
    "7a17dda6-45b5-11e8-8919-52543be04c81",  # Classic FM
    "f5345ab1-45b3-11e8-8919-52543be04c81",  # Skala FM
    "6397fc3c-fca0-11e9-bbf2-52543be04c81",  # Radio4
    "0d939aa0-cce8-4841-92fe-1a03d36da0d3",  # Classic Rock Danmark
    "632fe760-a124-4385-9061-6acb4bd14d0f",  # The Voice
]

# Inline SVG data URIs for flag category icons (Nordic cross, rounded corners)
FLAG_SVERIGE = "data:image/svg+xml,%3Csvg viewBox='0 0 128 128' xmlns='http://www.w3.org/2000/svg'%3E%3Crect width='128' height='128' rx='20' fill='%23005293'/%3E%3Crect y='52' width='128' height='24' fill='%23FECC02'/%3E%3Crect x='40' y='0' width='24' height='128' fill='%23FECC02'/%3E%3C/svg%3E"
FLAG_DANMARK = "data:image/svg+xml,%3Csvg viewBox='0 0 128 128' xmlns='http://www.w3.org/2000/svg'%3E%3Crect width='128' height='128' rx='20' fill='%23C8102E'/%3E%3Crect y='52' width='128' height='24' fill='white'/%3E%3Crect x='40' y='0' width='24' height='128' fill='white'/%3E%3C/svg%3E"

# Sveriges Radio channel mapping — Radio Browser UUID → SR API channel ID
SR_CHANNEL_MAP = {
    "960c660b-0601-11e8-ae97-52543be04c81": {"sr_id": 132, "name": "P1"},
    "960c62c1-0601-11e8-ae97-52543be04c81": {"sr_id": 163, "name": "P2"},
    "960c4e01-0601-11e8-ae97-52543be04c81": {"sr_id": 164, "name": "P3"},
    "962c1da9-0601-11e8-ae97-52543be04c81": {"sr_id": 4951, "name": "P4 Plus"},
}

SR_POLL_INTERVAL = 60  # seconds


class RadioService(SourceBase):
    """Internet radio browser and player."""

    id = "radio"
    name = "Radio"
    port = 8779
    player = "local"
    action_map = {
        "play": "toggle",
        "pause": "toggle",
        "go": "toggle",
        "next": "next",
        "prev": "prev",
        "left": "prev",
        "right": "next",
        "up": "next",
        "red": "toggle_favourite",
        "blue": "remove_favourite",
        "down": "prev",
        "stop": "stop",
        "0": "digit", "1": "digit", "2": "digit",
        "3": "digit", "4": "digit", "5": "digit",
        "6": "digit", "7": "digit", "8": "digit",
        "9": "digit",
    }

    def __init__(self):
        super().__init__()
        self._stations: list[dict] = []       # snapshotted on play — stable for next/prev
        self._browse_stations: list[dict] = []  # updated on every browse
        self._current_index: int = 0
        self._favourites: list[dict] = []
        self._cache: dict[str, tuple[float, any]] = {}
        self._favicon_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._api_base = "https://de1.api.radio-browser.info"
        self._api_session: ClientSession | None = None
        self._playing_state = "stopped"  # "playing", "paused", "stopped"
        self._current_station: dict | None = None
        # Sveriges Radio now-playing
        self._sr_now_playing: dict[str, dict] = {}  # uuid → {program, title, image}
        self._sr_channel_images: dict[str, bytes] = {}  # uuid → PNG bytes
        self._sr_artwork_cache: dict[str, tuple[str, bytes]] = {}  # uuid → (title, image bytes)
        self._sr_poll_task: asyncio.Task | None = None

    async def on_start(self):
        self._api_session = ClientSession(
            headers={"User-Agent": "BeoSound5c/1.0"}
        )
        self._load_favourites()

        # Detect remote player from config (not runtime probe — avoids
        # startup race where the player service isn't ready yet)
        player_type = cfg("player", "type", default="local")
        if player_type != "local":
            self.player = "remote"
            log.info("Playback mode: remote (player.type=%s)", player_type)
        else:
            log.info("Playback mode: local (mpv)")

        await self.register("available")
        self._sr_poll_task = asyncio.create_task(self._sr_poll_loop())
        log.info("Radio source ready (%d favourites)", len(self._favourites))

    async def on_stop(self):
        if self._sr_poll_task:
            self._sr_poll_task.cancel()
        if self._api_session:
            await self._api_session.close()

    def add_routes(self, app):
        app.router.add_get("/browse", self._handle_browse)
        app.router.add_get("/favicon", self._handle_favicon)
        app.router.add_get("/sr-artwork", self._handle_sr_artwork)

    # ── Browse API ──

    async def _handle_browse(self, request):
        path = request.query.get("path", "").strip("/")

        try:
            result = await self._browse(path)
            return web.json_response(result, headers=self._cors_headers())
        except Exception as e:
            log.exception("Browse error for path=%s", path)
            return web.json_response(
                {"error": str(e)}, status=500, headers=self._cors_headers()
            )

    async def _browse(self, path: str) -> dict:
        parts = path.split("/") if path else []

        if not parts:
            return self._root_categories()

        category = parts[0]

        if category == "popular":
            stations = await self._api_get(
                "/json/stations/topvote?limit=100&hidebroken=true",
                ttl=CACHE_TTL_STATIONS,
            )
            return self._station_list("Popular", "popular", "", stations)

        if category == "sverige":
            stations = await self._fetch_curated("Sweden", CURATED_SVERIGE)
            return self._station_list("Swedish", "sverige", "", stations)

        if category == "danmark":
            stations = await self._fetch_curated("Denmark", CURATED_DANMARK)
            return self._station_list("Danish", "danmark", "", stations)

        if category == "countries":
            if len(parts) == 1:
                countries = await self._api_get(
                    "/json/countries?order=name&hidebroken=true",
                    ttl=CACHE_TTL_CATEGORIES,
                )
                countries = [c for c in countries if c.get("stationcount", 0) > 20]
                return {
                    "path": "countries",
                    "parent": "",
                    "name": "Countries",
                    "items": [
                        {
                            "type": "category",
                            "name": c["name"],
                            "id": f"countries/{c['name']}",
                            "path": f"countries/{c['name']}",
                            "count": c.get("stationcount", 0),
                        }
                        for c in countries
                    ],
                }
            country = "/".join(parts[1:])
            stations = await self._api_get(
                f"/json/stations/bycountry/{urllib.parse.quote(country)}?order=votes&limit=100&hidebroken=true",
                ttl=CACHE_TTL_STATIONS,
            )
            return self._station_list(country, f"countries/{country}", "countries", stations)

        if category == "genres":
            if len(parts) == 1:
                tags = await self._api_get(
                    "/json/tags?order=stationcount&reverse=true&limit=80&hidebroken=true",
                    ttl=CACHE_TTL_CATEGORIES,
                )
                tags = [t for t in tags if t.get("stationcount", 0) > 20]
                return {
                    "path": "genres",
                    "parent": "",
                    "name": "Genres",
                    "items": [
                        {
                            "type": "category",
                            "name": t["name"].title(),
                            "id": f"genres/{t['name']}",
                            "path": f"genres/{t['name']}",
                            "count": t.get("stationcount", 0),
                        }
                        for t in tags
                    ],
                }
            tag = "/".join(parts[1:])
            stations = await self._api_get(
                f"/json/stations/bytag/{urllib.parse.quote(tag)}?order=votes&limit=100&hidebroken=true",
                ttl=CACHE_TTL_STATIONS,
            )
            return self._station_list(tag.title(), f"genres/{tag}", "genres", stations)

        if category == "languages":
            if len(parts) == 1:
                langs = await self._api_get(
                    "/json/languages?order=stationcount&reverse=true&hidebroken=true",
                    ttl=CACHE_TTL_CATEGORIES,
                )
                langs = [l for l in langs if l.get("stationcount", 0) > 20]
                return {
                    "path": "languages",
                    "parent": "",
                    "name": "Languages",
                    "items": [
                        {
                            "type": "category",
                            "name": l["name"].title(),
                            "id": f"languages/{l['name']}",
                            "path": f"languages/{l['name']}",
                            "count": l.get("stationcount", 0),
                        }
                        for l in langs
                    ],
                }
            lang = "/".join(parts[1:])
            stations = await self._api_get(
                f"/json/stations/bylanguage/{urllib.parse.quote(lang)}?order=votes&limit=100&hidebroken=true",
                ttl=CACHE_TTL_STATIONS,
            )
            return self._station_list(lang.title(), f"languages/{lang}", "languages", stations)

        if category == "favourites":
            self._browse_stations = list(self._favourites)
            return {
                "path": "favourites",
                "parent": "",
                "name": "Favourites",
                "items": [self._station_to_item(s) for s in self._favourites],
            }

        return {"path": path, "parent": "", "name": "Unknown", "items": []}

    def _root_categories(self) -> dict:
        return {
            "path": "",
            "parent": None,
            "name": "Radio",
            "items": [
                {"type": "category", "name": "Popular", "id": "popular", "path": "popular",
                 "icon": "star", "color": "#F9CA24"},
                {"type": "category", "name": "Swedish", "id": "sverige", "path": "sverige",
                 "image": FLAG_SVERIGE},
                {"type": "category", "name": "Danish", "id": "danmark", "path": "danmark",
                 "image": FLAG_DANMARK},
                {"type": "category", "name": "Favourites", "id": "favourites", "path": "favourites",
                 "icon": "heart", "color": "#FF6B6B"},
                {"type": "category", "name": "Countries", "id": "countries", "path": "countries",
                 "icon": "globe", "color": "#A29BFE"},
                {"type": "category", "name": "Genres", "id": "genres", "path": "genres",
                 "icon": "music-notes", "color": "#FD79A8"},
                {"type": "category", "name": "Languages", "id": "languages", "path": "languages",
                 "icon": "translate", "color": "#74B9FF"},
            ],
        }

    def _station_list(self, name, path, parent, stations) -> dict:
        items = [self._station_to_item(s) for s in (stations or [])]
        # Store as the browse context — snapshotted into _stations on play
        self._browse_stations = stations or []
        return {"path": path, "parent": parent, "name": name, "items": items}

    def _station_to_item(self, s) -> dict:
        tags = s.get("tags", "")
        tag_list = [t.strip() for t in tags.split(",") if t.strip()][:3]
        codec = s.get("codec", "")
        bitrate = s.get("bitrate", 0)
        codec_str = f"{codec} {bitrate}kbps" if codec and bitrate else codec or ""

        subtitle_parts = []
        if tag_list:
            subtitle_parts.append(", ".join(tag_list))
        elif s.get("country"):
            subtitle_parts.append(s["country"])
        if codec_str:
            subtitle_parts.append(codec_str)

        return {
            "type": "station",
            "name": s.get("name", "Unknown"),
            "id": s.get("stationuuid", ""),
            "stationuuid": s.get("stationuuid", ""),
            "url_resolved": s.get("url_resolved", s.get("url", "")),
            "favicon": s.get("favicon", ""),
            "country": s.get("country", ""),
            "tags": tags,
            "codec": codec,
            "bitrate": bitrate,
            "votes": s.get("votes", 0),
            "subtitle": " · ".join(subtitle_parts),
        }

    # ── Radio Browser API client ──

    async def _api_get(self, endpoint: str, ttl: int = CACHE_TTL_STATIONS) -> list:
        now = time.time()
        cached = self._cache.get(endpoint)
        if cached and (now - cached[0]) < ttl:
            return cached[1]

        url = f"{self._api_base}{endpoint}"
        try:
            async with self._api_session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._cache[endpoint] = (now, data)
                    return data
                log.warning("API %s returned %d", endpoint, resp.status)
                return cached[1] if cached else []
        except Exception as e:
            log.warning("API %s failed: %s", endpoint, e)
            return cached[1] if cached else []

    async def _fetch_curated(self, country: str, uuids: list[str]) -> list:
        """Fetch curated stations by country, filtered and ordered by UUID list."""
        cache_key = f"_curated_{country}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < CACHE_TTL_CURATED:
            return cached[1]

        all_stations = await self._api_get(
            f"/json/stations/bycountryexact/{urllib.parse.quote(country)}"
            f"?limit=500&hidebroken=true",
            ttl=CACHE_TTL_CURATED,
        )
        uuid_set = set(uuids)
        uuid_order = {u: i for i, u in enumerate(uuids)}
        filtered = [s for s in all_stations if s.get("stationuuid") in uuid_set]
        filtered.sort(key=lambda s: uuid_order.get(s.get("stationuuid"), 999))
        self._cache[cache_key] = (now, filtered)
        return filtered

    # ── Favicon proxy ──

    async def _handle_favicon(self, request):
        url = request.query.get("url", "")
        if not url or not url.startswith(("http://", "https://")):
            return web.Response(status=400, headers=self._cors_headers())

        # Block requests to internal networks
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if host in ("localhost", "127.0.0.1", "::1") or host.startswith("10.") or host.startswith("192.168.") or host.startswith("172."):
            return web.Response(status=403, headers=self._cors_headers())

        # Check cache
        if url in self._favicon_cache:
            data, ct = self._favicon_cache[url]
            self._favicon_cache.move_to_end(url)
            return web.Response(body=data, content_type=ct, headers={
                **self._cors_headers(), "Cache-Control": "public, max-age=86400"
            })

        try:
            async with self._api_session.get(url, timeout=5) as resp:
                if resp.status != 200:
                    return web.Response(status=404, headers=self._cors_headers())
                ct = resp.content_type or "image/png"
                data = await resp.read()
                if len(data) > 500_000:
                    return web.Response(status=404, headers=self._cors_headers())

                # LRU eviction
                self._favicon_cache[url] = (data, ct)
                if len(self._favicon_cache) > 200:
                    self._favicon_cache.popitem(last=False)

                return web.Response(body=data, content_type=ct, headers={
                    **self._cors_headers(), "Cache-Control": "public, max-age=86400"
                })
        except Exception:
            return web.Response(status=404, headers=self._cors_headers())

    # ── Commands ──

    async def handle_command(self, cmd, data) -> dict:
        if cmd == "play_station":
            uuid = data.get("stationuuid", "")
            station = self._find_station(uuid)
            if not station:
                return {"status": "error", "message": "Station not found"}
            await self._play_station(station)

        elif cmd == "toggle":
            if self._playing_state == "playing":
                await self.player_pause()
                self._playing_state = "paused"
                await self.register("paused")
                if self._current_station:
                    await self.post_media_update(
                        **self._build_meta(self._current_station), state="paused"
                    )
            elif self._playing_state == "paused":
                await self.player_resume()
                self._playing_state = "playing"
                await self.register("playing")
                if self._current_station:
                    await self.post_media_update(
                        **self._build_meta(self._current_station), state="playing"
                    )
            elif self._current_station:
                await self._play_station(self._current_station)

        elif cmd == "next":
            if self._stations and self._current_station:
                self._current_index = (self._current_index + 1) % len(self._stations)
                await self._play_station(self._stations[self._current_index])

        elif cmd == "prev":
            if self._stations and self._current_station:
                self._current_index = (self._current_index - 1) % len(self._stations)
                await self._play_station(self._stations[self._current_index])

        elif cmd == "stop":
            await self.player_stop()
            self._playing_state = "stopped"
            self._current_station = None
            await self.register("available")

        elif cmd == "digit":
            digit = int(data.get("action", "1"))
            idx = (digit - 1) if digit >= 1 else 9  # 1→0, 2→1, ..., 9→8, 0→9
            if idx < len(self._favourites):
                self._stations = list(self._favourites)
                self._current_index = idx
                await self._play_station(self._favourites[idx])
            else:
                log.info("No favourite at digit %d (have %d)", digit, len(self._favourites))

        elif cmd == "toggle_favourite":
            uuid = data.get("stationuuid", "")
            if uuid:
                station = self._find_station(uuid)
            else:
                station = self._current_station
            if station:
                return self._toggle_favourite(station)
            return {"status": "error", "message": "No station to favourite"}

        elif cmd == "remove_favourite":
            station = self._current_station
            if station:
                uuid = station.get("stationuuid", "")
                if any(s.get("stationuuid") == uuid for s in self._favourites):
                    return self._toggle_favourite(station)  # removes since it exists
                return {"status": "ok", "favourite": False}  # not a favourite, no-op
            return {"status": "error", "message": "No station playing"}

        else:
            return {"status": "error", "message": f"Unknown: {cmd}"}

        return {"status": "ok"}

    async def handle_resync(self) -> dict:
        state = self._playing_state if self._playing_state in ('playing', 'paused') else 'available'
        await self.register(state)
        await self._resync_media()
        return {'status': 'ok', 'resynced': True}

    async def handle_activate(self, data: dict) -> dict | None:
        if self._current_station or self._favourites:
            return await super().handle_activate(data)
        # No station ever played and no favourites — just stay available
        await self.register("available")

    async def activate_playback(self):
        if self._current_station:
            await self._play_station(self._current_station)
        elif self._favourites:
            # First activation with no prior play — start first favourite
            self._stations = list(self._favourites)
            self._current_index = 0
            await self._play_station(self._favourites[0])

    async def handle_status(self) -> dict:
        return {
            "source": self.id,
            "state": self._playing_state,
            "station": self._current_station.get("name") if self._current_station else None,
            "favourites": len(self._favourites),
        }

    def _find_station(self, uuid: str) -> dict | None:
        for s in self._browse_stations:
            if s.get("stationuuid") == uuid:
                return s
        for s in self._stations:
            if s.get("stationuuid") == uuid:
                return s
        for s in self._favourites:
            if s.get("stationuuid") == uuid:
                return s
        return None

    async def _play_station(self, station: dict):
        url = station.get("url_resolved", station.get("url", ""))
        if not url:
            log.warning("No URL for station %s", station.get("name"))
            return

        self._current_station = station
        # Snapshot browse list for next/prev cycling (only when playing from browse)
        uuid = station.get("stationuuid", "")
        found_in_browse = any(s.get("stationuuid") == uuid for s in self._browse_stations)
        if found_in_browse and self._browse_stations:
            self._stations = list(self._browse_stations)
        # Update current index in station list
        for i, s in enumerate(self._stations):
            if s.get("stationuuid") == uuid:
                self._current_index = i
                break

        meta = self._build_meta(station)

        # Pre-broadcast metadata
        await self.register("playing", auto_power=True)
        await self.post_media_update(**meta, state="playing", reason="track_change")

        # Play via player service (direct favicon URL for Sonos — it can fetch from internet)
        await self.player_play(
            url=url,
            meta={
                "title": meta["title"],
                "artist": meta["artist"],
                "artwork_url": station.get("favicon", ""),
            },
            radio=True,
        )
        self._playing_state = "playing"

    def _build_meta(self, station: dict) -> dict:
        uuid = station.get("stationuuid", "")

        # SR now-playing override
        sr_data = self._sr_now_playing.get(uuid)
        if uuid in SR_CHANNEL_MAP and sr_data:
            channel_name = SR_CHANNEL_MAP[uuid]["name"]
            program_title = sr_data.get("title", "")
            if program_title and channel_name.lower() not in program_title.lower():
                title = f"{channel_name}: {program_title}"
            elif program_title:
                title = program_title
            else:
                title = channel_name
            # Cache-bust so UI reloads artwork when program changes
            cb = hash(sr_data.get("title", "") + sr_data.get("program", "")) & 0xFFFFFFFF
            artwork = f"http://localhost:{self.port}/sr-artwork?uuid={uuid}&v={cb}"
            return {
                "title": title,
                "artist": "Sveriges Radio",
                "album": sr_data.get("description", ""),
                "artwork": artwork,
            }

        tags = station.get("tags", "")
        tag_list = [t.strip() for t in tags.split(",") if t.strip()][:3]
        country = station.get("country", "")
        codec = station.get("codec", "")
        bitrate = station.get("bitrate", 0)

        artist = ", ".join(tag_list) if tag_list else country
        album_parts = []
        if country:
            album_parts.append(country)
        if codec and bitrate:
            album_parts.append(f"{codec} {bitrate}kbps")
        elif codec:
            album_parts.append(codec)
        album = " · ".join(album_parts)

        favicon = station.get("favicon", "")
        artwork = f"http://localhost:{self.port}/favicon?url={favicon}" if favicon else ""

        return {"title": station.get("name", ""), "artist": artist, "album": album,
                "artwork": artwork}

    # ── Sveriges Radio now-playing ──

    async def _sr_poll_loop(self):
        """Background poller for SR now-playing metadata."""
        await self._sr_fetch_channel_images()
        while True:
            try:
                await self._sr_poll_now_playing()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("SR poll error (will retry)")
            await asyncio.sleep(SR_POLL_INTERVAL)

    async def _sr_fetch_channel_images(self):
        """Fetch channel logos from SR API (once)."""
        for uuid, info in SR_CHANNEL_MAP.items():
            if uuid in self._sr_channel_images:
                continue
            try:
                url = f"https://api.sr.se/api/v2/channels/{info['sr_id']}?format=json"
                async with self._api_session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    image_url = data.get("channel", {}).get("image")
                    if not image_url:
                        continue
                async with self._api_session.get(image_url, timeout=10) as resp:
                    if resp.status == 200:
                        self._sr_channel_images[uuid] = await resp.read()
                        log.info("Cached SR channel image for %s", info["name"])
            except Exception as e:
                log.warning("Failed to fetch SR channel image for %s: %s", info["name"], e)

    async def _sr_poll_now_playing(self):
        """Fetch current program for all SR channels, trigger update on change."""
        for uuid, info in SR_CHANNEL_MAP.items():
            try:
                url = (f"https://api.sr.se/api/v2/scheduledepisodes/rightnow"
                       f"?channelid={info['sr_id']}&format=json")
                async with self._api_session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                current = data.get("channel", {}).get("currentscheduledepisode", {})
                program = current.get("program", {}).get("name", "")
                title = current.get("title", "")
                image = (current.get("imageurl")
                         or current.get("socialimage")
                         or current.get("imageurltemplate", ""))

                description = current.get("description", "")

                old = self._sr_now_playing.get(uuid, {})
                new_entry = {"program": program, "title": title, "image": image,
                             "description": description}
                self._sr_now_playing[uuid] = new_entry

                # Invalidate composited artwork cache if program changed
                if old.get("title") != title or old.get("program") != program:
                    self._sr_artwork_cache.pop(uuid, None)
                    # Push live update if this station is currently playing
                    if (self._current_station
                            and self._current_station.get("stationuuid") == uuid
                            and self._playing_state == "playing"):
                        meta = self._build_meta(self._current_station)
                        await self.post_media_update(**meta, state="playing")
                        log.info("SR live update: %s → %s: %s", info["name"], program, title)

            except Exception as e:
                log.warning("SR poll failed for %s: %s", info["name"], e)

    async def _sr_get_artwork(self, uuid: str) -> bytes | None:
        """Return SR program artwork for a channel, falling back to channel logo."""
        sr_data = self._sr_now_playing.get(uuid)
        if not sr_data:
            return self._sr_channel_images.get(uuid)

        # Return cached if program hasn't changed
        cached = self._sr_artwork_cache.get(uuid)
        if cached and cached[0] == sr_data.get("title", ""):
            return cached[1]

        program_image = sr_data.get("image", "")

        # Fetch program artwork
        if program_image:
            try:
                async with self._api_session.get(program_image, timeout=10) as resp:
                    if resp.status == 200:
                        result = await resp.read()
                        self._sr_artwork_cache[uuid] = (sr_data.get("title", ""), result)
                        return result
            except Exception:
                pass

        # Fallback to high-res channel logo
        return self._sr_channel_images.get(uuid)

    async def _handle_sr_artwork(self, request):
        uuid = request.query.get("uuid", "")
        if uuid not in SR_CHANNEL_MAP:
            return web.Response(status=404, headers=self._cors_headers())

        data = await self._sr_get_artwork(uuid)
        if not data:
            return web.Response(status=404, headers=self._cors_headers())

        # Detect content type from data
        ct = "image/jpeg"
        if data[:4] == b'\x89PNG':
            ct = "image/png"
        elif data[:4] == b'<svg' or data[:5] == b'<?xml':
            ct = "image/svg+xml"

        return web.Response(
            body=data, content_type=ct,
            headers={**self._cors_headers(), "Cache-Control": "public, max-age=60"},
        )

    # ── Favourites ──

    def _favourites_path(self) -> str:
        if os.path.exists(os.path.dirname(FAVOURITES_PATH_PROD)):
            return FAVOURITES_PATH_PROD
        return FAVOURITES_PATH_DEV

    def _load_favourites(self):
        path = self._favourites_path()
        try:
            with open(path) as f:
                self._favourites = json.load(f)
            log.info("Loaded %d favourites from %s", len(self._favourites), path)
        except FileNotFoundError:
            self._favourites = []
        except Exception as e:
            log.warning("Failed to load favourites: %s", e)
            self._favourites = []

    def _save_favourites(self):
        path = self._favourites_path()
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._favourites, f, indent=2)
            os.replace(tmp, path)
            log.info("Saved %d favourites to %s", len(self._favourites), path)
        except Exception as e:
            log.warning("Failed to save favourites: %s", e)

    def _toggle_favourite(self, station: dict) -> dict:
        uuid = station.get("stationuuid", "")
        existing = [i for i, s in enumerate(self._favourites) if s.get("stationuuid") == uuid]
        if existing:
            for i in reversed(existing):
                self._favourites.pop(i)
            self._save_favourites()
            return {"status": "ok", "favourite": False}
        else:
            self._favourites.append({
                "stationuuid": station.get("stationuuid", ""),
                "name": station.get("name", ""),
                "url_resolved": station.get("url_resolved", station.get("url", "")),
                "favicon": station.get("favicon", ""),
                "country": station.get("country", ""),
                "tags": station.get("tags", ""),
                "codec": station.get("codec", ""),
                "bitrate": station.get("bitrate", 0),
                "votes": station.get("votes", 0),
            })
            self._save_favourites()
            return {"status": "ok", "favourite": True}


if __name__ == "__main__":
    service = RadioService()
    asyncio.run(service.run())
