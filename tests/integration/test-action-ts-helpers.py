"""
Shared helpers for action timestamp integration tests.

Copied to the device alongside the test scripts — not run directly.
"""

import json
import sys
import time
import urllib.request
import urllib.error

ROUTER = "http://localhost:8770"
PLAYER = "http://localhost:8766"
PASS = 0
FAIL = 0
SKIP = 0

SOURCE_PORTS = {
    "cd": 8769, "usb": 8773, "spotify": 8771,
    "plex": 8778, "radio": 8779, "apple_music": 8774,
    "tidal": 8777,
}


# ── HTTP ──

def get(url, timeout=5):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def post(url, data, timeout=10):
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ── Router / player helpers ──

def router_status():
    return get(f"{ROUTER}/router/status")


def router_event(action, device_type="button"):
    return post(f"{ROUTER}/router/event",
                {"type": device_type, "name": action, "action": action})


def router_source(id, state, **kwargs):
    """Register a source state directly with the router."""
    return post(f"{ROUTER}/router/source",
                {"id": id, "state": state, **kwargs})


def player_play(action_ts=None, url=None):
    """Send play command directly to the player, optionally with action_ts."""
    data = {}
    if url:
        data["url"] = url
    if action_ts is not None:
        data["action_ts"] = action_ts
    return post(f"{PLAYER}/player/play", data)


def player_state():
    return get(f"{PLAYER}/player/state")


def router_media(source_id, title="Test", action_ts=None):
    """Post a media update directly to the router."""
    data = {
        "title": title, "artist": "Test Artist", "album": "",
        "state": "playing", "_reason": "test", "_source_id": source_id,
    }
    if action_ts is not None:
        data["_action_ts"] = action_ts
    return post(f"{ROUTER}/router/media", data)


def playback_override(force=False, action_ts=None):
    """Post a playback override to the router."""
    data = {"force": force}
    if action_ts is not None:
        data["action_ts"] = action_ts
    return post(f"{ROUTER}/router/playback_override", data)


def set_volume(vol):
    """Set volume via router."""
    return post(f"{ROUTER}/router/volume", {"volume": vol})


def stop_all():
    """Stop playback and clear active source."""
    try:
        post(f"{PLAYER}/player/stop", {})
    except Exception:
        pass
    for sid in SOURCE_PORTS:
        try:
            router_source(sid, "available")
        except Exception:
            pass
    time.sleep(0.5)


# ── Test framework ──

def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  \033[32mPASS\033[0m  {name}")
        PASS += 1
    except Exception as e:
        print(f"  \033[31mFAIL\033[0m  {name}: {e}")
        FAIL += 1


def skip(name, reason):
    global SKIP
    print(f"  \033[33mSKIP\033[0m  {name}: {reason}")
    SKIP += 1


def discover():
    """Find available sources and their ports."""
    available = {}
    for sid, port in SOURCE_PORTS.items():
        try:
            data = get(f"http://localhost:{port}/status", timeout=2)
            available[sid] = {"port": port, "status": data}
        except Exception:
            pass
    return available


def summary():
    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 55}")
    print(f"  \033[32m{PASS} passed\033[0m", end="")
    if FAIL:
        print(f", \033[31m{FAIL} failed\033[0m", end="")
    if SKIP:
        print(f", \033[33m{SKIP} skipped\033[0m", end="")
    print(f"  ({total} total)")
    print(f"{'=' * 55}")
    return FAIL
