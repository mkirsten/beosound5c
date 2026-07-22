"""Tests for lib/volume_adapters/heos.py (raw HEOS CLI client).

Pins pid resolution from the mesh roster, the set_volume command string,
get_volume parsing of the level from heos.message, skipping of interim
"command under process" payloads, and stream reset after an error.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

from lib.volume_adapters.heos import HeosVolume  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeReader:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeWriter:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data.decode())

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def _resp(command, message="", payload=None, result="success"):
    obj = {"heos": {"command": command, "result": result, "message": message}}
    if payload is not None:
        obj["payload"] = payload
    return (json.dumps(obj) + "\r\n").encode()


PLAYERS_PAYLOAD = [
    {"name": "Kitchen", "ip": "192.168.1.61", "pid": 111},
    {"name": "Office", "ip": "192.168.1.60", "pid": -222},
]


def _adapter(lines):
    vol = HeosVolume("192.168.1.60", 70)
    vol._reader = _FakeReader(lines)
    vol._writer = _FakeWriter()
    return vol


class TestPidResolution:
    def test_resolves_pid_by_ip(self):
        vol = _adapter([_resp("player/get_players", payload=PLAYERS_PAYLOAD)])
        assert _run(vol._get_pid()) == -222

    def test_single_player_fallback(self):
        vol = _adapter([_resp("player/get_players",
                              payload=[{"name": "X", "ip": "10.0.0.5", "pid": 42}])])
        assert _run(vol._get_pid()) == 42

    def test_ambiguous_roster_returns_none(self):
        payload = [
            {"name": "A", "ip": "10.0.0.5", "pid": 1},
            {"name": "B", "ip": "10.0.0.6", "pid": 2},
        ]
        vol = _adapter([_resp("player/get_players", payload=payload)])
        assert _run(vol._get_pid()) is None


class TestVolumeCommands:
    def test_apply_volume_sends_set_volume(self):
        vol = _adapter([
            _resp("player/get_players", payload=PLAYERS_PAYLOAD),
            _resp("player/set_volume", message="pid=-222&level=45"),
        ])
        _run(vol._apply_volume(45.0))
        assert any("heos://player/set_volume?pid=-222&level=45" in w
                   for w in vol._writer.written)

    def test_get_volume_parses_level(self):
        vol = _adapter([
            _resp("player/get_players", payload=PLAYERS_PAYLOAD),
            _resp("player/get_volume", message="pid=-222&level=37"),
        ])
        assert _run(vol.get_volume()) == 37.0

    def test_interim_payload_skipped(self):
        vol = _adapter([
            _resp("player/get_players", payload=PLAYERS_PAYLOAD),
            _resp("player/get_volume", message="command under process"),
            _resp("player/get_volume", message="pid=-222&level=12"),
        ])
        assert _run(vol.get_volume()) == 12.0


class TestErrorHandling:
    def test_error_resets_streams_and_pid(self):
        vol = _adapter([
            _resp("player/get_players", payload=PLAYERS_PAYLOAD),
            _resp("player/get_volume", message="bad", result="fail"),
        ])
        writer = vol._writer
        assert _run(vol.get_volume()) is None
        # Streams reset so the next command reconnects; pid cache cleared
        # (pids can change across device power-cycles).
        assert writer.closed
        assert vol._writer is None
        assert vol._reader is None
        assert vol._pid is None

    def test_closed_connection_returns_none(self):
        vol = _adapter([])  # readline -> b"" (connection closed)
        assert _run(vol.get_volume()) is None

    def test_is_on_always_true(self):
        vol = HeosVolume("192.168.1.60", 70)
        assert _run(vol.is_on()) is True
