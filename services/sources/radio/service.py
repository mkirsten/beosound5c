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
import re
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
LAST_STATION_PATH_PROD = "/etc/beosound5c/radio_last_station.json"
LAST_STATION_PATH_DEV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "radio_last_station.json")

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


# Short-name suggestion — generates an alias for play_by_name (BeoRemote
# menu label matching). Heuristic: strip codec/bitrate noise and bracketed
# annotations, normalize separators, apply known broadcaster prefixes,
# then cap length. Examples:
#   "Sveriges Radio - P3"                    → "SR P3"
#   "Danmarks Radio P1"                      → "DR P1"
#   "BBC Radio 4"                            → "BBC R4"
#   "Radio Paradise Main Mix (EU) 320k AAC"  → "RP Main Mix"
#   "SomaFM Groove Salad"                    → "Soma Groove"
#   "Lugna Favoriter"                        → "Lugna"
_SHORT_NAME_PREFIXES = [
    (re.compile(r"^Sveriges\s+Radio\b\s*", re.I),         "SR "),
    (re.compile(r"^Danmarks\s+Radio\b\s*", re.I),         "DR "),
    (re.compile(r"^Norsk\s+Rikskringkasting\b\s*", re.I), "NRK "),
    (re.compile(r"^BBC\s+Radio\b\s*", re.I),              "BBC R"),
    (re.compile(r"^Radio\s+Paradise\b\s*", re.I),         "RP "),
    (re.compile(r"^Soma\s*FM\b\s*", re.I),                "Soma "),
]
_SHORT_NAME_MAX_LEN = 14
# Trailing codec/bitrate junk ("AAC 320", "320kbps", "MP3", "320k AAC", etc.)
_SHORT_NAME_TRAIL_PATTERNS = [
    re.compile(r"\s+\d+\s*k(?:bps)?\s+(?:AAC|MP3|OGG|FLAC|HE-AAC)\s*$", re.I),
    re.compile(r"\s+\d+\s+(?:AAC|MP3|OGG|FLAC|HE-AAC)\s*$", re.I),
    re.compile(r"\s+(?:AAC|MP3|OGG|FLAC|HE-AAC)(?:\s+\d+\s*k?(?:bps)?)?\s*$", re.I),
    re.compile(r"\s+\d+\s*k(?:bps)?\s*$", re.I),
]


def _suggest_short_name(name: str) -> str:
    if not name:
        return ""
    s = name.strip()
    # Drop bracketed annotations: "(EU)", "[HD]", etc.
    s = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", s)
    # Drop trailing codec/bitrate noise
    for pat in _SHORT_NAME_TRAIL_PATTERNS:
        s = pat.sub("", s)
    # Normalize "Name - P3" / "Name – P3" → "Name P3"
    s = re.sub(r"\s*[-–—]\s*", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    # Apply broadcaster prefix substitutions (first match wins)
    for pat, repl in _SHORT_NAME_PREFIXES:
        new = pat.sub(repl, s, count=1)
        if new != s:
            s = new.strip()
            break
    # Length cap — prefer first 2 words if it fits, else first word
    if len(s) > _SHORT_NAME_MAX_LEN:
        words = s.split()
        if len(words) >= 2:
            two = " ".join(words[:2])
            s = two if len(two) <= _SHORT_NAME_MAX_LEN else words[0][:_SHORT_NAME_MAX_LEN]
        else:
            s = words[0][:_SHORT_NAME_MAX_LEN]
    return s.strip()


class RadioService(SourceBase):
    """Internet radio browser and player."""

    id = "radio"
    name = "Radio"
    port = 8779
    player = "local"
    manages_queue = True
    # Base action map shared by all instances. RadioService.__init__ adds
    # color buttons (red/green/yellow/blue) to a per-instance copy only
    # when the user has bound them in config.json (radio.station_buttons).
    # Unbound color buttons fall through to the router's global handlers
    # (GREEN/YELLOW balance shortcut, BLUE→JOIN, RED→HA).
    action_map = {
        "play": "toggle",
        "pause": "toggle",
        "go": "toggle",
        "next": "next",
        "prev": "prev",
        "left": "prev",
        "right": "next",
        "up": "next",
        "down": "prev",
        "stop": "stop",
        "0": "digit", "1": "digit", "2": "digit",
        "3": "digit", "4": "digit", "5": "digit",
        "6": "digit", "7": "digit", "8": "digit",
        "9": "digit",
    }

    DIGIT_KEYS = ("1","2","3","4","5","6","7","8","9","0")
    COLOR_KEYS = ("red","green","yellow","blue")

    def __init__(self):
        super().__init__()
        # Per-instance action_map so we can attach the user's color-button
        # bindings without leaking into the class default.
        self.action_map = dict(RadioService.action_map)
        self._station_buttons: dict[str, str] = {}
        self._load_station_buttons()
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
        self._load_last_station()

        await self.register("available")
        # Pre-warm curated station caches so play_by_name is instant
        self._spawn(self._prewarm_curated(), name="prewarm_curated")
        self._sr_poll_task = asyncio.create_task(self._sr_poll_loop())
        log.info("Radio source ready (%d favourites, last=%s)",
                 len(self._favourites),
                 self._current_station.get("name") if self._current_station else "none")

    async def on_stop(self):
        if self._sr_poll_task:
            self._sr_poll_task.cancel()
        if self._api_session:
            await self._api_session.close()

    def add_routes(self, app):
        app.router.add_get("/browse", self._handle_browse)
        app.router.add_get("/favicon", self._handle_favicon)
        app.router.add_get("/sr-artwork", self._handle_sr_artwork)
        app.router.add_get("/favourites", self._handle_favourites_list)
        app.router.add_post("/favourites/short_name", self._handle_set_short_name)
        # CORS preflight — the Config UI is served from port 80 and POSTs
        # JSON here, so browsers send an OPTIONS check first.
        app.router.add_options("/favourites/short_name", self._handle_cors)

    async def _handle_favourites_list(self, request):
        """Return current favourites + button bindings for the Config UI."""
        items = [
            {
                "stationuuid": s.get("stationuuid", ""),
                "name": s.get("name", ""),
                "short_name": s.get("short_name", ""),
                "favicon": s.get("favicon", ""),
                "country": s.get("country", ""),
                "tags": s.get("tags", ""),
                "codec": s.get("codec", ""),
                "bitrate": s.get("bitrate", 0),
            }
            for s in self._favourites
        ]
        return web.json_response(
            {"favourites": items, "station_buttons": dict(self._station_buttons)},
            headers=self._cors_headers(),
        )

    async def _handle_set_short_name(self, request):
        """Set a favourite's short_name alias. {stationuuid, short_name}.

        Empty short_name = "user wants no alias" — stored as ""  so
        _load_favourites' auto-suggest backfill leaves it alone on the
        next restart (key-present means "user has decided").
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400,
                                     headers=self._cors_headers())
        uuid = (data.get("stationuuid") or "").strip()
        short = (data.get("short_name") or "").strip()
        if not uuid:
            return web.json_response({"error": "stationuuid required"}, status=400,
                                     headers=self._cors_headers())
        for s in self._favourites:
            if s.get("stationuuid") == uuid:
                s["short_name"] = short
                self._save_favourites()
                log.info("Favourite %s short_name → %r", s.get("name"), short)
                return web.json_response({"ok": True, "short_name": short},
                                         headers=self._cors_headers())
        return web.json_response({"error": "station not in favourites"}, status=404,
                                 headers=self._cors_headers())

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

    async def _prewarm_curated(self):
        """Pre-fetch curated station lists so play_by_name is instant."""
        try:
            await self._fetch_curated("Sweden", CURATED_SVERIGE)
            await self._fetch_curated("Denmark", CURATED_DANMARK)
            log.info("Curated station caches warmed")
        except Exception as e:
            log.debug("Curated prewarm failed (will fetch on demand): %s", e)

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
            if self._favourites:
                idx = self._find_in_favourites()
                if idx is not None:
                    idx = (idx + 1) % len(self._favourites)
                else:
                    idx = 0
                await self._play_station(self._favourites[idx])

        elif cmd == "prev":
            if self._favourites:
                idx = self._find_in_favourites()
                if idx is not None:
                    idx = (idx - 1) % len(self._favourites)
                else:
                    idx = len(self._favourites) - 1
                await self._play_station(self._favourites[idx])

        elif cmd == "stop":
            await self.player_stop()
            self._playing_state = "stopped"
            # Keep _current_station so source button can resume it
            await self.register("available")

        elif cmd == "digit":
            digit_str = str(data.get("action", "1"))
            station = self._resolve_station_button(digit_str)
            if station:
                self._stations = list(self._favourites)
                # Track index in favourites if present, else 0
                self._current_index = next(
                    (i for i, s in enumerate(self._favourites)
                     if s.get("stationuuid") == station.get("stationuuid")), 0)
                await self._play_station(station)
            else:
                digit = int(digit_str)
                idx = (digit - 1) if digit >= 1 else 9  # 1→0, 2→1, ..., 9→8, 0→9
                if idx < len(self._favourites):
                    self._stations = list(self._favourites)
                    self._current_index = idx
                    await self._play_station(self._favourites[idx])
                else:
                    log.info("No favourite at digit %d (have %d)", digit, len(self._favourites))

        elif cmd == "play_button":
            key = str(data.get("action", "")).lower()
            station = self._resolve_station_button(key)
            if station:
                self._stations = list(self._favourites)
                self._current_index = next(
                    (i for i, s in enumerate(self._favourites)
                     if s.get("stationuuid") == station.get("stationuuid")), 0)
                await self._play_station(station)
            else:
                log.info("Button %r has no resolvable station binding", key)

        elif cmd == "play_index":
            idx = data.get("index", 0)
            if 0 <= idx < len(self._favourites):
                self._stations = list(self._favourites)
                self._current_index = idx
                await self._play_station(self._favourites[idx])
            else:
                log.info("No favourite at index %d (have %d)", idx, len(self._favourites))

        elif cmd == "play_by_name":
            name = data.get("name", "")
            if name:
                station = await self._find_station_by_name(name)
                if station:
                    self._stations = [station]
                    self._current_index = 0
                    await self._play_station(station)
                else:
                    return {"status": "error", "message": f"Station not found: {name}"}
            else:
                return {"status": "error", "message": "Missing 'name'"}

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

        elif cmd == "add_favourite":
            # Persist a full station object (used by the Config UI when a
            # user picks a station from the browse hierarchy that wasn't
            # already in favourites, OR when adding a custom stream URL).
            station = data.get("station") or {}
            uuid = station.get("stationuuid", "")
            url = station.get("url_resolved", station.get("url", ""))
            if not uuid or not station.get("name"):
                return {"status": "error",
                        "message": "Missing 'station' with stationuuid + name"}
            # Custom entries (uuid prefixed "custom-") MUST carry a URL —
            # they have no Radio Browser fallback to look it up later.
            if uuid.startswith("custom-") and not url:
                return {"status": "error",
                        "message": "Custom station requires url_resolved"}
            if any(s.get("stationuuid") == uuid for s in self._favourites):
                return {"status": "ok", "favourite": True}  # already
            entry = {
                "stationuuid": uuid,
                "name": station.get("name", ""),
                "url_resolved": station.get("url_resolved", station.get("url", "")),
                "favicon": station.get("favicon", ""),
                "country": station.get("country", ""),
                "tags": station.get("tags", ""),
                "codec": station.get("codec", ""),
                "bitrate": station.get("bitrate", 0),
                "votes": station.get("votes", 0),
            }
            # Caller may pre-supply a short_name; otherwise auto-suggest.
            caller_short = station.get("short_name")
            if caller_short is not None:
                entry["short_name"] = caller_short
            else:
                suggested = _suggest_short_name(entry["name"])
                if suggested:
                    entry["short_name"] = suggested
            self._favourites.append(entry)
            self._save_favourites()
            return {"status": "ok", "favourite": True, "short_name": entry.get("short_name", "")}

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

    async def get_queue(self, start=0, max_items=50) -> dict:
        """Return favourites as a queue so Beo6 can browse/select stations."""
        if not self._favourites:
            return {"tracks": [], "current_index": -1, "total": 0}
        current_uuid = self._current_station.get("stationuuid") if self._current_station else None
        current_index = -1
        end = min(start + max_items, len(self._favourites))
        tracks = []
        for i in range(start, end):
            s = self._favourites[i]
            if s.get("stationuuid") == current_uuid:
                current_index = i
            tracks.append({
                "id": f"q:{i}",
                "title": s.get("name", "Unknown"),
                "artist": "",
                "album": "Radio",
                "artwork": s.get("favicon", ""),
                "index": i,
                "current": s.get("stationuuid") == current_uuid,
            })
        return {
            "tracks": tracks,
            "current_index": current_index,
            "total": len(self._favourites),
        }

    async def handle_status(self) -> dict:
        return {
            "source": self.id,
            "state": self._playing_state,
            "station": self._current_station.get("name") if self._current_station else None,
            "favourites": len(self._favourites),
        }

    def _find_in_favourites(self) -> int | None:
        """Return index of _current_station in _favourites, or None."""
        if not self._current_station:
            return None
        uuid = self._current_station.get("stationuuid", "")
        for i, s in enumerate(self._favourites):
            if s.get("stationuuid") == uuid:
                return i
        return None

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

    async def _find_station_by_name(self, name: str) -> dict | None:
        """Find a station by name — checks local caches first, then Radio Browser API.

        Match priority: favourite short_name (exact, case-insensitive) →
        favourite/browse/stations name (exact) → name substring → curated
        → Radio Browser API. The short_name pass exists so users can map
        short BeoRemote menu labels (e.g. "SR P3") to longer favourite
        names ("Sveriges Radio - P3"). Empty short_names are skipped."""
        name_lower = name.lower()

        # Favourite short_name alias — explicit override beats everything else.
        for s in self._favourites:
            short = s.get("short_name", "")
            if short and short.lower() == name_lower:
                return s

        # Check all local sources first
        for pool in (self._favourites, self._browse_stations, self._stations):
            # Exact match
            for s in pool:
                if s.get("name", "").lower() == name_lower:
                    return s
            # Substring match (e.g. "SR P1" matches "Sveriges Radio P1")
            for s in pool:
                if name_lower in s.get("name", "").lower():
                    return s

        # Fetch curated Swedish stations (likely candidates for SR P1, P3, etc.)
        try:
            curated = await self._fetch_curated("Sweden", CURATED_SVERIGE)
            for s in curated:
                if name_lower in s.get("name", "").lower():
                    return s
        except Exception:
            pass

        # Fall back to API search
        try:
            results = await self._api_get(
                f"/json/stations/byname/{urllib.parse.quote(name)}?limit=5&hidebroken=true",
                ttl=CACHE_TTL_STATIONS,
            )
            if results:
                # Prefer exact match
                for s in results:
                    if s.get("name", "").lower() == name_lower:
                        return s
                return results[0]  # best match from API
        except Exception as e:
            log.warning("Radio API search failed for '%s': %s", name, e)

        return None

    async def _play_station(self, station: dict, action_ts=None):
        url = station.get("url_resolved", station.get("url", ""))
        if not url:
            log.warning("No URL for station %s", station.get("name"))
            return

        # Snapshot action_ts before yielding calls (register, post_media_update)
        # can allow concurrent handlers to overwrite self._action_ts.
        if not action_ts:
            action_ts = self._action_ts

        self._current_station = station
        self._save_last_station()
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
        ok = await self.player_play(
            url=url,
            meta={
                "title": meta["title"],
                "artist": meta["artist"],
                "artwork_url": station.get("favicon", ""),
            },
            radio=True,
            action_ts=action_ts,
        )
        if ok:
            self._playing_state = "playing"
        else:
            # Roll back the pre-broadcast — otherwise GO toggles
            # pause/resume on a stream that never started.
            log.error("Player failed to start station %s", station.get("name"))
            self._playing_state = "stopped"
            await self.register("available")

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

    # ── Station-button bindings ──

    def _load_station_buttons(self):
        """Read radio.station_buttons from config.json and update action_map.

        Color buttons (red/green/yellow/blue) only get added to the action_map
        when explicitly bound. That keeps the global behaviour intact for
        unbound buttons: GREEN/YELLOW stay with the router's balance shortcut
        and BLUE keeps falling through to JOIN/HA.
        """
        raw = cfg("radio", "station_buttons", default={}) or {}
        self._station_buttons = {
            k: v for k, v in raw.items()
            if isinstance(v, str) and v
        }
        for key in self.COLOR_KEYS:
            if key in self._station_buttons:
                self.action_map[key] = "play_button"
            else:
                self.action_map.pop(key, None)

    def _resolve_station_button(self, key: str) -> dict | None:
        """Return the favourite station bound to this button, if any."""
        uuid = self._station_buttons.get(key)
        if not uuid:
            return None
        for s in self._favourites:
            if s.get("stationuuid") == uuid:
                return s
        log.info("Bound station for button %r (uuid=%s) is not in favourites",
                 key, uuid)
        return None

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
        # Backfill missing short_name aliases. Empty string = "user cleared
        # it"; we only suggest when the key is absent entirely so manual
        # clears stick across restarts.
        backfilled = 0
        for s in self._favourites:
            if "short_name" not in s:
                suggested = _suggest_short_name(s.get("name", ""))
                if suggested:
                    s["short_name"] = suggested
                    backfilled += 1
        if backfilled:
            self._save_favourites()
            log.info("Auto-suggested short_name aliases on %d favourites", backfilled)

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

    # ── Last station persistence ──

    def _last_station_path(self) -> str:
        if os.path.exists(os.path.dirname(LAST_STATION_PATH_PROD)):
            return LAST_STATION_PATH_PROD
        return LAST_STATION_PATH_DEV

    def _load_last_station(self):
        path = self._last_station_path()
        try:
            with open(path) as f:
                loaded = json.load(f)
            # Shape-validate: JSON that parses but isn't a station dict
            # (SD corruption, hand edit) would otherwise crash later
            # callers of .get() — on every restart, since the file persists.
            if not isinstance(loaded, dict) or not loaded.get("name"):
                log.warning("Malformed last-station file, ignoring: %.100r", loaded)
                return
            self._current_station = loaded
            log.info("Loaded last station: %s", self._current_station.get("name"))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Failed to load last station: %s", e)

    def _save_last_station(self):
        if not self._current_station:
            return
        path = self._last_station_path()
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._current_station, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            log.warning("Failed to save last station: %s", e)

    def _toggle_favourite(self, station: dict) -> dict:
        uuid = station.get("stationuuid", "")
        existing = [i for i, s in enumerate(self._favourites) if s.get("stationuuid") == uuid]
        if existing:
            for i in reversed(existing):
                self._favourites.pop(i)
            self._save_favourites()
            return {"status": "ok", "favourite": False}
        else:
            entry = {
                "stationuuid": station.get("stationuuid", ""),
                "name": station.get("name", ""),
                "url_resolved": station.get("url_resolved", station.get("url", "")),
                "favicon": station.get("favicon", ""),
                "country": station.get("country", ""),
                "tags": station.get("tags", ""),
                "codec": station.get("codec", ""),
                "bitrate": station.get("bitrate", 0),
                "votes": station.get("votes", 0),
            }
            suggested = _suggest_short_name(entry["name"])
            if suggested:
                entry["short_name"] = suggested
            self._favourites.append(entry)
            self._save_favourites()
            return {"status": "ok", "favourite": True}


if __name__ == "__main__":
    service = RadioService()
    asyncio.run(service.run())
