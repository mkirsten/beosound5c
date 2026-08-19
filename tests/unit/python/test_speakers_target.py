"""Coverage for the SPEAKERS active-target work (2026-08-18 review).

Two regressions this pins:
  * a player restart must honour a saved runtime target, and a config
    `player.ip` change must drop a now-stale override (active_target.py);
  * `/player/network` must keep returning the pinned current-target row on
    the *second* call — PlayerBase now owns the cache write, so the composite
    (current + others) survives instead of being replaced by others-only.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import lib.active_target as at
from lib.player_base import PlayerBase


class _GroupPlayer(PlayerBase):
    id = "fake"
    name = "Fake"
    port = 8766

    async def play(self, uri=None, url=None, track_uri=None, meta=None,
                   radio=False, track_uris=None):
        return True

    async def pause(self):
        return True

    async def stop(self):
        return True

    async def resume(self):
        return True

    def supports_grouping(self):
        return True


class _Req:
    async def json(self):
        return {}


class TestGroupNetworkCache:
    def test_current_row_survives_second_call(self):
        p = _GroupPlayer()
        devices = [
            {"ip": "1.1.1.1", "name": "Here", "current": True},
            {"ip": "2.2.2.2", "name": "There"},
        ]
        p.group_list_devices = AsyncMock(return_value=devices)

        r1 = asyncio.run(p._handle_group_network(_Req()))
        assert json.loads(r1.text) == devices

        # Second call serves the cache — the pinned current row must still
        # be there (the pre-fix bug returned others-only from poll 2 on).
        r2 = asyncio.run(p._handle_group_network(_Req()))
        got = json.loads(r2.text)
        assert any(d.get("current") for d in got), "current row must survive"
        assert got == devices


class TestGroupNetworkAntiFlap:
    def test_empty_result_does_not_poison_cache(self):
        # §2: a transient [] must not replace a good cache — and must not seed
        # one on the cold path (an empty cache also breaks retarget validation).
        p = _GroupPlayer()
        p.group_list_devices = AsyncMock(return_value=[])
        r = asyncio.run(p._handle_group_network(_Req()))
        assert json.loads(r.text) == []
        assert getattr(p, "_network_cache", None) is None

        good = [{"ip": "1.1.1.1", "name": "Here", "current": True}]
        p.group_list_devices = AsyncMock(return_value=good)
        asyncio.run(p._handle_group_network(_Req()))
        assert p._network_cache == good


class TestPlayerCapabilities:
    def test_ase_advertises_nothing(self):
        # §1: ASE has no URL-play endpoint, so it must NOT claim url_stream
        # (sources key off it and would send `url` → silent no-audio).
        import sys
        sys.path.insert(0, "services")
        from players.ase import AsePlayer
        caps = asyncio.run(AsePlayer().get_capabilities())
        assert "url_stream" not in caps and caps == []


class TestMozartGrouping:
    def test_is_grouped_true_as_follower(self):
        # §4: a Mozart that has joined someone's experience (follower) must
        # report is_grouped, not just when it leads.
        import sys
        sys.path.insert(0, "services")
        from players.mozart import MozartPlayer
        p = MozartPlayer()
        p._is_follower = True
        status = asyncio.run(p.get_status())
        assert status["is_grouped"] is True


class TestGroupingSingleSourceOfTruth:
    def test_grouping_types_match_supports_grouping(self):
        # §13: GROUPING_PLAYER_TYPES must equal the set of players whose
        # supports_grouping() returns True. Parse the sources rather than
        # importing (soco/pyheos aren't in the dev venv) so drift is still
        # detectable.
        import re
        import sys
        import pathlib
        sys.path.insert(0, "services")
        from router import GROUPING_PLAYER_TYPES

        grouping = set()
        for f in pathlib.Path("services/players").glob("*.py"):
            src = f.read_text()
            m_id = re.search(r'^\s+id = "([a-z_]+)"', src, re.M)
            m_grp = re.search(
                r'def supports_grouping\(self\)[^\n]*:\s*\n'
                r'(?:\s*#[^\n]*\n|\s*""".*?"""\n)*\s*return\s+(True|False)',
                src, re.S)
            if m_id and m_grp and m_grp.group(1) == "True":
                grouping.add(m_id.group(1))
        assert grouping == GROUPING_PLAYER_TYPES, (
            f"drift: source says {grouping}, router says {GROUPING_PLAYER_TYPES}")


class TestActiveTargetStickiness:
    def test_sticky_then_dropped_on_config_change(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(at, "_config_ip", lambda: "10.0.0.10")
        at.clear_active_target()

        # No override → configured room.
        assert at.effective_player_ip() == "10.0.0.10"

        # Retarget is sticky (survives "restart" = a fresh read).
        at.save_active_target("10.0.0.20", "Kitchen")
        assert at.load_active_target() == {"ip": "10.0.0.20", "name": "Kitchen"}
        assert at.effective_player_ip() == "10.0.0.20"

        # Config player.ip changes under it → stale override dropped.
        monkeypatch.setattr(at, "_config_ip", lambda: "10.0.0.99")
        assert at.load_active_target() is None
        assert at.effective_player_ip() == "10.0.0.99"
