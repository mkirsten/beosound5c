"""Tests for show_camera parameter handling in beo-input.

The camera overlay has two feed slots. Home Assistant may name the cameras
in the show_camera command, or send no parameters at all and let the
device use the "cameras" array from config.json.

Historically the handler required a single ``camera_entity`` and forwarded
it as ``camera_entity``/``title``, but the overlay only ever read a
``cameras`` array — so the entity HA sent was silently dropped and config
was always used. These tests pin the normalizer that fixed that.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "services"))

# `hid` is a native USB-HID binding that only exists on the device; stub it so
# input.py imports on a dev machine and in CI.
sys.modules.setdefault("hid", type(sys)("hid"))

import input as beo_input  # noqa: E402  (services/input.py)

normalize = beo_input.normalize_show_camera_cameras


# ── Empty result means "fall back to config.json" ────────────────────────────

def test_no_params_falls_back_to_config():
    assert normalize({}) == []


def test_empty_camera_array_falls_back_to_config():
    assert normalize({"cameras": []}) == []


def test_actions_only_falls_back_to_config():
    """A doorbell automation may only relabel the buttons."""
    assert normalize({"actions": {"left": "Open door"}}) == []


def test_entry_without_entity_is_dropped():
    assert normalize({"cameras": [{"title": "Front door"}]}) == []


# ── The cameras array form ───────────────────────────────────────────────────

def test_camera_array_is_used_in_order():
    assert normalize({"cameras": [
        {"title": "Front door", "entity": "camera.front"},
        {"title": "Gate", "entity": "camera.gate"},
    ]}) == [
        {"entity": "camera.front", "title": "Front door"},
        {"entity": "camera.gate", "title": "Gate"},
    ]


def test_camera_array_capped_at_two():
    result = normalize({"cameras": [
        {"title": "One", "entity": "camera.one"},
        {"title": "Two", "entity": "camera.two"},
        {"title": "Three", "entity": "camera.three"},
    ]})
    assert [c["entity"] for c in result] == ["camera.one", "camera.two"]


def test_missing_title_is_allowed():
    """The overlay falls back to a generic label; the entity is what matters."""
    assert normalize({"cameras": [{"entity": "camera.front"}]}) == [
        {"entity": "camera.front", "title": ""}]


def test_whitespace_is_trimmed():
    assert normalize({"cameras": [
        {"title": " Front door ", "entity": " camera.front "}]}) == [
        {"entity": "camera.front", "title": "Front door"}]


def test_junk_entries_are_skipped_not_fatal():
    assert normalize({"cameras": [
        "not a dict", None, {"entity": "camera.front", "title": "Front"}]}) == [
        {"entity": "camera.front", "title": "Front"}]


# ── The legacy singular form (documented in example-automation.yaml) ─────────

def test_legacy_singular_pair_still_works():
    assert normalize({"title": "Doorbell",
                      "camera_entity": "camera.doorbell"}) == [
        {"entity": "camera.doorbell", "title": "Doorbell"}]


def test_legacy_entity_without_title():
    assert normalize({"camera_entity": "camera.doorbell"}) == [
        {"entity": "camera.doorbell", "title": ""}]


def test_camera_array_wins_over_legacy_fields():
    result = normalize({
        "title": "Doorbell",
        "camera_entity": "camera.doorbell",
        "cameras": [{"title": "Gate", "entity": "camera.gate"}],
    })
    assert result == [{"entity": "camera.gate", "title": "Gate"}]


# ── End-to-end wiring ────────────────────────────────────────────────────────
#
# The original bug was not in any single function: the handler forwarded
# {'camera_entity': ...} while the overlay only read data['cameras'], so the
# entity vanished between the two. These pin the payload contract itself.

def _forwarded(params):
    """Run the show_camera command and return the broadcast payload."""
    forward = AsyncMock()
    with patch.object(beo_input, "_forward_to_router", forward), \
            patch.object(beo_input, "set_backlight"):
        result = asyncio.run(beo_input.process_command(
            {"command": "show_camera", "params": params}))
    forward.assert_awaited_once()
    event_type, data = forward.await_args.args
    assert event_type == "camera_overlay"
    assert data["action"] == "show"
    assert result["status"] == "ok"
    return data


def test_command_forwards_cameras_the_overlay_can_read():
    data = _forwarded({"cameras": [
        {"title": "Front door", "entity": "camera.front"},
        {"title": "Gate", "entity": "camera.gate"},
    ]})
    # The overlay reads data['cameras'] — each entry needs entity + title.
    assert data["cameras"] == [
        {"entity": "camera.front", "title": "Front door"},
        {"entity": "camera.gate", "title": "Gate"},
    ]


def test_legacy_command_forwards_as_a_cameras_list():
    data = _forwarded({"title": "Doorbell", "camera_entity": "camera.doorbell"})
    assert data["cameras"] == [
        {"entity": "camera.doorbell", "title": "Doorbell"}]


def test_bare_command_omits_cameras_so_the_overlay_uses_config():
    data = _forwarded({})
    assert "cameras" not in data


def test_actions_still_ride_along():
    data = _forwarded({"actions": {"left": "Open door"}})
    assert data["actions"] == {"left": "Open door"}
