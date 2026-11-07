"""beo-cast, the parts that do not need a Chromecast on the network.

The Cast protocol itself lives behind pychromecast and is only exercised on
real hardware. What is worth pinning here is everything around it: that the
module imports without the dependency present, that the URL handed to a
receiver is one it can actually fetch, and that "where the UI belongs" and
"whether it got there" stay separate — a receiver asleep at boot must still
be retried rather than quietly forgotten.
"""

import importlib

import pytest

cast = importlib.import_module("cast")


@pytest.fixture
def screen(monkeypatch):
    # cfg() reads /etc/beosound5c/config.json on a device; with no config the
    # defaults apply, which is what most of these assert.
    monkeypatch.setattr(cast, "cfg", lambda *a, **k: k.get("default"))
    return cast.CastScreen()


def test_imports_without_pychromecast_installed():
    """The import must not drag in the Cast library.

    beo-cast runs on devices where nobody has asked for mirroring, and an
    optional dependency that breaks start-up is worse than a missing feature.
    pychromecast is imported inside the methods that use it.
    """
    assert "pychromecast" not in dir(cast)


def test_default_app_id_is_the_url_loading_receiver(screen):
    assert screen._app_id == cast.DEFAULT_APP_ID


def test_url_override_is_used_verbatim(monkeypatch):
    monkeypatch.setattr(cast, "cfg",
                        lambda section, key, default=None:
                        "http://example.invalid/wall" if key == "url" else default)
    assert cast.CastScreen()._ui_url("10.0.0.5") == "http://example.invalid/wall"


def test_url_points_at_this_device_not_localhost(screen, monkeypatch):
    """A receiver fetching "localhost" would fetch from itself."""
    monkeypatch.setattr(cast, "_local_address_towards", lambda host: "10.0.0.9")
    url = screen._ui_url("10.0.0.5")
    assert url.startswith("http://10.0.0.9/")
    assert "localhost" not in url


def test_url_carries_a_cache_buster(screen, monkeypatch):
    """DashCast ignores a load for the URL it is already showing, so two
    casts in a row must not produce the same string."""
    monkeypatch.setattr(cast, "_local_address_towards", lambda host: "10.0.0.9")
    monkeypatch.setattr(cast.time, "time", lambda: 1000)
    first = screen._ui_url("10.0.0.5")
    monkeypatch.setattr(cast.time, "time", lambda: 1001)
    assert screen._ui_url("10.0.0.5") != first


def test_host_lookup_uses_the_discovered_list(screen):
    screen._receivers = [{"name": "Hall", "model": "x", "host": "10.0.0.7"}]
    assert screen._host_of("Hall") == "10.0.0.7"
    assert screen._host_of("Nowhere") is None


@pytest.mark.asyncio
async def test_failed_cast_still_records_where_the_ui_belongs(screen, monkeypatch):
    """Asleep at boot is not the same as not wanted.

    start() records the target before attempting, so the reconcile loop keeps
    trying once the receiver comes back. Reporting it as casting would be a
    lie, hence the separate flag.
    """
    async def refresh():
        screen._receivers = [{"name": "Hall", "model": "x", "host": "10.0.0.7"}]
    monkeypatch.setattr(screen, "refresh", refresh)
    monkeypatch.setattr(cast, "_local_address_towards", lambda host: "10.0.0.9")

    def boom(*a):
        raise LookupError("asleep")
    monkeypatch.setattr(screen, "_cast_sync", boom)

    with pytest.raises(LookupError):
        await screen.start("Hall")

    assert screen._target == "Hall"     # still where it belongs
    assert screen._active is False      # but not showing


@pytest.mark.asyncio
async def test_stop_clears_both(screen, monkeypatch):
    screen._target = "Hall"
    screen._active = True
    monkeypatch.setattr(screen, "_stop_sync", lambda receiver: None)
    await screen.stop()
    assert screen._target is None
    assert screen._active is False


@pytest.mark.asyncio
async def test_stop_is_a_no_op_when_nothing_is_cast(screen, monkeypatch):
    called = []
    monkeypatch.setattr(screen, "_stop_sync", lambda receiver: called.append(receiver))
    await screen.stop()
    assert called == []


class _Status:
    def __init__(self, app_id):
        self.app_id = app_id


class _Cast:
    def __init__(self, status):
        self.status = status


def test_unreachable_receiver_is_not_an_answer(screen, monkeypatch):
    """"Could not tell" must not read as "not showing".

    This is what turned a television that was simply off into a re-cast
    attempt every thirty seconds: the check could not reach it, returned
    False, and the reconcile loop dutifully tried to put the UI back.
    """
    def unreachable(receiver):
        raise LookupError("asleep")
    monkeypatch.setattr(screen, "_connect_sync", unreachable)
    assert screen._showing_sync("Hall", "84912283") is None


def test_no_status_is_not_an_answer_either(screen, monkeypatch):
    monkeypatch.setattr(screen, "_connect_sync", lambda r: _Cast(None))
    assert screen._showing_sync("Hall", "84912283") is None


def test_a_reachable_receiver_gives_a_definite_answer(screen, monkeypatch):
    monkeypatch.setattr(screen, "_connect_sync",
                        lambda r: _Cast(_Status("84912283")))
    assert screen._showing_sync("Hall", "84912283") is True

    monkeypatch.setattr(screen, "_connect_sync",
                        lambda r: _Cast(_Status("something-else")))
    assert screen._showing_sync("Hall", "84912283") is False


def test_discovery_reads_what_the_browser_already_knows(screen, monkeypatch):
    """No scan per call: the browser listens continuously, so a lookup is a
    dict read. Starting and stopping one per operation is what produced
    zeroconf's "instance loop must be running" every thirty seconds."""
    class Info:
        friendly_name, model_name, host = "Hall", "Some TV", "10.0.0.7"

    class Browser:
        devices = {"uuid-1": Info()}

    screen._browser = Browser()
    assert screen._discover_sync() == [
        {"name": "Hall", "model": "Some TV", "host": "10.0.0.7"}]


def test_stopping_something_already_gone_is_not_an_error(screen, monkeypatch):
    def unreachable(receiver):
        raise LookupError("asleep")
    monkeypatch.setattr(screen, "_connect_sync", unreachable)
    screen._stop_sync("Hall")        # must not raise


# ── Connection reuse ──────────────────────────────────────────────────────
# pychromecast is deliberately absent from the test environment (see the
# import test above), so the library is stood in for rather than imported.

class _Socket:
    def __init__(self, connected=True):
        self.is_connected = connected


class _LiveCast:
    def __init__(self):
        self.socket_client = _Socket()
        self.disconnected = False
        self.status = _Status("84912283")

    def wait(self, timeout=None):
        pass

    def disconnect(self, blocking=True):
        self.disconnected = True

    def quit_app(self):
        pass


@pytest.fixture
def fake_lib(monkeypatch, screen):
    """Stands in for pychromecast, which cast.py imports inside its
    methods. Hands back whatever the test puts in `built`."""
    import sys, types
    built = []
    mod = types.ModuleType("pychromecast")

    def factory(info, zconf):
        tv = built.pop(0) if built else _LiveCast()
        factory.made.append(tv)
        return tv
    factory.made = []
    mod.get_chromecast_from_cast_info = factory

    monkeypatch.setitem(sys.modules, "pychromecast", mod)
    monkeypatch.setattr(screen, "_info_for", lambda r: object())
    return factory


def test_one_connection_is_reused_not_rebuilt(screen, fake_lib):
    """Each connection carries a socket and a thread, and nothing closes
    them. Built per check they accumulate at two a minute, until the
    television stops accepting connections — measured 32 against one set
    after half an hour, after which the service could not reach it at all.
    """
    first = screen._connect_sync("Hall")
    for _ in range(5):
        assert screen._connect_sync("Hall") is first
    assert len(fake_lib.made) == 1, \
        f"built {len(fake_lib.made)} connections for one receiver"


def test_a_dead_connection_is_replaced_and_closed(screen, fake_lib):
    first = screen._connect_sync("Hall")
    first.socket_client.is_connected = False      # the set went away
    second = screen._connect_sync("Hall")
    assert second is not first
    assert first.disconnected, "the dead connection was abandoned, not closed"


def test_switching_receiver_closes_the_old_one(screen, fake_lib):
    first = screen._connect_sync("Hall")
    screen._connect_sync("Kitchen")
    assert first.disconnected


def test_a_failed_connect_does_not_leak(screen, fake_lib):
    """A half-built connection still owns a socket; dropping the reference
    would leak exactly what the reuse exists to avoid."""
    tv = _LiveCast()

    def boom(timeout=None):
        raise TimeoutError("wait timed out")
    tv.wait = boom

    import sys
    sys.modules["pychromecast"].get_chromecast_from_cast_info = \
        lambda info, zconf: tv

    with pytest.raises(TimeoutError):
        screen._connect_sync("Hall")
    assert tv.disconnected, "a failed connect left its socket open"
    assert screen._cast is None


@pytest.mark.asyncio
async def test_a_cast_records_when_it_happened(screen, fake_lib, monkeypatch):
    """The reconcile check skips a receiver that was just cast to. It takes
    a few seconds to finish launching and the status we hold lags it, so a
    check straight afterwards sees the old app and casts again — which the
    viewer sees as the picture loading twice."""
    async def refresh():
        screen._receivers = [{"name": "Hall", "model": "x", "host": "10.0.0.7"}]
    monkeypatch.setattr(screen, "refresh", refresh)
    monkeypatch.setattr(cast, "_local_address_towards", lambda host: "10.0.0.9")
    monkeypatch.setattr(screen, "_cast_sync", lambda receiver, url: url)

    assert screen._cast_at == 0.0
    await screen.start("Hall")
    assert screen._cast_at > 0.0, "nothing recorded when the cast happened"
    assert screen._active is True
