"""Tests for the standalone demo mode (web/js/demo-backend.js + web/json/demo/).

A fresh clone's first impression is `cd web && python3 -m http.server` — which
used to show a four-item arc and empty views, because the UI fetched its config
from absolute paths that 404 under that document root and its content from
services that were not running. These tests pin the pieces that make the
documented command produce a working device.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB = REPO_ROOT / "web"
DEMO_JSON = WEB / "json" / "demo"
BACKEND = WEB / "js" / "demo-backend.js"
CONFIG_JS = WEB / "js" / "config.js"


# ── Config discovery ──────────────────────────────────────────────────────────

def test_config_paths_are_relative():
    """Absolute paths 404 when web/ is the document root — the documented dev
    command — leaving the UI on its built-in defaults."""
    src = CONFIG_JS.read_text()
    paths_block = src[src.index("var paths = ["):src.index("]", src.index("var paths = ["))]
    for quoted in re.findall(r"'([^']+)'", paths_block):
        assert not quoted.startswith("/"), f"{quoted} is absolute"


def test_demo_config_is_reachable_from_web_root():
    """The fallback config must live under web/, since a static server rooted
    there cannot reach config/default.json above it."""
    assert (DEMO_JSON / "config.json").exists()
    src = CONFIG_JS.read_text()
    assert "json/demo/config.json" in src


def test_demo_mode_activates_off_device():
    """Port 80 means beo-http on a device; anything else is a static server."""
    src = CONFIG_JS.read_text()
    assert "demo.enabled = true" in src
    assert "location.port" in src
    assert "urlParams.get('demo') === 'false'" in src, "there must be an escape hatch"


# ── Demo data ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "config.json", "scenes.json", "digit_playlists.json",
    "spotify_playlists.json", "apple_music_playlists.json",
    "tidal_playlists.json", "plex_playlists.json",
    "jellyfin_playlists.json",
    "radio_browse.json", "radio_favourites.json",
    "news_articles.json", "usb_browse.json",
])
def test_demo_file_parses(name):
    data = json.loads((DEMO_JSON / name).read_text())
    assert data or data == [], f"{name} is empty"


@pytest.mark.parametrize("name", [
    "spotify_playlists.json", "apple_music_playlists.json",
    "tidal_playlists.json", "plex_playlists.json",
    "jellyfin_playlists.json",
])
def test_playlist_files_match_the_service_shape(name):
    """Views parse these with the same code they use for the real services:
    a flat array of {id, name, image, tracks[]}."""
    data = json.loads((DEMO_JSON / name).read_text())
    assert isinstance(data, list) and data
    for playlist in data:
        assert {"id", "name", "image", "tracks"} <= playlist.keys()
        assert playlist["tracks"], f"{playlist['name']} has no tracks"
        for track in playlist["tracks"]:
            assert {"name", "artist", "id"} <= track.keys()


def test_browse_files_match_the_service_envelope():
    """radio/usb browse endpoints return {path, parent, name, items}."""
    usb = json.loads((DEMO_JSON / "usb_browse.json").read_text())
    assert {"path", "parent", "name", "items"} <= usb.keys()

    radio = json.loads((DEMO_JSON / "radio_browse.json").read_text())
    assert "" in radio, "radio browse must have a root level"
    for level in radio.values():
        assert {"path", "name", "items"} <= level.keys()


def test_demo_libraries_are_distinct():
    """Each service showing the same playlist names reads as a copy-paste job."""
    names = {}
    for source in ("spotify", "apple_music", "tidal", "plex", "jellyfin"):
        data = json.loads((DEMO_JSON / f"{source}_playlists.json").read_text())
        names[source] = {p["name"] for p in data}
    assert not (names["spotify"] & names["tidal"])
    assert not (names["spotify"] & names["apple_music"])
    assert not (names["apple_music"] & names["plex"])
    assert not (names["plex"] & names["jellyfin"])


def test_artwork_is_self_contained_and_scalable():
    """No third-party image hosts (they rot, and they leak a page view), and
    every generated SVG needs a viewBox or it will not scale in the arc."""
    # config.json legitimately carries example service URLs; the content
    # files must not reach off-box for images.
    for path in DEMO_JSON.glob("*.json"):
        if path.name == "config.json":
            continue
        text = path.read_text()
        assert "http://" not in text.replace("http://www.w3.org", ""), f"{path.name} has an http URL"
        assert "https://" not in text, f"{path.name} loads a remote image"

    import base64
    data = json.loads((DEMO_JSON / "spotify_playlists.json").read_text())
    for playlist in data:
        svg = base64.b64decode(playlist["image"].split(",", 1)[1]).decode()
        assert "viewBox" in svg


# ── Wiring ────────────────────────────────────────────────────────────────────

def test_backend_is_inert_without_demo_mode():
    src = BACKEND.read_text()
    assert "if (!demoModeActive()) return;" in src


def test_demo_detection_requires_plain_http():
    """https reports an empty port just like a device on port 80. Keying on the
    port alone made the public emulator think it was talking to a device, and
    every source view failed to load."""
    for path in (CONFIG_JS, BACKEND):
        src = path.read_text()
        assert "location.protocol === 'http:'" in src, f"{path.name} ignores the scheme"


def test_pages_that_skip_config_js_still_get_the_backend():
    """config.html and system.html talk to beo-input but never load config.js,
    so the backend has to work out demo mode by itself."""
    for page in ("config", "system"):
        html = (WEB / "softarc" / f"{page}.html").read_text()
        assert "demo-backend.js" in html, f"{page}.html does not load the demo backend"
    assert "function demoModeActive()" in BACKEND.read_text()


def test_every_menu_entry_has_data_or_is_static():
    """A menu item whose source has no demo data would open an empty view."""
    src = BACKEND.read_text()
    menu_ids = set(re.findall(r"\{ id: '([a-z_]+)'", src))
    data_sources = set(re.findall(r"^\s{8}([a-z_]+):\s*\{ '/", src, re.MULTILINE))
    static = {"playing", "scenes", "system", "showing"}
    # Webpage entries embed a page instead of browsing a source, so the
    # per-source data map never applies to them. Derived rather than named,
    # so adding another one does not fail this test for the wrong reason.
    # [^{}]* keeps the match inside one entry and still spans a wrapped line.
    webpage = set(re.findall(r"\{ id: '([a-z_]+)'[^{}]*type: 'webpage'", src))
    assert webpage, "no webpage entries found — has the menu shape changed?"
    for item in menu_ids - static - webpage:
        assert item in data_sources, f"menu item {item} has no demo data"


def test_pages_load_the_backend():
    """Each frame has its own fetch, so each page needs its own interception."""
    assert "demo-backend.js" in (WEB / "index.html").read_text()
    for page in ("spotify", "tidal", "apple_music", "plex", "jellyfin", "radio",
                 "scenes", "news", "usb"):
        html = (WEB / "softarc" / f"{page}.html").read_text()
        assert "demo-backend.js" in html, f"{page}.html does not load the demo backend"


def test_demo_data_matches_its_generator():
    """web/json/demo/ is generated by tools/gen-demo-data.py — regenerate and
    commit rather than hand-editing, or the next run silently reverts edits."""
    import subprocess
    result = subprocess.run(
        ["python3", str(REPO_ROOT / "tools" / "gen-demo-data.py"), "--check"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_artwork_is_valid_xml():
    """An unescaped '&' in an artist name produces an SVG the browser refuses,
    and the arc silently falls back to a two-letter placeholder."""
    import base64
    import xml.dom.minidom

    checked = 0
    for path in DEMO_JSON.glob("*.json"):
        stack = [json.loads(path.read_text())]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, str) and node.startswith("data:image/svg+xml;base64,"):
                svg = base64.b64decode(node.split(",", 1)[1]).decode()
                xml.dom.minidom.parseString(svg)   # raises if malformed
                checked += 1
    assert checked > 50, f"expected the demo covers to be checked, saw {checked}"
