#!/usr/bin/env python3
"""
Action Timestamp — Sonos Player Tests

Tests race prevention specific to Sonos: external playback detection
(Sonos app, Spotify Connect), playback override timestamp propagation,
and stale source rejection after external takeover.

Requires: beo-player-sonos + at least 2 sources (Spotify + Radio recommended)
"""

import sys
import time

sys.path.insert(0, "/tmp")
from helpers import *


# ── Sonos-aware helpers ──

def sonos_stop_all():
    """Stop playback and clear active source, waiting for Sonos to confirm."""
    try:
        post(f"{PLAYER}/player/stop", {})
    except Exception:
        pass
    # Wait for Sonos to actually stop — its monitor polls every 0.5s
    for _ in range(10):
        time.sleep(0.5)
        try:
            ps = player_state()
            if ps.get("state") in ("stopped", None):
                break
        except Exception:
            break
    # Now clear sources
    for sid in SOURCE_PORTS:
        try:
            router_source(sid, "available")
        except Exception:
            pass
    time.sleep(1)


def prime_radio():
    """Ensure radio has a station loaded so activate will actually play.
    Plays a station briefly then pauses — leaves _current_station set
    (stop would clear it). Uses player stop separately so the Sonos stops."""
    radio_port = SOURCE_PORTS.get("radio", 8779)
    try:
        status = get(f"http://localhost:{radio_port}/status", timeout=3)
        if status.get("station"):
            return True  # already has a station
        # Pick first popular station and play it
        browse = get(f"http://localhost:{radio_port}/browse?path=popular", timeout=10)
        if browse.get("items"):
            uuid = browse["items"][0]["stationuuid"]
            post(f"http://localhost:{radio_port}/command",
                 {"command": "play_station", "stationuuid": uuid}, timeout=15)
            time.sleep(3)
            # Pause (not stop!) — keeps _current_station set
            post(f"http://localhost:{radio_port}/command",
                 {"command": "toggle"}, timeout=10)
            time.sleep(1)
            # Stop the actual Sonos player so nothing is audible
            try:
                post(f"{PLAYER}/player/stop", {})
            except Exception:
                pass
            time.sleep(1)
            router_source("radio", "available")
            return True
    except Exception as e:
        print(f"  Warning: could not prime radio: {e}")
    return False


# ── Tests: Override mechanism ──
# These use crafted stale timestamps relative to the CURRENT router state.
# They never set artificial future timestamps — only past ones that should
# be rejected.

def test_01_override_updates_router_ts():
    """playback_override with action_ts updates the router's latest_action_ts."""
    sonos_stop_all()

    router_event(src_a)
    time.sleep(3)
    ts_before = router_status()["latest_action_ts"]
    assert ts_before > 0, f"Expected nonzero ts after activation: {ts_before}"

    # Use a timestamp slightly above current — simulates what the player does
    # (time.monotonic() on the same machine, moments later)
    override_ts = ts_before + 0.5
    result = playback_override(force=True, action_ts=override_ts)
    assert result.get("cleared") is True, f"Override should clear active: {result}"

    status = router_status()
    assert status["latest_action_ts"] >= override_ts, \
        f"Router ts should be >= override: {status['latest_action_ts']} < {override_ts}"
    assert status["active_source"] is None, \
        f"Active should be cleared: {status['active_source']}"
    sonos_stop_all()


def test_02_stale_source_after_override():
    """After override, stale register from the old source is rejected."""
    sonos_stop_all()

    router_event(src_a)
    time.sleep(3)
    old_ts = router_status()["latest_action_ts"]

    # Override advances the timestamp
    playback_override(force=True, action_ts=old_ts + 0.5)
    time.sleep(0.5)

    # Old source tries to re-register with its original timestamp
    router_source(src_a, "playing", action_ts=old_ts,
                  name=src_a.upper(),
                  command_url=f"http://localhost:{SOURCE_PORTS[src_a]}/command",
                  player="remote")

    status = router_status()
    assert status["active_source"] is None, \
        f"Old source stole active after override: {status['active_source']}"
    sonos_stop_all()


def test_03_stale_play_after_override():
    """After override, stale player_play is rejected by the player."""
    sonos_stop_all()

    router_event(src_a)
    time.sleep(3)
    old_ts = router_status()["latest_action_ts"]

    # Advance player's timestamp (simulates what the player does internally)
    override_ts = old_ts + 0.5
    player_play(action_ts=override_ts, url="http://example.com/external.mp3")
    playback_override(force=True, action_ts=override_ts)

    result = player_play(action_ts=old_ts, url="http://example.com/stale.mp3")
    assert result.get("status") == "dropped", \
        f"Stale play after override should be dropped: {result}"
    sonos_stop_all()


def test_04_stale_media_after_override():
    """After override, stale media updates are dropped."""
    sonos_stop_all()

    router_event(src_a)
    time.sleep(3)
    old_ts = router_status()["latest_action_ts"]

    playback_override(force=True, action_ts=old_ts + 0.5)

    result = router_media(src_a, title="Stale After Override", action_ts=old_ts)
    assert result.get("dropped") is True, \
        f"Stale media after override should be dropped: {result}"
    sonos_stop_all()


def test_05_override_then_new_activation():
    """After override clears active, a fresh source activation works.
    The fresh route_event stamps a new time.monotonic() which becomes
    the latest, regardless of what the override set."""
    sonos_stop_all()

    router_event(src_a)
    time.sleep(3)
    ts_before = router_status()["latest_action_ts"]

    # Override — use a small offset so it doesn't exceed real monotonic time
    playback_override(force=True, action_ts=ts_before + 0.1)
    time.sleep(1)
    assert router_status()["active_source"] is None, \
        f"Override should clear active: {router_status()['active_source']}"

    # New activation — route_event stamps time.monotonic() unconditionally
    router_event(src_b)
    time.sleep(4)
    status = router_status()
    assert status["active_source"] == src_b, \
        f"New activation after override failed: active={status['active_source']}"
    assert status["latest_action_ts"] > ts_before, \
        f"New ts should exceed original: {status['latest_action_ts']} <= {ts_before}"
    sonos_stop_all()


def test_06_override_without_active_source():
    """Override when no source is active — updates timestamp, doesn't crash."""
    sonos_stop_all()
    time.sleep(1)

    assert router_status()["active_source"] is None, \
        f"Expected no active source: {router_status()['active_source']}"
    ts_before = router_status()["latest_action_ts"]

    override_ts = ts_before + 0.1
    playback_override(force=True, action_ts=override_ts)

    status = router_status()
    assert status["latest_action_ts"] >= override_ts, \
        f"Ts should be >= override: {status['latest_action_ts']} < {override_ts}"
    sonos_stop_all()


# ── Tests: Real playback on Sonos ──

def test_07_real_spotify_then_radio():
    """Real Spotify → Radio switch on Sonos. After switch, Spotify/Sonos
    monitor must not steal active back."""
    sonos_stop_all()

    router_event("spotify")
    time.sleep(6)
    status = router_status()
    if status["active_source"] != "spotify":
        raise Exception(f"Spotify didn't activate: {status['active_source']}")

    router_event("radio")
    time.sleep(5)

    status = router_status()
    assert status["active_source"] == "radio", \
        f"Radio should be active: {status['active_source']}"

    # Wait for Sonos monitor / Spotify poll to potentially try stealing back
    time.sleep(5)
    assert router_status()["active_source"] == "radio", \
        f"Spotify/Sonos stole active back: {router_status()['active_source']}"
    sonos_stop_all()


def test_08_rapid_spotify_then_radio():
    """Activate Spotify, quickly switch to Radio before Spotify loads.
    Radio should win."""
    sonos_stop_all()

    router_event("spotify")
    time.sleep(1)

    router_event("radio")
    time.sleep(5)

    status = router_status()
    assert status["active_source"] == "radio", \
        f"Radio should win rapid switch: {status['active_source']}"
    sonos_stop_all()


def main():
    global src_a, src_b

    print("=" * 55)
    print(" Action Timestamp Tests — Sonos Player")
    print("=" * 55)

    sources = discover()
    print(f"\n  Available: {', '.join(sorted(sources.keys()))}")

    # Verify Sonos player
    try:
        ps = get(f"{PLAYER}/player/status")
        if ps.get("player") != "sonos":
            print(f"  WARNING: Player is {ps.get('player')}, not sonos")
    except Exception:
        pass

    # Pick two sources
    has_spotify = "spotify" in sources
    has_radio = "radio" in sources
    for a, b in [("spotify", "radio"), ("spotify", "apple_music"),
                 ("radio", "spotify")]:
        if a in sources and b in sources:
            src_a, src_b = a, b
            break
    else:
        print("  ERROR: Need at least 2 running sources")
        sys.exit(2)

    print(f"  Test sources: {src_a}, {src_b}")

    # Prime radio with a station
    if has_radio:
        radio_ok = prime_radio()
        if radio_ok:
            print("  Radio: primed with station")
        else:
            print("  Radio: no station available")
            has_radio = False

    # Lower volume
    orig_vol = router_status()["volume"]
    if orig_vol > 10:
        set_volume(10)
        print(f"  Volume: {orig_vol}% -> 10% (will restore)")

    print()

    try:
        test("01. Override updates router latest_action_ts",
             test_01_override_updates_router_ts)
        test("02. Stale register after override rejected",
             test_02_stale_source_after_override)
        test("03. Stale player_play after override rejected",
             test_03_stale_play_after_override)
        test("04. Stale media after override rejected",
             test_04_stale_media_after_override)
        test("05. Override then fresh activation works",
             test_05_override_then_new_activation)
        test("06. Override with no active source",
             test_06_override_without_active_source)

        if has_spotify and has_radio:
            test("07. Real Spotify -> Radio on Sonos",
                 test_07_real_spotify_then_radio)
            test("08. Rapid Spotify -> Radio before Spotify loads",
                 test_08_rapid_spotify_then_radio)
        else:
            missing = []
            if not has_spotify:
                missing.append("spotify")
            if not has_radio:
                missing.append("radio (with station)")
            skip("07. Real Spotify -> Radio", f"need {' + '.join(missing)}")
            skip("08. Rapid Spotify -> Radio", f"need {' + '.join(missing)}")
    finally:
        sonos_stop_all()
        if orig_vol > 10:
            set_volume(orig_vol)
            print(f"\n  Volume restored to {orig_vol}%")

    failed = summary()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
