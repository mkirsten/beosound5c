#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────
# Source & Playback Regression Tests
#
# Tests menu navigation, source views, service health, playback
# commands (play/pause/next/prev/stop), playing view, router, and
# WebSocket connectivity.
#
# Runs ON the BS5c device (copied there by the wrapper script).
#
# Usage (via wrapper):
#   ./tests/integration/test-source-playback.sh
#   HOST=beosound5c-kitchen.local ./tests/integration/test-source-playback.sh
#
# Or directly on device:
#   python3 test-source-playback.py [--json]
#
# Prerequisites:
#   - beo-router + beo-player-* + beo-http + beo-ui running
#   - At least one source with playable content (USB recommended)
# ─────────────────────────────────────────────────────────────────────

import argparse
import asyncio
import json
import re
import sys
import time

try:
    import aiohttp
    import websockets
except ImportError:
    print("ERROR: requires aiohttp and websockets (pip install aiohttp websockets)")
    sys.exit(2)


# ── Globals ──

results = []
test_num = 0
passed = 0
failed = 0
skipped = 0


# ── Helpers ──

def get_cdp_target():
    """Get the first CDP page target ID."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:9222/json", timeout=3) as r:
            targets = json.loads(r.read())
            for t in targets:
                if t.get("type") == "page" and ":8000" in t.get("url", ""):
                    return t["id"]
    except Exception:
        pass
    return None


async def cdp_eval(ws, expr):
    """Evaluate JS expression via an open CDP websocket."""
    import random
    mid = random.randint(100, 99999)
    await ws.send(json.dumps({
        "id": mid, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True}
    }))
    r = json.loads(await ws.recv())
    val = r.get("result", {}).get("result", {}).get("value", "")
    return val if not isinstance(val, dict) else json.dumps(val)


async def http_get(port, path):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://localhost:{port}{path}") as r:
            return await r.text()


async def http_post(port, path, data):
    async with aiohttp.ClientSession() as s:
        async with s.post(f"http://localhost:{port}{path}", json=data) as r:
            return await r.text()


def test(name, result, expect):
    global test_num, passed, failed
    test_num += 1
    ok = bool(re.search(expect, str(result), re.IGNORECASE)) if result else False
    if ok:
        passed += 1
    else:
        failed += 1
    status = "PASS" if ok else "FAIL"
    detail = str(result)[:160]
    results.append({"num": test_num, "name": name, "status": status, "detail": detail})
    mark = "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
    print(f"  {mark} Test {test_num}: {name}" + ("" if ok else f" — {detail}"))


def skip(name, reason):
    global test_num, skipped
    test_num += 1
    skipped += 1
    results.append({"num": test_num, "name": name, "status": "SKIP", "detail": reason})
    print(f"  \033[33m- Test {test_num}: {name} — {reason}\033[0m")


# ── Discover what's available ──

async def discover():
    """Probe services and return capabilities dict."""
    info = {"sources": {}, "player": None, "cdp_target": None, "menu_items": []}

    # Router
    try:
        r = json.loads(await http_get(8770, "/router/status"))
        info["router"] = True
        info["player_type"] = r.get("active_player")
        for sid, sdata in r.get("sources", {}).items():
            info["sources"][sid] = sdata
    except Exception:
        info["router"] = False

    # Player
    try:
        r = json.loads(await http_get(8766, "/player/status"))
        info["player"] = r
    except Exception:
        pass

    # Source details
    source_ports = {"cd": 8769, "spotify": 8771, "usb": 8773, "demo": 8775, "plex": 8778}
    for sid, port in source_ports.items():
        try:
            r = json.loads(await http_get(port, "/status"))
            info["sources"].setdefault(sid, {})["status"] = r
            info["sources"][sid]["port"] = port
        except Exception:
            pass

    # Menu
    try:
        r = json.loads(await http_get(8770, "/router/menu"))
        info["menu_items"] = [i["id"] for i in r.get("items", [])]
    except Exception:
        pass

    # CDP
    info["cdp_target"] = get_cdp_target()

    return info


# ── Find a playable source ──

def pick_playable_source(info):
    """Pick the best source for playback testing. Prefers USB > demo > plex > spotify."""
    for sid in ["usb", "demo", "plex", "spotify"]:
        sdata = info["sources"].get(sid, {})
        status = sdata.get("status", {})
        # Skip sources that need reauth
        if status.get("needs_reauth"):
            continue
        # USB: check it has content
        if sid == "usb" and status.get("available"):
            return sid, sdata.get("port", 8773)
        # Demo: always playable
        if sid == "demo" and sdata.get("port"):
            return sid, sdata["port"]
        # Plex: needs credentials
        if sid == "plex" and status.get("has_credentials") and not status.get("needs_reauth"):
            return sid, sdata.get("port", 8778)
        # Spotify: needs working auth
        if sid == "spotify" and status.get("has_credentials") and not status.get("needs_reauth"):
            return sid, sdata.get("port", 8771)
    return None, None


# ── Test sections ──

async def test_menu_navigation(cdp_ws, menu_items):
    """Tests 1-N: Navigate to each menu item."""
    print("\n── Menu Navigation ──")
    for item in menu_items:
        await cdp_eval(cdp_ws, f'window.uiStore.navigateToView("menu/{item}")')
        await asyncio.sleep(1)
        route = await cdp_eval(cdp_ws, "window.uiStore.currentRoute")
        test(f"Navigate to menu/{item}", route, f"menu/{item}")


async def test_service_health(info):
    """Tests: Service health checks."""
    print("\n── Service Health ──")
    test("Router status", await http_get(8770, "/router/status"), "active_source|volume|sources")

    if info["player"]:
        test("Player status", json.dumps(info["player"]), "player|state")
    else:
        skip("Player status", "player service not reachable")

    for sid in sorted(info["sources"]):
        sdata = info["sources"][sid]
        if "status" in sdata:
            test(f"{sid.upper()} service status", json.dumps(sdata["status"]), "state|source|available")


async def test_source_views(cdp_ws, info):
    """Tests: Navigate to source views (requires active source)."""
    print("\n── Source View Loading ──")
    source_ids = [sid for sid in info["sources"] if sid in info["menu_items"]]
    if not source_ids:
        skip("Source view loading", "no sources in menu")
        return

    for sid in source_ids[:3]:  # test up to 3
        await cdp_eval(cdp_ws, f'window.uiStore.navigateToView("source/{sid}")')
        await asyncio.sleep(2)
        route = await cdp_eval(cdp_ws, "window.uiStore.currentRoute")
        # Source view may redirect to menu/playing if no active source — that's expected
        test(f"Navigate toward source/{sid}", route, f"source/{sid}|menu/playing")


async def test_playback(cdp_ws, source_id, source_port, info):
    """Tests: Full playback lifecycle on the chosen source."""
    print(f"\n── Playback ({source_id.upper()}) ──")

    # Play via action (as router would forward)
    r = await http_post(source_port, "/command", {"action": "play"})
    test(f"{source_id.upper()}: Play via action", r, "ok|toggle|status")

    await asyncio.sleep(4)
    r = await http_get(source_port, "/status")
    d = json.loads(r) if r.startswith("{") else {}
    pb = d.get("playback", d)
    state = pb.get("state", "?")
    test(f"{source_id.upper()}: State=playing", state, "playing")

    # Player service should reflect playing
    r = await http_get(8766, "/player/state")
    test(f"{source_id.upper()}: Player reflects playing", r, "playing")

    # Next
    await http_post(source_port, "/command", {"action": "next"})
    await asyncio.sleep(3)
    r = await http_get(source_port, "/status")
    d = json.loads(r) if r.startswith("{") else {}
    pb = d.get("playback", d)
    test(f"{source_id.upper()}: Next track", f"state={pb.get('state','?')}", "playing")

    # Prev
    await http_post(source_port, "/command", {"action": "prev"})
    await asyncio.sleep(3)
    r = await http_get(source_port, "/status")
    d = json.loads(r) if r.startswith("{") else {}
    pb = d.get("playback", d)
    test(f"{source_id.upper()}: Prev track", f"state={pb.get('state','?')}", "playing")

    # Pause
    await http_post(source_port, "/command", {"action": "pause"})
    await asyncio.sleep(2)
    r = await http_get(source_port, "/status")
    d = json.loads(r) if r.startswith("{") else {}
    pb = d.get("playback", d)
    test(f"{source_id.upper()}: Pause", pb.get("state", "?"), "paused")

    # Resume
    await http_post(source_port, "/command", {"action": "play"})
    await asyncio.sleep(2)
    r = await http_get(source_port, "/status")
    d = json.loads(r) if r.startswith("{") else {}
    pb = d.get("playback", d)
    test(f"{source_id.upper()}: Resume", pb.get("state", "?"), "playing")

    # Playing view
    await cdp_eval(cdp_ws, 'window.uiStore.navigateToView("menu/playing")')
    await asyncio.sleep(2)
    route = await cdp_eval(cdp_ws, "window.uiStore.currentRoute")
    test(f"{source_id.upper()}: PLAYING view accessible", route, "menu/playing")

    # Router shows active source
    r = await http_get(8770, "/router/status")
    d = json.loads(r) if r.startswith("{") else {}
    test(f"{source_id.upper()}: Router active_source", d.get("active_source", "none"), source_id)

    # Stop
    await http_post(source_port, "/command", {"command": "stop"})
    await asyncio.sleep(2)
    r = await http_get(source_port, "/status")
    d = json.loads(r) if r.startswith("{") else {}
    pb = d.get("playback", d)
    test(f"{source_id.upper()}: Stop", pb.get("state", "?"), "stopped")


async def test_command_acceptance(info):
    """Tests: Verify all sources accept commands (even if auth is expired)."""
    print("\n── Command Acceptance ──")
    for sid, sdata in sorted(info["sources"].items()):
        port = sdata.get("port")
        if not port:
            continue
        for cmd in ["play", "stop"]:
            try:
                r = await http_post(port, "/command", {"action": cmd})
                test(f"{sid.upper()}: '{cmd}' action accepted", r, "ok|status|error")
            except Exception as e:
                test(f"{sid.upper()}: '{cmd}' action accepted", str(e), "ok|status")


async def test_router_and_websocket():
    """Tests: Router menu, event handling, WebSocket."""
    print("\n── Router & WebSocket ──")

    r = await http_get(8770, "/router/menu")
    test("Router menu endpoint", r, "items")

    r = await http_post(8770, "/router/event", {"type": "button", "name": "go"})
    test("Router accepts button event", r, ".")

    try:
        async with websockets.connect("ws://localhost:8770/router/ws") as ws:
            test("Router WebSocket connects", "connected", "connected")
    except Exception as e:
        test("Router WebSocket connects", str(e), "connected")

    try:
        async with websockets.connect("ws://localhost:8766/ws") as ws:
            test("Player WebSocket connects", "connected", "connected")
    except Exception as e:
        test("Player WebSocket connects", str(e), "connected")


# ── Main ──

async def main():
    parser = argparse.ArgumentParser(description="BS5c source & playback regression tests")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    print("═══════════════════════════════════════════════════")
    print(" Source & Playback Regression Tests")
    print("═══════════════════════════════════════════════════")

    # Discover device state
    print("\nDiscovering services...")
    info = await discover()

    if not info["router"]:
        print("ERROR: Router not reachable at localhost:8770")
        sys.exit(2)

    sources = list(info["sources"].keys())
    print(f"  Sources: {', '.join(sources) or 'none'}")
    print(f"  Menu:    {', '.join(info['menu_items']) or 'none'}")
    print(f"  Player:  {info['player']['player'] if info['player'] else 'none'}")
    print(f"  CDP:     {'available' if info['cdp_target'] else 'unavailable'}")

    playable_source, playable_port = pick_playable_source(info)
    if playable_source:
        print(f"  Playback test source: {playable_source} (port {playable_port})")
    else:
        print("  Playback test source: none (all sources need reauth or unavailable)")

    # Open CDP connection if available
    cdp_ws = None
    if info["cdp_target"]:
        try:
            cdp_ws = await websockets.connect(
                f"ws://localhost:9222/devtools/page/{info['cdp_target']}")
        except Exception:
            pass

    # Run test sections
    if cdp_ws and info["menu_items"]:
        await test_menu_navigation(cdp_ws, info["menu_items"])
    else:
        skip("Menu navigation", "CDP unavailable or no menu items")

    await test_service_health(info)

    if cdp_ws:
        await test_source_views(cdp_ws, info)
    else:
        skip("Source view loading", "CDP unavailable")

    if playable_source and playable_port:
        if cdp_ws:
            await test_playback(cdp_ws, playable_source, playable_port, info)
        else:
            skip("Playback tests", "CDP unavailable")
    else:
        for name in ["Play", "State=playing", "Player reflects", "Next", "Prev",
                      "Pause", "Resume", "PLAYING view", "Router active", "Stop"]:
            skip(f"Playback: {name}", "no playable source")

    await test_command_acceptance(info)
    await test_router_and_websocket()

    if cdp_ws:
        await cdp_ws.close()

    # Summary
    total = passed + failed + skipped
    print(f"\n═══════════════════════════════════════════════════")
    print(f" Results: \033[32m{passed} passed\033[0m", end="")
    if failed:
        print(f", \033[31m{failed} failed\033[0m", end="")
    if skipped:
        print(f", \033[33m{skipped} skipped\033[0m", end="")
    print(f" ({total} total)")
    print(f"═══════════════════════════════════════════════════")

    if args.json:
        print(json.dumps({
            "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total": total, "passed": passed, "failed": failed, "skipped": skipped,
            "playback_source": playable_source,
            "tests": results,
        }, indent=2))

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
