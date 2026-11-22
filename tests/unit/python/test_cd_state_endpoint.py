"""GET /cd_state — the CD view's pull path.

``cd_update`` is push-only.  A client that connects *after* the last
broadcast never learns about the disc already in the drive: the router
debounces ``/router/resync`` for 5s, and at boot that window is reliably
consumed by the earlier clients before Chromium's page connects.  The CD
view then sits on "Reading disc..." forever, even though the service has
full metadata — which is exactly what happened on the office device
(disc inserted 2026-10-03, dead CD view until manually resynced).

Every other piece of UI state has an HTTP pull to recover from a missed
broadcast (``/router/menu`` for menu items, ``/router/media`` for the
PLAYING view).  ``/cd_state`` is the CD equivalent, and it must serve the
*same* payload the broadcast carries so the two can't drift apart.
"""

from __future__ import annotations

import importlib
import json
import sys
import types

import pytest


def _load_cd_module():
    """Import ``sources.cd`` with pyudev stubbed, leaving sys.modules as found.

    pyudev is Linux-only, so it is absent on a dev Mac.  Stubbing it without
    cleaning up would leak into the shared test process and silently change
    what ``test_cross_source_import`` sees (it expects ``sources.cd`` to skip
    when pyudev is missing).
    """
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
    current_track = 3
    state = "playing"
    shuffle = False
    repeat = True

    def get_status(self):
        return {"state": self.state, "track": self.current_track}


METADATA = {
    "title": "Joyride",
    "artist": "Roxette",
    "album": "Joyride (1991)",
    "year": "1991",
    "artwork": "data:image/png;base64,AAA",
    "back_artwork": None,
    "tracks": [{"num": 1, "title": "Joyride", "duration": "4:24"}],
    "track_count": 1,
    "alternatives": [],
}


def _service(metadata):
    """A CDService with only the attributes the state payload touches."""
    svc = cd.CDService.__new__(cd.CDService)
    svc.metadata = metadata
    svc.cdplayer = _FakePlayer()

    async def _no_external_drive():
        return False

    svc._detect_external_drive = _no_external_drive
    return svc


class _Request:
    pass


@pytest.mark.asyncio
async def test_cd_state_matches_broadcast_payload():
    """The pull must serve byte-for-byte what the push broadcasts."""
    svc = _service(METADATA)
    broadcast_payload = await svc._build_cd_update()

    resp = await svc._handle_cd_state(_Request())
    assert json.loads(resp.body.decode()) == json.loads(
        json.dumps(broadcast_payload)
    )
    assert broadcast_payload["title"] == "Joyride"
    assert broadcast_payload["tracks"]
    assert broadcast_payload["current_track"] == 3
    assert broadcast_payload["state"] == "playing"


@pytest.mark.asyncio
async def test_cd_state_empty_without_disc():
    """No disc metadata → ``{}``, so the view keeps its loading state."""
    resp = await _service(None)._handle_cd_state(_Request())
    assert json.loads(resp.body.decode()) == {}


def test_cd_state_route_registered():
    """Without the route the view's pull 404s and the bug returns."""
    registered = []

    class _Router:
        def add_get(self, path, handler):
            registered.append(path)

        def add_post(self, path, handler):
            registered.append(path)

    class _App:
        router = _Router()

    cd.CDService.add_routes(_service(None), _App())
    assert "/cd_state" in registered


@pytest.mark.asyncio
async def test_broadcast_still_uses_the_shared_builder():
    """``_broadcast_cd_update`` must stay wired to ``_build_cd_update``."""
    svc = _service(METADATA)
    sent = []

    async def _capture(event, data):
        sent.append((event, data))

    svc.broadcast = _capture
    # Only the cd_update broadcast is under test; the PLAYING-view media
    # push that follows for playing/paused state is a separate path.
    svc.cdplayer.state = "available"

    await svc._broadcast_cd_update()
    assert sent == [("cd_update", await svc._build_cd_update())]

    # No disc → no broadcast at all.
    sent.clear()
    svc.metadata = None
    await svc._broadcast_cd_update()
    assert sent == []
