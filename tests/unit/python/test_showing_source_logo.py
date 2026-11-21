"""SHOWING view payload: app name and own logos for TV sources.

TVs (LG webOS, Samsung, ...) report the active app or HDMI input as the
media_player `source`, with no app_name and at most a small app icon.
A logo dropped in web/assets/sources/<slug>.png should be shown instead.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.modules.setdefault("hid", type(sys)("hid"))

import input as beo_input  # noqa: E402

HA = "http://ha:8123"


def state(**attrs):
    return {"state": "on", "attributes": attrs}


def test_slug():
    assert beo_input.source_logo_slug("KPN TV") == "kpn-tv"
    assert beo_input.source_logo_slug("Pathé Thuis") == "pathe-thuis"
    assert beo_input.source_logo_slug("Spotify: muziek en podcasts") == "spotify-muziek-en-podcasts"
    assert beo_input.source_logo_slug("  ") == ""


def test_apple_tv_payload_unchanged(tmp_path):
    r = beo_input.showing_from_state(
        state(media_title="Show", app_name="Netflix", friendly_name="ATV",
              entity_picture="/api/media_player_proxy/x"), HA, str(tmp_path))
    assert r == {"title": "Show", "app_name": "Netflix", "friendly_name": "ATV",
                 "artwork": "http://ha:8123/api/media_player_proxy/x", "state": "on"}


def test_tv_source_is_used_as_app_name(tmp_path):
    r = beo_input.showing_from_state(state(source="kpn tv"), HA, str(tmp_path))
    assert r["app_name"] == "kpn tv"


def test_no_app_name_no_source_shows_dash(tmp_path):
    assert beo_input.showing_from_state(state(), HA, str(tmp_path))["app_name"] == "—"


def test_own_logo_wins_over_ha_picture(tmp_path):
    (tmp_path / "kpn-tv.png").write_bytes(b"png")
    r = beo_input.showing_from_state(
        state(source="KPN TV", entity_picture="/api/icon"), HA, str(tmp_path))
    assert r["artwork"] == "/assets/sources/kpn-tv.png"


def test_svg_logo(tmp_path):
    (tmp_path / "netflix.svg").write_text("<svg/>")
    r = beo_input.showing_from_state(state(source="Netflix"), HA, str(tmp_path))
    assert r["artwork"] == "/assets/sources/netflix.svg"


def test_no_logo_keeps_ha_picture(tmp_path):
    r = beo_input.showing_from_state(
        state(source="YouTube", entity_picture="/api/icon"), HA, str(tmp_path))
    assert r["artwork"] == "http://ha:8123/api/icon"
