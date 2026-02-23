#!/usr/bin/env python3
"""
BeoSound 5c — News source (The Guardian API).

Fetches articles from The Guardian, groups by section, and serves them
to the softarc V2 frontend for browsing.  Articles include thumbnail
images and full body text displayed as page views.

Config (config.json):
    "news": { "guardian_api_key": "YOUR_KEY" }

Port: 8776
"""

import asyncio
import logging
import re
import sys
import time

from aiohttp import web

sys.path.insert(0, "..")
sys.path.insert(0, ".")

from lib.config import cfg
from lib.source_base import SourceBase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NEWS] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

GUARDIAN_API = "https://content.guardianapis.com/search"
REFRESH_INTERVAL = 15 * 60  # 15 minutes


def strip_html(text):
    return re.sub(r"<[^>]+>", "", text) if text else ""


def body_to_html(body_text):
    """Convert plain body text to simple HTML paragraphs."""
    if not body_text:
        return ""
    paragraphs = body_text.strip().split("\n")
    return "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())


class NewsService(SourceBase):
    id = "news"
    name = "News"
    port = 8776
    player = "local"
    action_map = {
        "go": "select",
        "up": "up",
        "down": "down",
        "left": "back",
        "right": "select",
    }

    def __init__(self):
        super().__init__()
        self._articles = []      # flat list from Guardian API
        self._sections = []      # grouped for frontend
        self._last_fetch = 0
        self._fetch_task = None
        self._api_key = ""

    async def on_start(self):
        self._api_key = cfg("news", "guardian_api_key", default="")
        if not self._api_key:
            log.info("No guardian_api_key in config — news source disabled")
            raise SystemExit(0)

        log.info("Guardian API key configured, starting article fetch loop")
        await self.register("available")
        self._fetch_task = asyncio.create_task(self._refresh_loop())

    async def on_stop(self):
        if self._fetch_task:
            self._fetch_task.cancel()
        await self.register("gone")

    async def _refresh_loop(self):
        while True:
            try:
                await self._fetch_articles()
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error("Fetch failed: %s", e)
            await asyncio.sleep(REFRESH_INTERVAL)

    async def _fetch_articles(self):
        log.info("Fetching articles from The Guardian...")
        params = {
            "show-fields": "trailText,bodyText,thumbnail",
            "page-size": 30,
            "api-key": self._api_key,
        }
        async with self._http_session.get(GUARDIAN_API, params=params) as resp:
            if resp.status != 200:
                log.error("Guardian API returned %d", resp.status)
                return
            data = await resp.json()

        results = data.get("response", {}).get("results", [])
        log.info("Got %d articles", len(results))

        self._articles = results
        self._sections = self._group_by_section(results)
        self._last_fetch = time.time()

    def _group_by_section(self, articles):
        """Group articles into sections for the arc browser."""
        section_map = {}
        section_icons = {
            "world": "globe",
            "uk-news": "flag",
            "us-news": "flag",
            "politics": "scales",
            "environment": "leaf",
            "science": "flask",
            "technology": "cpu",
            "business": "chart-line-up",
            "sport": "football",
            "football": "football",
            "culture": "masks-theater",
            "music": "music-notes",
            "film": "film-slate",
            "books": "book-open",
            "tv-and-radio": "television",
            "artanddesign": "paint-brush",
            "stage": "masks-theater",
            "lifeandstyle": "heart",
            "fashion": "t-shirt",
            "food": "fork-knife",
            "travel": "airplane",
            "money": "currency-circle-dollar",
            "opinion": "chat-circle-text",
            "commentisfree": "chat-circle-text",
            "education": "graduation-cap",
            "society": "users-three",
            "media": "newspaper",
            "australia-news": "flag",
            "global-development": "globe-hemisphere-east",
        }
        section_colors = {
            "world": "#3498DB",
            "uk-news": "#E74C3C",
            "us-news": "#2ECC71",
            "politics": "#9B59B6",
            "environment": "#27AE60",
            "science": "#2980B9",
            "technology": "#4ECDC4",
            "business": "#F39C12",
            "sport": "#E67E22",
            "football": "#E67E22",
            "culture": "#8E44AD",
            "opinion": "#95A5A6",
            "commentisfree": "#95A5A6",
        }

        for article in articles:
            section_id = article.get("sectionId", "other")
            section_name = article.get("sectionName", "Other")

            if section_id not in section_map:
                section_map[section_id] = {
                    "id": f"sec-{section_id}",
                    "name": section_name,
                    "icon": section_icons.get(section_id, "newspaper"),
                    "color": section_colors.get(section_id, "#FF6348"),
                    "articles": [],
                }

            fields = article.get("fields", {})
            title = article.get("webTitle", "Untitled")
            trail = strip_html(fields.get("trailText", ""))
            body = fields.get("bodyText", "")
            thumbnail = fields.get("thumbnail", "")

            # Build the article entry for softarc V2
            art = {
                "id": article.get("id", ""),
                "name": title if len(title) <= 40 else title[:37] + "...",
            }

            if thumbnail:
                art["image"] = thumbnail
            else:
                art["icon"] = "article"
                art["color"] = section_colors.get(section_id, "#FF6348")

            # Full article as a page view
            page_body = ""
            if trail:
                page_body += f"<p><em>{trail}</em></p>"
            page_body += body_to_html(body)

            if page_body:
                art["page"] = {
                    "title": title,
                    "body": page_body,
                }

            section_map[section_id]["articles"].append(art)

        return list(section_map.values())

    def add_routes(self, app):
        app.router.add_get("/articles", self._handle_articles)

    async def _handle_articles(self, request):
        return web.json_response(self._sections, headers=self._cors_headers())

    async def handle_status(self):
        return {
            "source": self.id,
            "name": self.name,
            "article_count": len(self._articles),
            "section_count": len(self._sections),
            "last_fetch": self._last_fetch,
            "api_key_set": bool(self._api_key),
        }

    async def handle_resync(self):
        await self.register("available")
        return {"status": "ok", "resynced": True}

    async def handle_command(self, cmd, data):
        if cmd == "refresh":
            await self._fetch_articles()
            return {"refreshed": True, "article_count": len(self._articles)}
        return {}


if __name__ == "__main__":
    service = NewsService()
    asyncio.run(service.run())
