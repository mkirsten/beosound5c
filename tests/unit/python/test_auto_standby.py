"""Auto-standby fires when the idle timeout expires, not on a 10-min tick.

The loop used to sleep a fixed 600 s between checks, so with the default
30-minute timeout the device could stay on for up to 40 minutes.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))

from test_router import make_router  # noqa: E402


def test_wait_is_time_left_until_timeout():
    router = make_router()
    assert router._standby_wait(1800, 0) == 1800
    assert router._standby_wait(1800, 1500) == 300


def test_wait_never_drops_below_recheck_interval():
    router = make_router()
    assert router._standby_wait(1800, 1790) == router.STANDBY_RECHECK_S
    assert router._standby_wait(1800, 5000) == router.STANDBY_RECHECK_S


def _run_loop(router, *, playing, sleeps_before_stop):
    """Run _auto_standby_loop against a fake clock; return the standby time."""
    import router as router_mod

    clock = [1000.0]
    router._last_activity = clock[0]
    fired = []
    calls = [0]

    async def fake_sleep(seconds):
        calls[0] += 1
        if calls[0] > sleeps_before_stop:
            raise asyncio.CancelledError
        clock[0] += seconds

    def fake_spawn(coro, name=None):
        if name == "auto_standby_screen_off":
            fired.append(clock[0] - 1000.0)
        coro.close()

    router._is_player_active = AsyncMock(side_effect=playing)
    router._spawn = fake_spawn
    router._volume = None

    with patch.object(router_mod, "cfg", side_effect=lambda *k, default=None: 30), \
            patch.object(router_mod.time, "monotonic", side_effect=lambda: clock[0]), \
            patch.object(router_mod.asyncio, "sleep", fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(router._auto_standby_loop())
    return fired


def test_standby_fires_exactly_at_timeout():
    router = make_router()
    fired = _run_loop(router, playing=[False], sleeps_before_stop=1)
    assert fired == [1800]


def test_standby_follows_within_a_minute_after_playback_stops():
    router = make_router()
    # Still playing at the 30-minute mark, stopped one check later.
    fired = _run_loop(router, playing=[True, False], sleeps_before_stop=2)
    assert fired == [1800 + router.STANDBY_RECHECK_S]
