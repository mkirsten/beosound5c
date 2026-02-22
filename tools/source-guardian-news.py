#!/usr/bin/env python3
"""Fetch the latest Guardian news articles with thumbnail images.

Outputs:
  - guardian_news.md: Markdown file with title, text, and image ID per article
  - images/<id>.jpg: Thumbnail images named by Guardian media asset ID

Usage:
  python3 tools/source-guardian-news.py [--output-dir /path/to/dir] [--count 20]

Requires GUARDIAN_API_KEY env var or --api-key flag.
"""

import argparse
import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

GUARDIAN_API = "https://content.guardianapis.com/search"


async def fetch_articles(session, count, api_key):
    params = {
        "show-fields": "trailText,bodyText,thumbnail",
        "page-size": count,
        "api-key": api_key,
    }
    async with session.get(GUARDIAN_API, params=params) as resp:
        data = await resp.json()
        return data["response"]["results"]


def extract_image_id(thumbnail_url):
    """Extract the Guardian media hash ID from a thumbnail URL."""
    # URL format: https://media.guim.co.uk/<hash>/<crop>/500.jpg
    match = re.match(r"https://media\.guim\.co\.uk/([a-f0-9]+)/", thumbnail_url)
    return match.group(1) if match else None


def strip_html(text):
    """Remove basic HTML tags from trail text."""
    return re.sub(r"<[^>]+>", "", text) if text else ""


async def download_image(session, url, dest):
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                dest.write_bytes(await resp.read())
                return True
    except Exception as e:
        print(f"  Failed to download {url}: {e}")
    return False


async def main():
    parser = argparse.ArgumentParser(description="Fetch Guardian news with images")
    parser.add_argument(
        "--output-dir", "-o", default=".", help="Output directory (default: current)"
    )
    parser.add_argument(
        "--count", "-n", type=int, default=20, help="Number of articles (default: 20)"
    )
    parser.add_argument(
        "--api-key", "-k",
        default=os.environ.get("GUARDIAN_API_KEY", "test"),
        help="Guardian API key (default: $GUARDIAN_API_KEY or 'test')",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    async with aiohttp.ClientSession() as session:
        print(f"Fetching {args.count} articles from The Guardian...")
        articles = await fetch_articles(session, args.count, args.api_key)
        print(f"Got {len(articles)} articles")

        # Build markdown and collect image downloads
        md_lines = ["# Guardian News\n"]
        downloads = []

        for i, article in enumerate(articles, 1):
            title = article.get("webTitle", "Untitled")
            fields = article.get("fields", {})
            body = fields.get("bodyText", "").strip()
            trail = strip_html(fields.get("trailText", ""))
            thumbnail = fields.get("thumbnail", "")
            image_id = extract_image_id(thumbnail) if thumbnail else None

            md_lines.append("---\n")
            md_lines.append(f"## {i}. {title}\n")
            md_lines.append(f"*{trail}*\n")
            if body:
                md_lines.append(f"\n{body}\n")

            if image_id:
                ext = Path(urlparse(thumbnail).path).suffix or ".jpg"
                filename = f"{image_id}{ext}"
                md_lines.append(f"\n**Image:** `{filename}`\n")
                downloads.append((thumbnail, images_dir / filename))
            else:
                md_lines.append("\n**Image:** none\n")

            md_lines.append("")

        # Write markdown
        md_path = output_dir / "guardian_news.md"
        md_path.write_text("\n".join(md_lines))
        print(f"Wrote {md_path}")

        # Download images in parallel
        print(f"Downloading {len(downloads)} images...")
        results = await asyncio.gather(
            *[download_image(session, url, dest) for url, dest in downloads]
        )
        ok = sum(results)
        print(f"Downloaded {ok}/{len(downloads)} images to {images_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
