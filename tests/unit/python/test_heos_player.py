"""Tests for players/heos.py.

Pins the missing-config exit convention (same as test_bluesound_player),
player selection from the HEOS mesh roster, the PlayState -> UI state
mapping, now-playing media mapping (station fallback, ms->time, track-key
dedup), and the play() url/uri/resume paths.

pyheos is faked when not installed so these tests run in CI without the
dependency (mirrors the OPTIONAL_MODULES convention in
test_cross_source_import).
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

SERVICES_DIR = Path(__file__).resolve().parents[3] / "services"
sys.path.insert(0, str(SERVICES_DIR))


def _install_fake_pyheos():
    """Insert a minimal fake pyheos into sys.modules (if not installed)."""
    if "pyheos" in sys.modules:
        return
    try:
        import pyheos  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    fake = types.ModuleType("pyheos")

    class PlayState:
        PLAY = "play"
        PAUSE = "pause"
        STOP = "stop"

    class ConnectionState:
        CONNECTED = "connected"
        DISCONNECTED = "disconnected"

    class HeosError(Exception):
        pass

    class HeosOptions:
        def __init__(self, host, **kwargs):
            self.host = host
            self.kwargs = kwargs

    class Heos:
        def __init__(self, options):
            self.options = options
            self.connection_state = ConnectionState.DISCONNECTED

        def add_on_connected(self, cb):
            return lambda: None

        def add_on_disconnected(self, cb):
            return lambda: None

        async def connect(self):
            self.connection_state = ConnectionState.CONNECTED

        async def disconnect(self):
            self.connection_state = ConnectionState.DISCONNECTED

        async def get_players(self, *, refresh=False):
            return {}

    const = types.SimpleNamespace(
        EVENT_PLAYER_STATE_CHANGED="event/player_state_changed",
        EVENT_PLAYER_NOW_PLAYING_CHANGED="event/player_now_playing_changed",
        EVENT_PLAYER_NOW_PLAYING_PROGRESS="event/player_now_playing_progress",
        EVENT_PLAYER_VOLUME_CHANGED="event/player_volume_changed",
    )

    fake.PlayState = PlayState
    fake.ConnectionState = ConnectionState
    fake.HeosError = HeosError
    fake.HeosOptions = HeosOptions
    fake.Heos = Heos
    fake.const = const
    sys.modules["pyheos"] = fake


_install_fake_pyheos()

import players.heos as heos_module  # noqa: E402
from players.heos import HeosPlayerService  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeMedia:
    def __init__(self, song="", station="", artist="", album="",
                 image_url="", media_id="", source_id=None,
                 current_position=0, duration=0):
        self.song = song
        self.station = station
        self.artist = artist
        self.album = album
        self.image_url = image_url
        self.media_id = media_id
        self.source_id = source_id
        self.current_position = current_position
        self.duration = duration


class _FakePlayer:
    def __init__(self, name="Living Room", player_id=1, ip_address="192.168.1.60",
                 state=None, volume=25, media=None):
        self.name = name
        self.player_id = player_id
        self.ip_address = ip_address
        self.state = state
        self.volume = volume
        self.repeat = "off"
        self.now_playing_media = media or _FakeMedia()
        self.calls = []

    def add_on_player_event(self, cb):
        self.calls.append(("subscribe",))
        return lambda: None

    async def play_url(self, url):
        self.calls.append(("play_url", url))

    async def play(self):
        self.calls.append(("play",))

    async def pause(self):
        self.calls.append(("pause",))

    async def stop(self):
        self.calls.append(("stop",))

    async def play_next(self):
        self.calls.append(("play_next",))

    async def play_previous(self):
        self.calls.append(("play_previous",))

    async def set_play_mode(self, repeat, shuffle):
        self.calls.append(("set_play_mode", repeat, shuffle))


class _FakeHeos:
    def __init__(self, players):
        self._players = players

    async def get_players(self, *, refresh=False):
        return self._players


class TestMissingIpGuard:
    def test_on_start_without_ip_exits_zero(self, monkeypatch):
        import lib.watchdog as watchdog_module

        notifications = []
        monkeypatch.setattr(watchdog_module, "sd_notify", notifications.append)
        monkeypatch.setattr(heos_module, "HEOS_IP", "")
        player = HeosPlayerService()
        with pytest.raises(SystemExit) as excinfo:
            _run(player.on_start())
        # Exit 0 so Restart=on-failure does NOT crash-loop the service.
        assert excinfo.value.code == 0
        # READY=1 must be notified before exiting (Type=notify unit).
        assert any(n.startswith("READY=1") for n in notifications)
        assert any("STOPPING=1" in n for n in notifications)

    def test_on_start_with_ip_starts_monitor(self, monkeypatch):
        monkeypatch.setattr(heos_module, "HEOS_IP", "192.168.1.60")
        player = HeosPlayerService()
        # running=False makes the connect loop exit immediately.
        player.running = False

        async def _go():
            await player.on_start()
            assert player._monitor_task is not None
            await player._monitor_task

        _run(_go())


class TestPlayerSelection:
    def _service(self, monkeypatch, players):
        monkeypatch.setattr(heos_module, "HEOS_IP", "192.168.1.60")
        svc = HeosPlayerService()
        svc.ip = "192.168.1.60"
        svc._heos = _FakeHeos(players)
        return svc

    def test_selects_by_ip(self, monkeypatch):
        p1 = _FakePlayer(name="Kitchen", player_id=1, ip_address="192.168.1.61")
        p2 = _FakePlayer(name="Office", player_id=2, ip_address="192.168.1.60")
        svc = self._service(monkeypatch, {1: p1, 2: p2})
        _run(svc._attach_player())
        assert svc._player is p2

    def test_single_player_fallback(self, monkeypatch):
        p1 = _FakePlayer(name="Kitchen", player_id=1, ip_address="10.0.0.9")
        svc = self._service(monkeypatch, {1: p1})
        _run(svc._attach_player())
        assert svc._player is p1

    def test_ambiguous_roster_selects_none(self, monkeypatch):
        p1 = _FakePlayer(name="A", player_id=1, ip_address="10.0.0.8")
        p2 = _FakePlayer(name="B", player_id=2, ip_address="10.0.0.9")
        svc = self._service(monkeypatch, {1: p1, 2: p2})
        _run(svc._attach_player())
        assert svc._player is None


class TestStateMapping:
    def _service(self, state):
        svc = HeosPlayerService()
        svc._player = _FakePlayer(state=state)
        return svc

    def test_play_maps_to_playing(self):
        from pyheos import PlayState
        assert self._service(PlayState.PLAY)._map_state() == "playing"

    def test_pause_maps_to_paused(self):
        from pyheos import PlayState
        assert self._service(PlayState.PAUSE)._map_state() == "paused"

    def test_stop_maps_to_stopped(self):
        from pyheos import PlayState
        assert self._service(PlayState.STOP)._map_state() == "stopped"

    def test_no_player_maps_to_stopped(self):
        svc = HeosPlayerService()
        assert svc._map_state() == "stopped"


class TestNowPlayingSync:
    def _service(self, media, state=None, volume=25):
        svc = HeosPlayerService()
        svc.ip = "192.168.1.60"
        svc._player = _FakePlayer(state=state, volume=volume, media=media)
        svc._broadcasts = []

        async def _capture(media_data, reason="update"):
            svc._cached_media_data = media_data
            svc._broadcasts.append((media_data, reason))

        svc.broadcast_media_update = _capture
        # Recent internal command — suppress external-change override path
        svc.seconds_since_command = lambda: 0.0
        return svc

    def test_broadcasts_track_change(self):
        media = _FakeMedia(song="Song", artist="Artist", album="Album",
                           media_id="m1", duration=185000, current_position=5000)
        svc = self._service(media)
        _run(svc._sync_now_playing())
        assert len(svc._broadcasts) == 1
        data, reason = svc._broadcasts[0]
        assert reason == "track_change"
        assert data["title"] == "Song"
        assert data["artist"] == "Artist"
        assert data["duration"] == "3:05"
        assert data["position"] == "0:05"
        assert data["uri"] == "heos:m1"
        assert data["volume"] == 25

    def test_station_fallback_for_streams(self):
        media = _FakeMedia(song="", station="Radio Paradise")
        svc = self._service(media)
        _run(svc._sync_now_playing())
        assert svc._broadcasts[0][0]["title"] == "Radio Paradise"

    def test_same_track_not_rebroadcast(self):
        media = _FakeMedia(song="Song", artist="Artist", album="Album")
        svc = self._service(media)
        _run(svc._sync_now_playing())
        _run(svc._sync_now_playing())
        assert len(svc._broadcasts) == 1


class TestTransport:
    def _service(self):
        svc = HeosPlayerService()
        svc._player = _FakePlayer()
        return svc

    def test_play_url(self):
        svc = self._service()
        assert _run(svc.play(url="http://x/stream.mp3")) is True
        assert ("play_url", "http://x/stream.mp3") in svc._player.calls

    def test_play_ignores_spotify_uri_and_resumes(self):
        svc = self._service()
        assert _run(svc.play(uri="spotify:playlist:abc")) is True
        # uri unsupported -> falls through to resume (player.play)
        assert ("play",) in svc._player.calls
        assert not any(c[0] == "play_url" for c in svc._player.calls)

    def test_transport_without_player_returns_false(self):
        svc = HeosPlayerService()
        assert _run(svc.play(url="http://x")) is False
        assert _run(svc.pause()) is False
        assert _run(svc.next_track()) is False

    def test_capabilities(self):
        svc = HeosPlayerService()
        assert _run(svc.get_capabilities()) == ["url_stream"]


class TestMsToTime:
    def test_conversions(self):
        f = HeosPlayerService._ms_to_time
        assert f(0) == "0:00"
        assert f(5000) == "0:05"
        assert f(185000) == "3:05"
        assert f(3723000) == "1:02:03"
        assert f(None) == "0:00"
        assert f("bogus") == "0:00"
