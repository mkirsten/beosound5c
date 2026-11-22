"""Pairing a BeoRemote One from SYSTEM > Remotes > Pair New Remote.

The UI sends `start_bt_pairing`; beo-input scans for a BEORC that is
advertising right now and isn't paired yet, then pairs, trusts and connects
it, broadcasting `bt_pairing` progress to every UI client.

See start_bt_pairing() and _run_bt_pairing() in services/input.py.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "services"))

# `hid` is a native USB-HID binding that only exists on the device.
sys.modules.setdefault("hid", type(sys)("hid"))

import input as beo_input  # noqa: E402  (services/input.py)


ADVERTISING = """Device AA:BB:CC:00:00:01 (public)
\tName: BEORC
\tPaired: no
\tConnected: no
\tRSSI: 0xffffffc4 (-60)
"""
CACHED = """Device AA:BB:CC:00:00:01 (public)
\tName: BEORC
\tPaired: no
\tConnected: no
"""
PAIRED = """Device AA:BB:CC:00:00:01 (public)
\tName: BEORC
\tPaired: yes
\tConnected: yes
\tRSSI: 0xffffffc4 (-60)
"""
MAC = "AA:BB:CC:00:00:01"


def test_parse_bt_devices():
    text = "Device AA:BB:CC:00:00:01 BEORC\nDevice AA:BB:CC:DD:EE:FF My Phone\ngarbage\n"
    assert beo_input.parse_bt_devices(text) == {
        "AA:BB:CC:00:00:01": "BEORC",
        "AA:BB:CC:DD:EE:FF": "My Phone",
    }


def test_advertising_unpaired_remote_is_pairable():
    assert beo_input.is_pairable_remote("BEORC", ADVERTISING)


def test_stale_cache_entry_is_not_pairable():
    # No RSSI = not heard during this scan, e.g. not in pairing mode.
    assert not beo_input.is_pairable_remote("BEORC", CACHED)


def test_already_paired_remote_is_not_pairable():
    assert not beo_input.is_pairable_remote("BEORC", PAIRED)


def test_other_devices_are_not_pairable():
    assert not beo_input.is_pairable_remote("My Phone", ADVERTISING)


def _run(found_mac, info_after_pair):
    """Run the pairing flow with bluetoothctl mocked; return (calls, messages)."""
    calls = []

    async def fake_btctl(*args, timeout=5.0):
        calls.append(args)
        return info_after_pair if args[0] == "info" else ""

    sent = []

    async def fake_broadcast(msg):
        sent.append(json.loads(msg))

    with patch.object(beo_input, "_bluetoothctl", side_effect=fake_btctl), \
         patch.object(beo_input, "_find_pairable_remote", AsyncMock(return_value=found_mac)), \
         patch.object(beo_input, "broadcast", side_effect=fake_broadcast):
        asyncio.run(beo_input._run_bt_pairing())
    return calls, sent


def test_pairs_trusts_and_connects_found_remote():
    calls, sent = _run(MAC, PAIRED)
    verbs = [c[2] if c[0] == "--timeout" else c[0] for c in calls]
    assert verbs == ["power", "pair", "info", "trust", "connect"]
    assert all(MAC in c for c in calls[1:])
    assert sent[-1]["status"] == "completed"
    assert all(m["type"] == "bt_pairing" for m in sent)


def test_reports_error_when_no_remote_found():
    calls, sent = _run(None, PAIRED)
    assert not any("pair" in c for c in calls)
    assert sent[-1]["status"] == "error"


def test_reports_error_when_pairing_fails():
    calls, sent = _run(MAC, CACHED)
    assert not any("trust" in c for c in calls)
    assert sent[-1]["status"] == "error"


def test_second_request_while_running_does_not_start_another():
    async def go():
        gate = asyncio.Event()

        async def slow():
            await gate.wait()

        with patch.object(beo_input, "_run_bt_pairing", side_effect=slow) as run:
            first = await beo_input.start_bt_pairing()
            second = await beo_input.start_bt_pairing()
            gate.set()
            await beo_input._bt_pairing_task
            return first, second, run.call_count

    first, second, n = asyncio.run(go())
    assert first["status"] == "started"
    assert second["status"] == "progress"
    assert n == 1
