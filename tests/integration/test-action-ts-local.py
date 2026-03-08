#!/usr/bin/env python3
"""
Action Timestamp — Local Player Tests

Tests race prevention with real playback on a local-player device.
Requires: beo-player-local + radio + usb (+ optionally cd, plex)
"""

import sys
import time

sys.path.insert(0, "/tmp")
from helpers import *


def test_01_real_radio_then_switch(sources):
    """Start radio, let it play, switch to USB. Radio's poll loop
    must not steal active back."""
    stop_all()

    router_event("radio")
    time.sleep(5)
    status = router_status()
    if status["active_source"] != "radio":
        raise Exception(f"Radio didn't activate: {status['active_source']}")

    router_event("usb")
    time.sleep(3)
    assert router_status()["active_source"] == "usb"

    # Wait for radio's poll loop to potentially try re-registering
    time.sleep(3)
    assert router_status()["active_source"] == "usb", \
        "Radio poll stole active back"
    stop_all()


def test_02_rapid_radio_to_plex(sources):
    """Start radio (slow stream connect), switch to plex before radio
    is fully up. Plex should win."""
    stop_all()

    router_event("radio")
    time.sleep(1)  # radio starts connecting but isn't playing yet

    router_event("plex")
    time.sleep(4)

    assert router_status()["active_source"] == "plex", \
        f"Plex should win: {router_status()['active_source']}"
    stop_all()


def test_03_cd_disc_insert_timestamp(sources):
    """CD stamps action_ts at disc insert (bypasses route_event).
    Activate CD, then switch away — the CD's old timestamp must not
    let it steal back."""
    stop_all()

    cd_status = get(f"http://localhost:{sources['cd']['port']}/status", timeout=3)
    if not cd_status.get("disc_inserted"):
        raise Exception("No disc in drive")

    router_event("cd")
    time.sleep(4)
    status = router_status()
    assert status["active_source"] == "cd", \
        f"CD should be active: {status['active_source']}"
    cd_ts = status["latest_action_ts"]
    assert cd_ts > 0

    router_event("radio")
    time.sleep(2)
    status = router_status()
    assert status["active_source"] == "radio"
    assert status["latest_action_ts"] > cd_ts
    stop_all()


def test_04_cross_player_type(sources):
    """Switch between local-player source (radio) and remote-player
    source (plex). Cross-player stop logic fires; stale register
    from the old source is rejected."""
    stop_all()

    router_event("radio")
    time.sleep(3)
    status = router_status()
    assert status["active_source"] == "radio"
    assert status["active_player"] == "local"

    router_event("plex")
    time.sleep(3)
    status = router_status()
    assert status["active_source"] == "plex", \
        f"Expected plex: {status['active_source']}"
    assert status["active_player"] == "remote"

    # Stale register from radio
    stale_ts = status["latest_action_ts"] - 10.0
    router_source("radio", "playing", action_ts=stale_ts,
                  name="Radio",
                  command_url=f"http://localhost:{SOURCE_PORTS['radio']}/command",
                  player="local")
    assert router_status()["active_source"] == "plex"
    stop_all()


def test_05_radio_metadata_after_switch(sources):
    """Start radio, let metadata arrive, switch to USB.
    Verify the router's media state is NOT radio's metadata."""
    stop_all()

    router_event("radio")
    time.sleep(5)
    status = router_status()
    if status["active_source"] != "radio":
        raise Exception(f"Radio didn't activate: {status['active_source']}")
    radio_media = status.get("media", {})
    radio_title = radio_media.get("title", "")

    # Switch to USB
    router_event("usb")
    time.sleep(3)
    assert router_status()["active_source"] == "usb"

    # Post a stale media update as if radio's metadata arrived late
    current_ts = router_status()["latest_action_ts"]
    result = router_media("radio", title="Late Radio Metadata",
                          action_ts=current_ts - 5.0)
    assert result.get("dropped") is True, \
        f"Late radio metadata should be dropped: {result}"
    stop_all()


def main():
    print("=" * 55)
    print(" Action Timestamp Tests — Local Player")
    print("=" * 55)

    sources = discover()
    print(f"\n  Available: {', '.join(sorted(sources.keys()))}")

    has_radio = "radio" in sources
    has_usb = "usb" in sources
    has_plex = "plex" in sources
    has_cd = "cd" in sources

    if not has_radio or not has_usb:
        print("  ERROR: Need at least radio + usb")
        sys.exit(2)

    # Verify local player
    status = router_status()
    try:
        ps = get(f"{PLAYER}/player/status")
        if ps.get("player") != "local":
            print(f"  WARNING: Player is {ps.get('player')}, not local")
    except Exception:
        pass

    # Lower volume
    orig_vol = status["volume"]
    if orig_vol > 10:
        set_volume(10)
        print(f"  Volume: {orig_vol}% -> 10% (will restore)")

    print()

    try:
        test("01. Radio plays, switch to USB — poll doesn't steal back",
             lambda: test_01_real_radio_then_switch(sources))

        if has_plex:
            test("02. Rapid radio -> plex (slow vs fast)",
                 lambda: test_02_rapid_radio_to_plex(sources))
        else:
            skip("02. Rapid radio -> plex", "need plex")

        if has_cd:
            test("03. CD disc insert stamps action_ts",
                 lambda: test_03_cd_disc_insert_timestamp(sources))
        else:
            skip("03. CD disc insert", "need cd")

        if has_plex:
            test("04. Cross-player-type switch (local -> remote)",
                 lambda: test_04_cross_player_type(sources))
        else:
            skip("04. Cross-player-type switch", "need plex")

        test("05. Radio metadata dropped after switch to USB",
             lambda: test_05_radio_metadata_after_switch(sources))
    finally:
        stop_all()
        if orig_vol > 10:
            set_volume(orig_vol)
            print(f"\n  Volume restored to {orig_vol}%")

    failed = summary()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
