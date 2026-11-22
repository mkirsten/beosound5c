"""STOP twice in a row ejects the disc.

Requested behaviour: STOP while playing stops; STOP again ejects. Deliberately
state-driven rather than a double-press timer, so it behaves the same whether
the two presses are half a second or half a minute apart, and so a single STOP
can never eject.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest


def _load_cd_module():
    """Import ``sources.cd`` with pyudev stubbed, leaving sys.modules as found."""
    added = []
    if "pyudev" not in sys.modules:
        stub = types.ModuleType("pyudev")
        stub.Context = object
        stub.Monitor = object
        stub.MonitorObserver = object
        sys.modules["pyudev"] = stub
        added.append("pyudev")
    before = set(sys.modules)
    try:
        return importlib.import_module("sources.cd")
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"CD service not importable here: {exc}")
    finally:
        for name in set(sys.modules) - before | set(added):
            sys.modules.pop(name, None)


cd = _load_cd_module()


class _FakePlayer:
    def __init__(self, state="playing"):
        self.state = state
        self.total_tracks = 12
        self.stops = 0

    async def stop(self):
        self.stops += 1
        self.state = "stopped"

    async def play(self):
        self.state = "playing"

    async def pause(self):
        self.state = "paused"

    async def toggle_playback(self):
        self.state = "paused" if self.state == "playing" else "playing"

    async def next_track(self):
        pass

    async def prev_track(self):
        pass

    def get_status(self):
        return {"state": self.state, "total_tracks": self.total_tracks}


class _FakeDrive:
    def __init__(self):
        self.disc_inserted = True
        self.ejects = 0

    async def eject(self):
        self.ejects += 1
        self.disc_inserted = False


def _service(state="playing"):
    svc = cd.CDService.__new__(cd.CDService)
    svc.cdplayer = _FakePlayer(state)
    svc.drive = _FakeDrive()
    svc._stop_armed = False
    svc.registered = []

    async def _register(state, **kw):
        svc.registered.append(state)

    async def _broadcast():
        pass

    svc.register = _register
    svc._broadcast_cd_update = _broadcast
    return svc


@pytest.mark.asyncio
async def test_stop_then_stop_ejects():
    svc = _service("playing")

    await svc.handle_command("stop", {})
    assert svc.cdplayer.stops == 1
    assert svc.drive.ejects == 0, "first STOP must never eject"
    assert svc._stop_armed is True

    await svc.handle_command("stop", {})
    assert svc.drive.ejects == 1
    assert svc._stop_armed is False


@pytest.mark.asyncio
async def test_single_stop_does_not_eject_however_long_you_wait():
    """No timer involved, so there is no window in which the pair expires."""
    svc = _service("playing")
    await svc.handle_command("stop", {})
    assert svc.drive.ejects == 0
    # ... arbitrarily later, still armed, still not ejected
    assert svc._stop_armed is True
    assert svc.drive.ejects == 0


@pytest.mark.asyncio
async def test_playing_again_between_stops_disarms():
    svc = _service("playing")
    await svc.handle_command("stop", {})
    svc.cdplayer.state = "playing"
    await svc.handle_command("play", {})          # breaks the pair
    await svc.handle_command("stop", {})
    assert svc.drive.ejects == 0, "play between STOPs must cancel the eject"


@pytest.mark.asyncio
async def test_third_stop_does_not_double_eject():
    svc = _service("playing")
    await svc.handle_command("stop", {})
    await svc.handle_command("stop", {})
    await svc.handle_command("stop", {})
    assert svc.drive.ejects == 1


@pytest.mark.asyncio
async def test_still_playing_means_stop_not_eject():
    """A STOP that arrives while the player is still playing always stops."""
    svc = _service("playing")
    svc._stop_armed = True          # armed, but playback resumed externally
    await svc.handle_command("stop", {})
    assert svc.drive.ejects == 0
    assert svc.cdplayer.stops == 1


@pytest.mark.asyncio
async def test_other_commands_disarm():
    for cmd in ("pause", "next", "prev", "toggle"):
        svc = _service("playing")
        svc._stop_armed = True
        svc.cdplayer.state = "stopped"
        # these commands touch the player; we only care that they disarm
        try:
            await svc.handle_command(cmd, {})
        except Exception:
            pass
        assert svc._stop_armed is False, f"{cmd} should disarm"
