"""Regression tests for the JOIN discovery loop.

Discovery used to run exactly once at startup. When the boot-time scan
raced WiFi bring-up and came back empty, ``_sonos_devices`` stayed empty
forever: JOIN never appeared in the menu and ``/player/resync`` refused
to re-register it. Kitchen ran two months in that state (May–Jul 2026)
until a manual service restart.

The loop in ``_startup_and_monitor`` must:

  1. Retry discovery (at DISCOVERY_RETRY_INTERVAL) while no speakers
     have ever been found, and register JOIN as soon as a scan succeeds.
  2. Keep refreshing (at DISCOVERY_REFRESH_INTERVAL) after success, so
     speakers added later show up without a restart.
  3. Never let an empty rescan clear a non-empty device map — speakers
     may be transiently unreachable.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from test_sonos_external_start import _install_fake_soco


@pytest.fixture
def sonos_player():
    _install_fake_soco()
    from players.sonos import MediaServer
    p = MediaServer()
    p.running = True
    p._register_join_source = AsyncMock(return_value=True)
    return p


def _run_loop(p, scans):
    """Drive _startup_and_monitor through one scan per entry in ``scans``,
    recording the sleep delay chosen after each scan."""
    scan_iter = iter(scans)
    delays = []

    def _next_scan():
        try:
            return next(scan_iter)
        except StopIteration:
            p.running = False
            return {}

    p._discover_all_sync = _next_scan

    async def _fake_sleep(delay):
        delays.append(delay)
        if len(delays) >= len(scans):
            p.running = False

    with patch("players.sonos.asyncio.sleep", _fake_sleep):
        asyncio.new_event_loop().run_until_complete(p._startup_and_monitor())
    return delays


class TestJoinDiscoveryLoop:
    def test_empty_first_scan_retries_and_registers(self, sonos_player):
        """The original bug: an empty boot-time scan must not be final."""
        p = sonos_player
        delays = _run_loop(p, [{}, {"Loft bedroom": "192.168.1.50"}])

        assert p._sonos_devices == {"Loft bedroom": "192.168.1.50"}
        assert p._register_join_source.await_count == 1
        p._register_join_source.assert_awaited_with("available")
        # First wait uses the fast retry interval (nothing found yet),
        # the wait after success uses the slower refresh interval.
        assert delays == [p.DISCOVERY_RETRY_INTERVAL,
                          p.DISCOVERY_REFRESH_INTERVAL]

    def test_empty_rescan_keeps_known_devices(self, sonos_player):
        """A transient empty rescan must not wipe the map or hide JOIN."""
        p = sonos_player
        _run_loop(p, [{"Loft bedroom": "192.168.1.50"}, {}])

        assert p._sonos_devices == {"Loft bedroom": "192.168.1.50"}
        # JOIN registered once — the empty rescan doesn't re-register
        # (or unregister) anything.
        assert p._register_join_source.await_count == 1

    def test_join_registered_only_once_across_refreshes(self, sonos_player):
        p = sonos_player
        scan = {"Loft bedroom": "192.168.1.50"}
        _run_loop(p, [scan, scan, scan])

        assert p._register_join_source.await_count == 1

    def test_refresh_picks_up_new_speaker(self, sonos_player):
        """User adds a speaker after boot — it must appear on refresh."""
        p = sonos_player
        _run_loop(p, [
            {"Loft bedroom": "192.168.1.50"},
            {"Loft bedroom": "192.168.1.50", "Patio": "192.168.1.51"},
        ])

        assert "Patio" in p._sonos_devices
