"""The BS5c's Mozart and ASE clients driven against the B&O device emulator.

The bo-emulator (bs9000-gateway repo, bo-emulator/; it lived in
private/bo-emulator here until 3 Oct 2026) is the product the Bang & Olufsen
app and Home Assistant were watched talking to (its README records what each verified
live). Running the real player services and volume adapters against it,
in-process on loopback ports, pins the whole wire contract — paths, bodies,
notification frames — rather than hand-picked JSON snippets.

Looked up in a sibling bs9000-gateway checkout (or $BO_EMULATOR = path to
bo_emulator.py); skipped when neither is there.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import os
import socket
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
EMULATOR = Path(os.environ.get("BO_EMULATOR")
                or REPO_ROOT.parent / "bs9000-gateway" / "bo-emulator" / "bo_emulator.py")
pytestmark = [
    pytest.mark.skipif(not EMULATOR.is_file(),
                       reason="bo_emulator.py not found (sibling bs9000-gateway checkout or $BO_EMULATOR)"),
    # the emulator registers plain lambdas as aiohttp handlers (its choice)
    pytest.mark.filterwarnings("ignore:Bare functions are deprecated:DeprecationWarning"),
]

sys.path.insert(0, str(REPO_ROOT / "services"))

import players.ase as ase_mod  # noqa: E402
import players.mozart as mozart_mod  # noqa: E402
from lib.volume_adapters.ase import AseVolume  # noqa: E402
from lib.volume_adapters.mozart import MozartVolume  # noqa: E402
import lib.volume_adapters.ase as ase_vol_mod  # noqa: E402
import lib.volume_adapters.mozart as mozart_vol_mod  # noqa: E402

JID_ASE = "3064.1000000.20000001@products.bang-olufsen.com"


def _emulator_module():
    spec = importlib.util.spec_from_file_location("bo_emulator", EMULATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.AsyncZeroconf = None  # never advertise a test double on the real LAN
    return mod


def _free_ports(n: int) -> list[int]:
    """n distinct free TCP ports. The emulator also needs base and base+1
    for its loopback API copies, so callers pass a base with a free
    neighbour (see _free_pair)."""
    socks, ports = [], []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        socks.append(s)
        ports.append(s.getsockname()[1])
    for s in socks:
        s.close()
    return ports


def _free_pair() -> int:
    for _ in range(50):
        (p,) = _free_ports(1)
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p + 1))
            except OSError:
                continue
        return p
    raise RuntimeError("no free port pair")


def mozart_config(api: int, notify: int, console: int, internal: int) -> dict:
    """config.mozart.balance.json, on loopback test ports."""
    return {
        "platform": "mozart", "model": "Beosound Balance", "friendly_name": "Balance (emulated)",
        "serial": "38378341", "type_number": "6471", "item_number": "1120000",
        "software_version": "6.3.0.31", "mac": "8c:1f:64:ba:1a:01",
        "bind": ["127.0.0.1"], "advertise_ip": "127.0.0.1", "advertise_ip6": [],
        "internal_port_base": internal,
        "ports": {"api": api, "notification": notify, "console": console},
        "sources": [
            {"id": "lineIn", "name": "Line-In", "type": "lineIn"},
            {"id": "spotify", "name": "Spotify", "type": "spotify", "remote_uri": "aux-spotify"},
            {"id": "tidal", "name": "Tidal", "type": "tidal", "remote_uri": "aux-tidal"},
            {"id": "netRadio", "name": "B&O Radio", "type": "netRadio"},
        ],
        "items": {
            "tidal": [
                {"title": "Moondance", "artist": "Van Morrison", "album": "Moondance", "track": 1,
                 "duration": 273, "art_url": "http://127.0.0.1:1/moondance.jpg"},
                {"title": "Into the Mystic", "artist": "Van Morrison", "album": "Moondance", "track": 2,
                 "duration": 205, "art_url": "http://127.0.0.1:1/mystic.jpg"},
            ],
            "spotify": [
                {"title": "So What", "artist": "Miles Davis", "album": "Kind of Blue", "track": 1,
                 "duration": 562, "art_url": "http://127.0.0.1:1/sowhat.jpg"},
            ],
        },
        "initial_state": {"power": "on", "source": "lineIn", "volume": 30, "muted": False,
                          "playback": "playing",
                          "metadata": {"title": "Line-In", "artist": "", "album": "", "duration": 0,
                                       "progress": 0, "art_url": ""}},
        "forward_url": "",
    }


def ase_config(api: int, console: int, internal: int) -> dict:
    """config.ase.example.json (Beoplay A9) plus a net-radio and a CD source so
    the RADIO / legacy frame shapes get exercised."""
    return {
        "platform": "ase", "model": "Beoplay A9", "friendly_name": "A9 (emulated)",
        "serial": "20000001", "type_number": "3064", "item_number": "1000000",
        "software_version": "3.4.1.29", "mac": "8c:1f:64:a9:00:01",
        "bind": ["127.0.0.1"], "advertise_ip": "127.0.0.1", "advertise_ip6": [],
        "internal_port_base": internal,
        "ports": {"api": api, "console": console},
        "sources": [
            {"id": "spotify", "name": "Spotify"},
            {"id": "radio", "name": "TuneIn", "category": "RADIO"},
            {"id": "CD", "name": "CD", "category": "MUSIC"},
            {"id": "linein", "name": "Line-In"},
        ],
        "items": {
            "spotify": [
                {"title": "ASE Test Track", "artist": "ASE Artist", "album": "ASE Album", "duration": 210,
                 "art_url": "http://127.0.0.1:1/ase.jpg"},
                {"title": "Second Track", "artist": "ASE Artist", "album": "ASE Album", "duration": 180,
                 "art_url": ""},
            ],
            "radio": [{"title": "P3", "artist": "Morgonpasset", "album": "", "duration": 0, "art_url": ""}],
            "CD": [{"title": "Track 1", "artist": "", "album": "", "duration": 200, "art_url": ""},
                   {"title": "Track 2", "artist": "", "album": "", "duration": 190, "art_url": ""}],
        },
        "initial_state": {"power": "on", "source": "spotify", "volume": 28, "muted": False,
                          "playback": "playing",
                          "metadata": {"title": "ASE Test Track", "artist": "ASE Artist",
                                       "album": "ASE Album", "duration": 210, "progress": 0,
                                       "art_url": "http://127.0.0.1:1/ase.jpg"}},
        "forward_url": "",
    }


class Emulated:
    """An emulator instance running in the current event loop."""

    def __init__(self, cfg: dict):
        self.mod = _emulator_module()
        self.emu = self.mod.Emulator(cfg)
        self.api_port = cfg["ports"]["api"]
        self._task: asyncio.Task | None = None

    async def __aenter__(self):
        self._task = asyncio.create_task(self.emu.run())
        for _ in range(100):
            try:
                _, w = await asyncio.open_connection("127.0.0.1", self.api_port)
                w.close()
                return self
            except OSError:
                await asyncio.sleep(0.05)
        raise RuntimeError("emulator did not come up")

    async def __aexit__(self, *exc):
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    @property
    def state(self):
        return self.emu.state

    def requests(self) -> list[str]:
        """Request lines the emulator logged, e.g. 'POST /BeoZone/Zone/List/StepUp'."""
        return [e["summary"] for e in self.emu.bus.recent() if e["kind"] == "rx"]

    def commands(self) -> list[str]:
        return [e["summary"] for e in self.emu.bus.recent() if e["kind"] == "cmd"]


async def _wait_for(pred, timeout=5.0, what="condition"):
    for _ in range(int(timeout / 0.05)):
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _stub_player_io(player):
    """Cut the player off from the router/input services and the artwork
    fetch; the tests inspect the calls instead."""
    player.running = True
    player.broadcast_media_update = AsyncMock()
    player.trigger_wake = AsyncMock()
    player.notify_router_playback_override = AsyncMock()
    player.report_volume_to_router = AsyncMock()
    player.fetch_artwork = AsyncMock(return_value=None)
    player.register_speakers_source = AsyncMock(return_value=True)
    return player


def _broadcasts(player) -> list[tuple[dict, str]]:
    return [(c.args[0], c.args[1] if len(c.args) > 1 else c.kwargs.get("reason"))
            for c in player.broadcast_media_update.await_args_list]


@contextlib.asynccontextmanager
async def mozart_setup(monkeypatch):
    api, notify, console = _free_ports(3)
    internal = _free_pair()
    monkeypatch.setattr(mozart_mod, "MOZART_PORT", api)
    monkeypatch.setattr(mozart_mod, "MOZART_WS_PORT", notify)
    monkeypatch.setattr(mozart_vol_mod, "MOZART_PORT", api)
    monkeypatch.setattr(mozart_mod, "effective_player_ip", lambda: "127.0.0.1")
    async with Emulated(mozart_config(api, notify, console, internal)) as emu:
        async with aiohttp.ClientSession() as session:
            player = _stub_player_io(mozart_mod.MozartPlayer())
            player.ip = "127.0.0.1"
            player._http_session = session
            try:
                yield emu, player, session
            finally:
                player.running = False


@contextlib.asynccontextmanager
async def ase_setup(monkeypatch):
    api, console = _free_ports(2)
    internal = _free_pair()
    monkeypatch.setattr(ase_mod, "ASE_PORT", api)
    monkeypatch.setattr(ase_vol_mod, "ASE_PORT", api)
    monkeypatch.setattr(ase_mod, "effective_player_ip", lambda: "127.0.0.1")
    async with Emulated(ase_config(api, console, internal)) as emu:
        async with aiohttp.ClientSession() as session:
            player = _stub_player_io(ase_mod.AsePlayer())
            player._http_session = session
            try:
                yield emu, player, session
            finally:
                player.running = False


# ── Mozart ────────────────────────────────────────────────────────────────

def test_mozart_poll_reads_playback_state(monkeypatch):
    async def scenario():
        async with mozart_setup(monkeypatch) as (emu, player, _):
            await emu.emu.do_source("tidal")
            await player._handle_state(await player._get("/playback/state"))
            media, reason = _broadcasts(player)[-1]
            assert reason == "track_change"
            assert (media["title"], media["artist"], media["album"]) == ("Moondance", "Van Morrison", "Moondance")
            assert media["state"] == "playing" and media["duration"] == "4:33"
            player.fetch_artwork.assert_awaited_once()
            assert player.fetch_artwork.await_args.args[0] == "http://127.0.0.1:1/moondance.jpg"

            await emu.emu.do_pause()
            await player._handle_state(await player._get("/playback/state"))
            media, reason = _broadcasts(player)[-1]
            assert (reason, media["state"]) == ("state_change", "paused")
    asyncio.run(scenario())


def test_mozart_transport_commands(monkeypatch):
    async def scenario():
        async with mozart_setup(monkeypatch) as (emu, player, _):
            await emu.emu.do_source("tidal")
            assert await player.next_track() and emu.state.title == "Into the Mystic"
            assert await player.prev_track() and emu.state.title == "Moondance"
            assert await player.pause() and emu.state.playback == "paused"
            assert await player.resume() and emu.state.playback == "playing"
            assert await player.stop() and emu.state.playback == "stopped"
            assert "POST /api/v1/playback/command/skip" in emu.requests()
            assert "POST /api/v1/playback/command/prev" in emu.requests()
    asyncio.run(scenario())


def test_mozart_play_url_uses_uri_location(monkeypatch):
    async def scenario():
        async with mozart_setup(monkeypatch) as (emu, player, _):
            assert await player.play(url="http://radio.example/p3.mp3")
            assert emu.state.active_source == "uriStreamer"
            assert emu.state.playback == "playing" and emu.state.title == "p3.mp3"
            assert any(c.startswith("PLAY URI") for c in emu.commands())
    asyncio.run(scenario())


def test_mozart_volume_and_standby(monkeypatch):
    async def scenario():
        async with mozart_setup(monkeypatch) as (emu, _, session):
            vol = MozartVolume("127.0.0.1", 100, session)
            await vol.set_volume(37)
            await _wait_for(lambda: emu.state.volume == 37, what="volume applied")
            assert await vol.get_volume() == 37
            assert await vol.is_on() is True

            await vol.power_off()
            assert emu.state.power == "standby" and emu.state.playback == "stopped"
            assert vol.is_on_cached() is False
            assert "PUT /api/v1/state/standby" in emu.requests()

            await vol.power_on()
            assert emu.state.power == "on" and vol.is_on_cached() is True
    asyncio.run(scenario())


def test_mozart_websocket_events_drive_state(monkeypatch):
    async def scenario():
        async with mozart_setup(monkeypatch) as (emu, player, _):
            await emu.emu.do_source("tidal")
            ws_task = asyncio.create_task(player._ws_loop())
            try:
                # connecting seeds from /playback/state
                await _wait_for(lambda: player._ws_connected, what="ws connect")
                await _wait_for(lambda: _broadcasts(player), what="seed broadcast")
                assert _broadcasts(player)[-1][0]["title"] == "Moondance"

                await emu.emu.do_volume(55)
                # the socket primes the current volume (30) on connect, like a real
                # Mozart product does — wait for the event carrying the new level
                await _wait_for(lambda: player.report_volume_to_router.await_args
                                and player.report_volume_to_router.await_args.args[0] == 55,
                                what="volume event")

                await emu.emu.do_source("spotify")
                await _wait_for(lambda: _broadcasts(player)[-1][0]["title"] == "So What", what="metadata event")
                assert _broadcasts(player)[-1][0]["artist"] == "Miles Davis"

                await emu.emu.do_pause()
                await _wait_for(lambda: _broadcasts(player)[-1] == (_broadcasts(player)[-1][0], "state_change")
                                and _broadcasts(player)[-1][0]["state"] == "paused", what="state event")
            finally:
                ws_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ws_task
    asyncio.run(scenario())


def test_mozart_group_list_shows_current_room(monkeypatch):
    async def scenario():
        async with mozart_setup(monkeypatch) as (emu, player, _):
            await emu.emu.do_source("spotify")
            devices = await player.group_list_devices()
            assert [d["current"] for d in devices] == [True]
            assert devices[0]["title"] == "So What" and devices[0]["state"] == "playing"
    asyncio.run(scenario())


# ── ASE ───────────────────────────────────────────────────────────────────

def test_ase_notification_stream_primes_now_playing(monkeypatch):
    async def scenario():
        async with ase_setup(monkeypatch) as (emu, player, _):
            task = asyncio.create_task(player._notify_loop())
            try:
                await _wait_for(lambda: _broadcasts(player), what="primed now-playing")
                media, reason = _broadcasts(player)[-1]
                assert reason == "track_change"
                assert (media["title"], media["artist"], media["album"]) == ("ASE Test Track", "ASE Artist", "ASE Album")
                assert media["duration"] == "3:30"
                assert player.fetch_artwork.await_args.args[0] == "http://127.0.0.1:1/ase.jpg"
                assert player._active_source == f"spotify:{JID_ASE}"
                assert player._current_playback_state == "playing"
                # level 28 of 0-90 -> 31 %
                await _wait_for(lambda: player.report_volume_to_router.await_count, what="volume frame")
                assert player.report_volume_to_router.await_args.args[0] == 31
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    asyncio.run(scenario())


def test_ase_live_frames(monkeypatch):
    async def scenario():
        async with ase_setup(monkeypatch) as (emu, player, _):
            task = asyncio.create_task(player._notify_loop())
            try:
                await _wait_for(lambda: _broadcasts(player), what="primed")

                await emu.emu.do_volume(45)
                await _wait_for(lambda: player.report_volume_to_router.await_args.args[0] == 50, what="VOLUME 45/90")

                await emu.emu.do_pause()
                await _wait_for(lambda: _broadcasts(player)[-1][0]["state"] == "paused", what="PROGRESS pause")

                await emu.emu.do_source("radio")  # NOW_PLAYING_NET_RADIO: name + liveDescription
                await _wait_for(lambda: _broadcasts(player)[-1][0]["title"] == "P3", what="net radio frame")
                assert _broadcasts(player)[-1][0]["artist"] == "Morgonpasset"
                assert player._active_source == f"radio:{JID_ASE}"

                await emu.emu.do_standby()  # SHUTDOWN
                await _wait_for(lambda: player._current_playback_state == "stopped", what="SHUTDOWN")
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    asyncio.run(scenario())


def test_ase_transport_follows_source_kind(monkeypatch):
    async def scenario():
        async with ase_setup(monkeypatch) as (emu, player, _):
            # streaming source: Stream/Forward|Backward
            player._active_source = f"spotify:{JID_ASE}"
            assert await player.next_track() and emu.state.title == "Second Track"
            assert await player.prev_track() and emu.state.title == "ASE Test Track"
            assert "POST /BeoZone/Zone/Stream/Forward" in emu.requests()
            assert "POST /BeoZone/Zone/Stream/Backward" in emu.requests()

            # legacy source (CD): the app steps with List/StepUp|StepDown
            await emu.emu.do_source("CD")
            player._active_source = f"CD:{JID_ASE}"
            assert await player.next_track() and emu.state.title == "Track 2"
            assert await player.prev_track() and emu.state.title == "Track 1"
            assert "POST /BeoZone/Zone/List/StepUp" in emu.requests()
            assert "POST /BeoZone/Zone/List/StepDown" in emu.requests()

            assert await player.pause() and emu.state.playback == "paused"
            assert await player.resume() and emu.state.playback == "playing"
            assert await player.stop() and emu.state.playback == "stopped"
    asyncio.run(scenario())


def test_ase_volume_adapter_scales_and_controls_power(monkeypatch):
    async def scenario():
        async with ase_setup(monkeypatch) as (emu, _, session):
            vol = AseVolume("127.0.0.1", 100, session)
            await vol.set_volume(50)
            await _wait_for(lambda: emu.state.volume == 45, what="50% -> level 45 of 90")
            assert await vol.get_volume() == 50
            assert "PUT /BeoZone/Zone/Sound/Volume/Speaker/Level" in emu.requests()

            assert await vol.is_on() is True
            await vol.power_off()
            assert emu.state.power == "standby" and vol.is_on_cached() is False
            await vol.power_on()
            assert emu.state.power == "on" and vol.is_on_cached() is True
    asyncio.run(scenario())
