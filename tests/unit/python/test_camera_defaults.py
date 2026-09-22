"""Camera overlay defaults: demo-only, never invented on a real device.

Two things went wrong here before. The shipped defaults were the author's own
UniFi entity ids, so the public repo carried them and every other install got
tiles wired to cameras it doesn't have. Replacing them with generic-looking
placeholders (camera.front_door) was no better: a device with no cameras
configured would still show an overlay pointing at an entity Home Assistant
has never heard of.

So: nothing by default, cameras come from config.json, and the demo content
lives only in the demo config.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB = REPO_ROOT / "web"
DEMO_CONFIG = WEB / "json" / "demo" / "config.json"

# Files that ship to every install. A camera entity id here is either someone's
# personal setup or a guess; both are wrong.
SHIPPED = [
    WEB / "js" / "config.js",
    WEB / "js" / "camera-overlay.js",
    REPO_ROOT / "services" / "input.py",
]


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_no_camera_entity_ids_in_shipped_code(path):
    hits = [f"line {i}: {line.strip()[:80]}"
            for i, line in enumerate(path.read_text().splitlines(), 1)
            if re.search(r"['\"]camera\.[a-z0-9_]+['\"]", line)]
    assert not hits, f"{path.name} hardcodes a camera entity:\n" + "\n".join(hits)


def test_no_cameras_configured_by_default():
    src = (WEB / "js" / "config.js").read_text()
    assert re.search(r"cameras:\s*\[\s*\]", src), "config.js should ship an empty camera list"


def test_overlay_skips_itself_when_nothing_is_configured():
    """An empty overlay, or one wired to guessed entities, is worse than none."""
    src = (WEB / "js" / "camera-overlay.js").read_text()
    assert "window.AppConfig?.cameras || []" in src
    assert "No cameras configured" in src, "show() must bail out with a log line"


def test_config_json_can_supply_cameras():
    """The key was previously ignored by applyConfig, which is why the
    hardcoded defaults were load-bearing."""
    src = (WEB / "js" / "config.js").read_text()
    assert "config.cameras" in src and "AppConfig.cameras = config.cameras" in src


def test_camera_proxy_endpoints_require_an_entity():
    src = (REPO_ROOT / "services" / "input.py").read_text()
    assert src.count('missing "entity" parameter') == 2, "stream and snapshot both need the guard"


def test_show_camera_params_are_optional():
    """The command itself is the trigger — every parameter is optional.

    show_camera used to reject a message without camera_entity while the
    overlay ignored the entity it *did* send (it only ever read a "cameras"
    array), so HA could not actually pick the cameras. Now: no params means
    fall back to config.json, params override it.
    """
    src = (REPO_ROOT / "services" / "input.py").read_text()
    assert "show_camera without camera_entity" not in src, (
        "show_camera must not require camera_entity")
    assert "def normalize_show_camera_cameras" in src


def test_demo_config_has_cameras_so_the_emulator_still_shows_them():
    cameras = json.loads(DEMO_CONFIG.read_text()).get("cameras")
    assert cameras, "the browser emulator demonstrates the overlay — it needs demo cameras"
    titles = {c["title"] for c in cameras}
    # Titles are the lookup key for the bundled demo photos.
    mock = (WEB / "js" / "emulator-mock-data.js").read_text()
    for title in titles:
        assert f"'{title}'" in mock, f"no demo image mapped for {title!r}"


def test_demo_camera_images_are_bundled_not_hotlinked():
    """A demo that breaks when someone else's image host changes a URL is not
    much of a demo."""
    mock = (WEB / "js" / "emulator-mock-data.js").read_text()
    camera_block = mock[mock.index("cameras: {"):mock.index("},", mock.index("cameras: {"))]
    assert "http" not in camera_block, "demo camera images must be local files"
