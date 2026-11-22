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
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── Screen state is restored when the overlay closes ─────────────────────────
#
# A doorbell at night turns the screen on for the camera. Closing the
# overlay — from HA (dismiss_camera) or on the device (GO / timeout, which
# the UI reports as camera_closed) — should put it back to sleep, but must
# leave a screen that was already on alone.

def _run(command, backlight_on, params=None):
    """Run one command with the backlight in a given state; return set_backlight calls."""
    setter = MagicMock()
    with patch.object(beo_input, "_forward_to_router", AsyncMock()), \
            patch.object(beo_input, "set_backlight", setter), \
            patch.object(beo_input, "is_backlight_on", return_value=backlight_on):
        asyncio.run(beo_input.process_command(
            {"command": command, "params": params or {}}))
    return [c.args[0] for c in setter.call_args_list]


def setup_function(_):
    beo_input._camera_woke_screen = False


def _closed():
    """camera_closed as the UI sends it for the latest show_camera overlay."""
    return {"overlay_id": beo_input._camera_overlay_id}


def test_dismiss_turns_screen_off_again_if_camera_woke_it():
    assert _run("show_camera", backlight_on=False) == [True]
    assert _run("dismiss_camera", backlight_on=True) == [False]


def test_device_close_turns_screen_off_again_if_camera_woke_it():
    _run("show_camera", backlight_on=False)
    assert _run("camera_closed", backlight_on=True, params=_closed()) == [False]


def test_screen_that_was_on_stays_on():
    _run("show_camera", backlight_on=True)
    assert _run("dismiss_camera", backlight_on=True) == []
    assert _run("camera_closed", backlight_on=True, params=_closed()) == []


def test_restore_happens_once():
    _run("show_camera", backlight_on=False)
    assert _run("camera_closed", backlight_on=True, params=_closed()) == [False]
    # HA dismissing an overlay the device already closed is a no-op.
    assert _run("dismiss_camera", backlight_on=False) == []


def test_show_camera_tags_its_overlay():
    first = _forwarded({})["overlay_id"]
    assert _forwarded({})["overlay_id"] == first + 1


def test_close_of_an_untagged_or_older_overlay_leaves_screen_alone():
    # The Security view opens its own overlay (no id); an older id is stale.
    _run("show_camera", backlight_on=False)
    assert _run("camera_closed", backlight_on=True) == []
    assert _run("camera_closed", backlight_on=True,
                params={"overlay_id": beo_input._camera_overlay_id - 1}) == []
    assert _run("camera_closed", backlight_on=True, params=_closed()) == [False]


# ── Any other screen change hands the screen back to the user ────────────────
#
# These use the real set_backlight (only the hardware writes are stubbed), so
# the screen state actually changes between commands.

def _screen(on):
    beo_input.state_byte1 = (beo_input.state_byte1 | 0x40) if on \
        else (beo_input.state_byte1 & ~0x40)


def _cmd(command, params=None):
    with patch.object(beo_input, "_forward_to_router", AsyncMock()), \
            patch.object(beo_input, "bs5_send_cmd"), \
            patch.object(beo_input._xrandr_pool, "submit"):
        asyncio.run(beo_input.process_command(
            {"command": command, "params": params or {}}))


def test_manual_off_then_on_means_close_leaves_screen_on():
    # Doorbell at night wakes the screen; the user turns it off and back on
    # by hand, then the overlay times out while they are using it.
    _screen(False)
    _cmd("show_camera")
    assert beo_input._camera_woke_screen
    _cmd("screen_off")
    _cmd("screen_on")
    _cmd("camera_closed", _closed())
    assert beo_input.is_backlight_on()


def test_close_still_restores_when_nothing_else_touched_the_screen():
    _screen(False)
    _cmd("show_camera")
    _cmd("camera_closed", _closed())
    assert not beo_input.is_backlight_on()


def test_ha_wake_during_overlay_keeps_screen_on():
    _screen(False)
    _cmd("show_camera")
    _cmd("wake")
    _cmd("camera_closed", _closed())
    assert beo_input.is_backlight_on()
