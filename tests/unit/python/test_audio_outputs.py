"""Tests for lib.audio_outputs — pactl output parsing + classification.

This module was the subject of commit 27cb774 (async conversion from
``subprocess.run``) and 5e79b24 (USB DAC S/PDIF profile support).
It had zero direct tests before this file, so the behaviour lived
entirely in code.

Strategy:
  * Classification helpers (``_classify_sink``, ``_classify_airplay``)
    are pure — exhaustively cover every rule.
  * ``AudioOutputs`` shells out to ``pactl`` — mock ``_run`` with fake
    text captured from a real device and verify the parser.
  * ``check_pipewire_health`` must catch the broken-handshake sentinel
    value ``4294967295``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from lib.audio_outputs import (
    AudioOutputs,
    _classify_airplay,
    _classify_sink,
)


# ── Pure classification (no mocking) ─────────────────────────────────


class TestClassifySink:
    @pytest.mark.parametrize("name,desc,expected", [
        ("bluez_output.XX_XX_XX_XX_XX_XX.a2dp-sink", "Bose QC", "bluetooth"),
        ("alsa_output.platform-bcm2835_audio.analog-stereo", "Built-in", "analog"),
        ("alsa_output.usb-Topping_MX3-00.analog-stereo", "Topping", "usb"),
        ("alsa_output.platform-fef05700.hdmi.hdmi-stereo", "HDMI 1", "hdmi"),
        ("alsa_output.platform-fe00b840.spdif", "HiFiBerry Digi", "optical"),
        ("alsa_output.hdmi-stereo-extra1", "Some HDMI", "hdmi"),
        ("something_completely_unknown", "foo", "other"),
    ])
    def test_classifies_common_sink_types(self, name, desc, expected):
        assert _classify_sink(name, desc) == expected

    def test_airplay_sonos_is_subclassified(self):
        # raop_sink.<hostname>.<ip>.<port>
        sink = "raop_sink.Sonos-XXXXXXXXXXXX.local.192.168.1.100.7000"
        assert _classify_sink(sink, "Sonos Living Room") == "sonos"

    def test_airplay_homepod(self):
        sink = "raop_sink.HomePod-Kitchen.local.192.168.0.42.7000"
        assert _classify_sink(sink, "HomePod") == "homepod"

    def test_airplay_apple_tv(self):
        sink = "raop_sink.Living-Room-AppleTV.local.192.168.0.50.7000"
        assert _classify_sink(sink, "Apple TV") == "appletv"

    def test_airplay_mac_variants(self):
        for host in (
            "raop_sink.macbook-pro.local.192.168.0.2.7000",
            "raop_sink.Mac-Studio.local.192.168.0.3.7000",
            "raop_sink.macmini.local.192.168.0.4.7000",
        ):
            assert _classify_sink(host, "Mac") == "mac"

    def test_airplay_generic_fallback(self):
        sink = "raop_sink.NoBrand.local.192.168.0.99.7000"
        assert _classify_sink(sink, "Generic AirPlay") == "airplay"

    def test_null_sinks_still_classified(self):
        """(`null_sink` is filtered by get_outputs, but the classifier
        itself should still give a sensible answer.)"""
        assert _classify_sink("null_sink", "Dummy") == "other"


class TestClassifyAirPlayHostnameExtraction:
    """_classify_airplay strips ``raop_sink.`` + trailing IP/port to find the host."""

    def test_extracts_hostname_with_dots(self):
        sink = "raop_sink.Sonos-Living-Room.local.192.168.1.101.7000"
        assert _classify_airplay(sink) == "sonos"

    def test_handles_missing_ip(self):
        """If the sink name has no IP, the hostname is the whole tail —
        classification should still work."""
        assert _classify_airplay("raop_sink.Sonos-NoIp") == "sonos"


# ── AudioOutputs with mocked _run ────────────────────────────────────


def _mock_run_factory(outputs_map: dict[tuple, tuple[str, int]]):
    """Return an async fake for AudioOutputs._run.

    ``outputs_map`` keys are tuples of argv (excluding the instance)
    returning (stdout, returncode).
    """
    async def _fake(self, *args, timeout=3.0, capture=True):
        key = tuple(args)
        if key in outputs_map:
            return outputs_map[key]
        for k, v in outputs_map.items():
            if k and args[:len(k)] == k:
                return v
        return ("", 0)
    return _fake


_PACTL_SHORT = (
    "0\talsa_output.platform-bcm2835_audio.analog-stereo\tPipeWire\ts16le 2ch 44100Hz\tRUNNING\n"
    "1\traop_sink.Sonos-Kitchen.local.192.168.1.101.7000\tPipeWire\ts16le 2ch 44100Hz\tSUSPENDED\n"
    "2\tbluez_output.40_EF_4C_12_34_56.a2dp-sink\tPipeWire\ts16le 2ch 44100Hz\tSUSPENDED\n"
)

_PACTL_FULL = """\
Sink #0
    State: RUNNING
    Name: alsa_output.platform-bcm2835_audio.analog-stereo
    Description: Built-in Audio Analog Stereo
    Driver: PipeWire
Sink #1
    State: SUSPENDED
    Name: raop_sink.Sonos-Kitchen.local.192.168.1.101.7000
    Description: Sonos Kitchen
Sink #2
    State: SUSPENDED
    Name: bluez_output.40_EF_4C_12_34_56.a2dp-sink
    Description: Bose QC35
"""

_PACTL_DEFAULT = "alsa_output.platform-bcm2835_audio.analog-stereo\n"


class TestGetOutputs:
    def test_parses_short_and_full_pactl_output(self):
        mp = {
            ("pactl", "list", "sinks", "short"): (_PACTL_SHORT, 0),
            ("pactl", "list", "sinks"): (_PACTL_FULL, 0),
            ("pactl", "get-default-sink"): (_PACTL_DEFAULT, 0),
        }
        ao = AudioOutputs()
        with patch.object(AudioOutputs, "_run",
                          _mock_run_factory(mp)):
            outputs = asyncio.new_event_loop().run_until_complete(ao.get_outputs())

        by_type = {o["type"]: o for o in outputs}
        assert "analog" in by_type
        assert "sonos" in by_type
        assert "bluetooth" in by_type

        analog = by_type["analog"]
        assert analog["active"] is True           # matches default
        assert analog["label"] == "Built-in Audio Analog Stereo"

        sonos = by_type["sonos"]
        assert sonos["active"] is False
        assert sonos["label"] == "Sonos Kitchen"

    def test_filters_null_sinks(self):
        short = _PACTL_SHORT + "3\tnull_sink\tPipeWire\ts16le\tSUSPENDED\n"
        mp = {
            ("pactl", "list", "sinks", "short"): (short, 0),
            ("pactl", "list", "sinks"): (_PACTL_FULL, 0),
            ("pactl", "get-default-sink"): (_PACTL_DEFAULT, 0),
        }
        ao = AudioOutputs()
        with patch.object(AudioOutputs, "_run",
                          _mock_run_factory(mp)):
            outputs = asyncio.new_event_loop().run_until_complete(ao.get_outputs())
        assert not any("null" in o["name"] for o in outputs)

    def test_returns_empty_on_error(self):
        async def _bad(self, *a, **k):
            raise RuntimeError("pactl died")
        ao = AudioOutputs()
        with patch.object(AudioOutputs, "_run", _bad):
            outputs = asyncio.new_event_loop().run_until_complete(ao.get_outputs())
        assert outputs == []


class TestFindSink:
    def test_filters_by_ip(self):
        mp = {
            ("pactl", "list", "sinks", "short"): (_PACTL_SHORT, 0),
            ("pactl", "list", "sinks"): (_PACTL_FULL, 0),
            ("pactl", "get-default-sink"): (_PACTL_DEFAULT, 0),
        }
        ao = AudioOutputs()
        with patch.object(AudioOutputs, "_run",
                          _mock_run_factory(mp)):
            sink = asyncio.new_event_loop().run_until_complete(
                ao.find_sink(ip="192.168.1.101")
            )
        assert sink is not None
        assert sink["type"] == "sonos"

    def test_filters_by_type(self):
        mp = {
            ("pactl", "list", "sinks", "short"): (_PACTL_SHORT, 0),
            ("pactl", "list", "sinks"): (_PACTL_FULL, 0),
            ("pactl", "get-default-sink"): (_PACTL_DEFAULT, 0),
        }
        ao = AudioOutputs()
        with patch.object(AudioOutputs, "_run",
                          _mock_run_factory(mp)):
            sink = asyncio.new_event_loop().run_until_complete(
                ao.find_sink(type="bluetooth")
            )
        assert sink is not None
        assert "bluez" in sink["name"]

    def test_returns_none_when_no_match(self):
        mp = {
            ("pactl", "list", "sinks", "short"): (_PACTL_SHORT, 0),
            ("pactl", "list", "sinks"): (_PACTL_FULL, 0),
            ("pactl", "get-default-sink"): (_PACTL_DEFAULT, 0),
        }
        ao = AudioOutputs()
        with patch.object(AudioOutputs, "_run",
                          _mock_run_factory(mp)):
            sink = asyncio.new_event_loop().run_until_complete(
                ao.find_sink(ip="10.0.0.1")
            )
        assert sink is None


class TestPipewireHealth:
    _BROKEN = (
        "Server String: /run/user/1000/pulse/native\n"
        "Library Protocol Version: 35\n"
        "Server Protocol Version: 4294967295\n"
        "Is Local: yes\n"
    )
    _HEALTHY = (
        "Server String: /run/user/1000/pulse/native\n"
        "Library Protocol Version: 35\n"
        "Server Protocol Version: 35\n"
        "Is Local: yes\n"
    )

    def test_detects_broken_handshake_sentinel(self):
        mp = {("pactl", "info"): (self._BROKEN, 0)}
        ao = AudioOutputs()
        with patch.object(AudioOutputs, "_run",
                          _mock_run_factory(mp)):
            ok = asyncio.new_event_loop().run_until_complete(
                ao.check_pipewire_health()
            )
        assert ok is False

    def test_healthy_server_returns_true(self):
        mp = {("pactl", "info"): (self._HEALTHY, 0)}
        ao = AudioOutputs()
        with patch.object(AudioOutputs, "_run",
                          _mock_run_factory(mp)):
            ok = asyncio.new_event_loop().run_until_complete(
                ao.check_pipewire_health()
            )
        assert ok is True

    def test_pactl_error_returns_false(self):
        async def _bad(self, *a, **k):
            raise RuntimeError("pactl died")
        ao = AudioOutputs()
        with patch.object(AudioOutputs, "_run", _bad):
            ok = asyncio.new_event_loop().run_until_complete(
                ao.check_pipewire_health()
            )
        assert ok is False
