"""An unreachable Sonos must not flood the journal.

The monitor polls get_current_track_info() every 0.5s. It used to log
every failure at ERROR, so Kitchen's 4-minute USB/WiFi outage on Nov 18
2026 produced ~500 identical lines. Failures are now reported as edges:
the first in full, a one-line reminder per minute, and a recovery line.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from test_sonos_external_start import _install_fake_soco


@pytest.fixture
def viewer():
    _install_fake_soco()
    from players.sonos import SonosArtworkViewer
    v = SonosArtworkViewer("192.0.2.1")
    v.sonos = MagicMock()
    v.get_coordinator = lambda: v.sonos
    return v


def _errors(caplog):
    return [r for r in caplog.records
            if r.name == "beo-player-sonos" and r.levelno >= logging.ERROR]


def test_outage_logs_first_failure_then_once_per_minute(viewer, caplog):
    viewer.sonos.get_current_track_info.side_effect = ConnectionError("refused")
    clock = [1000.0]
    with patch("players.sonos.time.monotonic", lambda: clock[0]), \
            caplog.at_level(logging.DEBUG, logger="beo-player-sonos"):
        for _ in range(241):            # 0s..120s inclusive at the 0.5s poll rate
            assert viewer.get_current_track_info() is None
            clock[0] += 0.5

    errors = _errors(caplog)
    assert len(errors) == 3, [r.getMessage() for r in errors]
    assert "Error getting track info: refused" in errors[0].getMessage()
    assert "Still failing" in errors[1].getMessage()
    assert "ConnectionError" in errors[2].getMessage()


def test_recovery_is_logged_and_resets(viewer, caplog):
    info = {"title": "x"}
    viewer.sonos.get_current_track_info.side_effect = [
        ConnectionError("a"), ConnectionError("b"), info, ConnectionError("c")]
    with caplog.at_level(logging.INFO, logger="beo-player-sonos"):
        viewer.get_current_track_info()
        viewer.get_current_track_info()
        assert viewer.get_current_track_info() is info
        viewer.get_current_track_info()

    msgs = [r.getMessage() for r in caplog.records if r.name == "beo-player-sonos"]
    assert any("recovered after 2 errors" in m for m in msgs)
    # A fresh outage after recovery is reported in full again.
    assert msgs[-1] == "Error getting track info: c"


def test_success_without_outage_logs_nothing(viewer, caplog):
    viewer.sonos.get_current_track_info.return_value = {"title": "x"}
    with caplog.at_level(logging.INFO, logger="beo-player-sonos"):
        for _ in range(5):
            viewer.get_current_track_info()
    assert not [r for r in caplog.records if r.name == "beo-player-sonos"]
