#!/usr/bin/env python3
"""
UI lifecycle integration tests — validates the four UI stability fixes:

  #1 iframe lifecycle: destroy() called before iframe moves on nav-away;
     revive() called on attach so preloaded ArcLists are never left dead
  #5 menu listener delegation: single delegated mouseover handler on #menuItems,
     data-angle datasets populated, re-render doesn't duplicate firings
  #6 nav guard: clicks from detached iframes ignored, clicks from the active
     iframe still fire

Runs against a live kiosk via Chrome DevTools Protocol. Point it at either:
  - a device:  tests/integration/test-ui-lifecycle.py --host beosound5c.local
  - localhost: tests/integration/test-ui-lifecycle.py --host localhost --cdp-port 9222

Device is assumed to run Chromium with --remote-debugging-port=9222.
For localhost testing, start Chromium manually with that flag pointed at the
dev web server.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import websockets
import urllib.request


CHECKS = [
    # id, description, JS expression (returns JSON-serializable), assertion
    (
        "no_nav_guard_attr",
        "Fix #6: _navGuardUntil plumbing removed from view manager",
        '"_navGuardUntil" in window.uiStore.view',
        False,
    ),
    (
        "hover_delegated_flag",
        "Fix #5: MenuManager installed delegated hover (single handler)",
        "window.uiStore.menu._hoverDelegated === true",
        True,
    ),
    (
        "menu_items_have_angle",
        "Fix #5: every .list-item has a numeric data-angle dataset",
        """(() => {
            const items = [...document.querySelectorAll('#menuItems .list-item')];
            return items.length > 0 && items.every(el => !Number.isNaN(parseFloat(el.dataset.angle)));
        })()""",
        True,
    ),
]


ASYNC_CHECKS = [
    # id, description, async JS expression (returns {pass, info})
    (
        "delegated_hover_fires_once_per_item",
        "Fix #5: dispatching mouseover on each item triggers onItemHover exactly once",
        """(async () => {
            const mm = window.uiStore.menu;
            const fired = [];
            const prev = mm.onItemHover;
            mm.onItemHover = (a) => fired.push(a);
            try {
                const items = [...document.querySelectorAll('#menuItems .list-item')];
                for (const el of items) {
                    el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, relatedTarget: document.body }));
                }
                return { pass: fired.length === items.length, info: { items: items.length, fired: fired.length } };
            } finally {
                mm.onItemHover = prev;
            }
        })()""",
    ),
    (
        "detached_iframe_click_ignored",
        "Fix #6: click postMessage from a preloaded (detached) iframe does NOT fire sendClickCommand",
        """(async () => {
            // Start from playing so scenes iframe sits in preload container
            if (window.uiStore.currentRoute !== 'menu/playing') {
                window.uiStore.navigateToView('menu/playing');
                await new Promise(r => setTimeout(r, 400));
            }
            let clicks = 0;
            const orig = window.uiStore.sendClickCommand.bind(window.uiStore);
            window.uiStore.sendClickCommand = () => { clicks++; };
            try {
                const iframe = document.getElementById('preload-scenes');
                if (!iframe) return { pass: false, info: 'no scenes iframe preloaded' };
                // Must post from INSIDE the iframe so event.source is the iframe's window.
                // iframe.contentWindow.eval(...) is same-origin on this kiosk.
                iframe.contentWindow.eval(
                    "window.parent.postMessage({type:'click'}, '*')"
                );
                await new Promise(r => setTimeout(r, 80));
                return { pass: clicks === 0, info: { clicks } };
            } finally {
                window.uiStore.sendClickCommand = orig;
            }
        })()""",
    ),
    (
        "attached_iframe_click_fires",
        "Fix #6: click postMessage from the currently-visible iframe DOES fire sendClickCommand",
        """(async () => {
            window.uiStore.navigateToView('menu/scenes');
            await new Promise(r => setTimeout(r, 600));
            let clicks = 0;
            const orig = window.uiStore.sendClickCommand.bind(window.uiStore);
            window.uiStore.sendClickCommand = () => { clicks++; };
            try {
                const iframe = document.getElementById('preload-scenes');
                iframe.contentWindow.eval(
                    "window.parent.postMessage({type:'click'}, '*')"
                );
                await new Promise(r => setTimeout(r, 80));
                return { pass: clicks === 1, info: { clicks } };
            } finally {
                window.uiStore.sendClickCommand = orig;
                window.uiStore.navigateToView('menu/playing');
            }
        })()""",
    ),
    (
        "scenes_iframe_destroyed_on_leave",
        "Fix #1: scenes ArcList destroy() runs before the iframe moves to preload on nav-away",
        """(async () => {
            window.uiStore.navigateToView('menu/scenes');
            await new Promise(r => setTimeout(r, 500));
            const iframe = document.getElementById('preload-scenes');
            const inst = iframe?.contentWindow?.arcListInstance;
            if (!inst) return { pass: false, info: 'no arcListInstance on scenes iframe' };
            let destroyCalls = 0;
            const origD = inst.destroy.bind(inst);
            inst.destroy = function() { destroyCalls++; return origD(); };
            window.uiStore.navigateToView('menu/playing');
            await new Promise(r => setTimeout(r, 400));
            // After destroy(), the ORIGINAL instance should have cleared state.
            // (Chromium reloads iframe on re-parent, but we still verify clean teardown.)
            return {
                pass: destroyCalls === 1
                    && inst.animationFrame === null
                    && inst._messageHandler === null
                    && inst._saveInterval === null,
                info: {
                    destroyCalls,
                    animationFrame: inst.animationFrame,
                    messageHandler: typeof inst._messageHandler,
                    saveInterval: inst._saveInterval,
                }
            };
        })()""",
    ),
    (
        "scenes_revives_on_reentry",
        "Fix #1: after nav back to scenes, the iframe's ArcList is actively animating",
        """(async () => {
            window.uiStore.navigateToView('menu/playing');
            await new Promise(r => setTimeout(r, 400));
            window.uiStore.navigateToView('menu/scenes');
            await new Promise(r => setTimeout(r, 700));
            const iframe = document.getElementById('preload-scenes');
            const inst = iframe?.contentWindow?.arcListInstance;
            return {
                pass: !!inst && !!inst.animationFrame && !!inst._messageHandler,
                info: {
                    hasInstance: !!inst,
                    animating: !!inst?.animationFrame,
                    hasHandler: !!inst?._messageHandler,
                    parent: iframe?.parentElement?.id,
                }
            };
        })()""",
    ),
]


def get_target_id(host: str, cdp_port: int) -> str:
    with urllib.request.urlopen(f"http://{host}:{cdp_port}/json", timeout=5) as r:
        tabs = json.loads(r.read())
    if not tabs:
        raise RuntimeError(f"No Chromium tabs at {host}:{cdp_port}")
    # Prefer the root page tab
    for t in tabs:
        if t.get("type") == "page":
            return t["id"]
    return tabs[0]["id"]


async def eval_js(ws, expr: str, await_promise: bool = False) -> Any:
    await ws.send(json.dumps({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
        },
    }))
    resp = json.loads(await ws.recv())
    result = resp.get("result", {}).get("result", {})
    if result.get("type") == "object" and "value" in result:
        return result["value"]
    return result.get("value")


async def run(host: str, cdp_port: int) -> int:
    target_id = get_target_id(host, cdp_port)
    uri = f"ws://{host}:{cdp_port}/devtools/page/{target_id}"
    fails = 0
    async with websockets.connect(uri) as ws:
        # Wait briefly for page ready
        for _ in range(20):
            ready = await eval_js(ws, "typeof window.uiStore !== 'undefined' && window.uiStore.menu?._menuLoaded")
            if ready:
                break
            await asyncio.sleep(0.5)
        else:
            print("FAIL: uiStore not initialized within 10s", file=sys.stderr)
            return 1

        for cid, desc, expr, expected in CHECKS:
            got = await eval_js(ws, f"({expr})")
            ok = got == expected
            print(f"[{'PASS' if ok else 'FAIL'}] {cid}: {desc}")
            if not ok:
                print(f"    expected={expected} got={got}")
                fails += 1

        for cid, desc, expr in ASYNC_CHECKS:
            got = await eval_js(ws, expr, await_promise=True)
            ok = isinstance(got, dict) and got.get("pass") is True
            print(f"[{'PASS' if ok else 'FAIL'}] {cid}: {desc}")
            if not ok:
                print(f"    result={got}")
                fails += 1

    print()
    print(f"{'ALL PASSED' if fails == 0 else f'{fails} FAILED'}")
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="beosound5c.local")
    ap.add_argument("--cdp-port", type=int, default=9222)
    args = ap.parse_args()

    # When targeting a remote device, we need to reach its CDP port — tunnel
    # through SSH. For the office device, a simple SSH port forward works:
    #   ssh -L 9222:localhost:9222 kirsten@beosound5c.local -N
    # ...then pass --host localhost. Running locally (e.g. from a dev laptop)
    # is otherwise simpler than wrapping SSH here.
    sys.exit(asyncio.run(run(args.host, args.cdp_port)))


if __name__ == "__main__":
    main()
