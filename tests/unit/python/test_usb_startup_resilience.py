"""Regression tests for USB startup decisions that must survive boot races.

Two decisions in the USB source used to be made exactly once in
``on_start`` and never revisited, so a transient failure at early boot
silently degraded USB playback until a service restart (same pattern as
the Sonos JOIN discovery bug):

  1. ``_device_stream_ip`` — the local IP baked into stream/artwork URLs.
     If the route lookup failed (interface had no address yet) it fell
     back to ``"localhost"`` forever, handing the remote player URLs it
     can never fetch.
  2. ``_init_playback_mode`` — local mpv vs remote player. If the player
     service's HTTP endpoint wasn't answering at probe time, USB locked
     into local mpv and never streamed to Sonos/BlueSound again.

Both must retry until they get a real answer.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sources.usb.service as usb_service
from sources.usb.service import USBService


@pytest.fixture
def usb():
    return USBService()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _cfg(player_ip):
    def fake_cfg(*keys, default=None):
        if keys[:2] == ("player", "ip"):
            return player_ip
        return default
    return fake_cfg


class TestDeviceStreamIp:
    def test_failure_falls_back_but_is_not_cached(self, usb):
        with patch.object(usb_service, "cfg", _cfg("192.0.2.10")):
            with patch.object(usb_service.socket, "socket",
                              side_effect=OSError("network unreachable")):
                assert usb._device_stream_ip() == "localhost"
            assert usb._device_ip is None, \
                "a failed lookup must not be cached"

            sock = MagicMock()
            sock.getsockname.return_value = ("192.168.1.5", 12345)
            with patch.object(usb_service.socket, "socket", return_value=sock):
                assert usb._device_stream_ip() == "192.168.1.5"
            assert usb._device_ip == "192.168.1.5"

    def test_success_is_cached(self, usb):
        with patch.object(usb_service, "cfg", _cfg("192.0.2.10")):
            sock = MagicMock()
            sock.getsockname.return_value = ("192.168.1.5", 12345)
            with patch.object(usb_service.socket, "socket",
                              return_value=sock) as socket_ctor:
                usb._device_stream_ip()
                usb._device_stream_ip()
                assert socket_ctor.call_count == 1

    def test_no_player_ip_means_localhost(self, usb):
        """Local playback (no player.ip configured) — localhost is a
        decision, not a failure, and may be cached."""
        with patch.object(usb_service, "cfg", _cfg("")):
            assert usb._device_stream_ip() == "localhost"
            assert usb._device_ip == "localhost"


class TestInitPlaybackMode:
    def test_unreachable_player_retries_then_goes_remote(self, usb):
        """The original bug: an unanswered probe must not be final."""
        usb._player_get = AsyncMock(side_effect=[
            None,  # player service not up yet
            {"capabilities": ["url_stream"]},
        ])
        usb.file_player.get_status = MagicMock(return_value={"state": "stopped"})
        with patch.object(usb_service, "RemotePlayer", MagicMock()), \
             patch.object(usb_service.asyncio, "sleep", AsyncMock()):
            _run(usb._init_playback_mode())

        assert usb._playback_mode == "remote"
        assert usb.remote_player is not None
        assert usb._player_get.await_count == 2

    def test_reachable_player_without_url_stream_stays_local(self, usb):
        usb._player_get = AsyncMock(return_value={"capabilities": []})
        spawned = []
        usb._spawn = MagicMock(
            side_effect=lambda coro, name=None: (spawned.append(name), coro.close()))
        _run(usb._init_playback_mode())

        assert usb._playback_mode == "local"
        assert usb.remote_player is None
        assert "set_default_airplay" in spawned

    def test_upgrade_deferred_while_local_playback_active(self, usb):
        """If the user started local playback before the player answered,
        flipping _player mid-track would strand it — wait until idle."""
        usb._player_get = AsyncMock(return_value={"capabilities": ["url_stream"]})
        usb.file_player.get_status = MagicMock(side_effect=[
            {"state": "playing"},
            {"state": "stopped"},
        ])
        with patch.object(usb_service, "RemotePlayer", MagicMock()), \
             patch.object(usb_service.asyncio, "sleep", AsyncMock()):
            _run(usb._init_playback_mode())

        assert usb._playback_mode == "remote"
        assert usb.file_player.get_status.call_count == 2
