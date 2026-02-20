#!/usr/bin/env python3
# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

"""
BeoRemote One Bluetooth HID service.

Reads button events from Linux input devices created by BlueZ's HOGP
(HID over GATT Profile) handler.  No gatttool needed.

The remote must already be paired via BlueZ (bluetoothctl pair <MAC>).
After pairing, BlueZ auto-reconnects when the remote wakes from deep sleep
and creates /dev/input/event* devices automatically.

Usage (standalone test — log only, no dispatch):
    sudo python3 bluetooth.py --dry-run

Usage (standalone with transport):
    sudo python3 bluetooth.py

As systemd service:
    Configured in system/beo-bluetooth.service
"""

import asyncio
import fcntl
import logging
import os
import re
import select
import signal
import struct
import subprocess
import sys
import time

import aiohttp

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.config import cfg
from lib.watchdog import watchdog_loop

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bluetooth_hid")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEVICE_NAME = cfg("device", default="BeoSound5c")
BEOREMOTE_MAC = cfg("bluetooth", "remote_mac", default="")
DRY_RUN = "--dry-run" in sys.argv
ROUTER_URL = "http://localhost:8770/router/event"

# ---------------------------------------------------------------------------
# Linux input constants
# ---------------------------------------------------------------------------
EVIOCGRAB = 0x40044590
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 1

# ---------------------------------------------------------------------------
# Button maps: keycode -> action name
# ---------------------------------------------------------------------------

# Consumer Control device
CC_KEYS = {
    103: "up",
    108: "down",
    105: "left",
    106: "right",
    353: "go",
    115: "volup",
    114: "voldown",
    113: "mute",
    172: "home",              # KEY_HOMEPAGE — HOME button
    362: "guide",             # KEY_PROGRAM — GUIDE button
    358: "info",              # KEY_INFO — INFO button
    116: "off",
    403: "chup",
    402: "chdown",
    207: "play",
    164: "play",              # PLAYPAUSE
    119: "pause",
    163: "next",              # NEXTSONG
    208: "forward",           # FASTFORWARD
    165: "prev",              # PREVIOUSSONG
    168: "rewind",            # REWIND
    166: "stop",              # STOPCD
    128: "stop",              # STOP
    158: "back",              # KEY_BACK
}

# Mode-switch keycodes (handled separately)
MODE_KEYS = {274: "tv", 271: "music"}

# Button page device — HID button N -> Linux BTN_(N-1)
BTN_KEYS = {
    256: "red",               # btn1
    257: "green",             # btn2
    258: "yellow",            # btn3
    259: "blue",              # btn4
    260: "0",                 # btn5
    261: "1",
    262: "2",
    263: "3",
    264: "4",
    265: "5",
    266: "6",
    267: "7",
    268: "8",
    269: "9",
    270: "scene_1",           # btn15 — MyButton 1
    271: "music",             # btn16 — MUSIC button (also in MODE_KEYS)
    272: "scene_2",           # btn17 — MyButton 2
    273: "scene_3",           # btn18 — MyButton 3
    274: "tv",                # btn19 — TV button (also in MODE_KEYS)
    275: "scene_4",           # btn20 — MyButton 4
}

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------
REPEAT_ACTIONS = {"volup", "voldown", "chup", "chdown", "up", "down", "left", "right"}
REPEAT_DELAY = 0.4      # seconds before first repeat
REPEAT_INTERVAL = 0.2   # seconds between repeats

HOLD_THRESHOLD = 0.5     # seconds to trigger hold event
DOUBLE_CLICK_WINDOW = 0.3  # max gap between presses for double-click


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------
def find_beorc_devices():
    """Scan /proc/bus/input/devices for BEORC input event devices."""
    devices = []
    try:
        with open("/proc/bus/input/devices") as f:
            content = f.read()
    except OSError:
        return devices

    for block in content.strip().split("\n\n"):
        if "BEORC" not in block:
            continue
        name = ""
        event_path = None
        for line in block.split("\n"):
            if line.startswith("N: Name="):
                name = line.split('"')[1] if '"' in line else ""
            if line.startswith("H: Handlers="):
                for part in line.split("=", 1)[1].split():
                    if part.startswith("event"):
                        event_path = f"/dev/input/{part}"
        if event_path:
            devices.append((name, event_path))
    return devices


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class BluetoothHIDService:

    def __init__(self):
        self.router_session: aiohttp.ClientSession | None = None
        self.led_session: aiohttp.ClientSession | None = None
        self.current_mode = "Video"
        self.event_queue: asyncio.Queue = asyncio.Queue()

        # Software repeat tasks (vol/ch hold)
        self._repeat_tasks: dict[int, asyncio.Task] = {}
        # Hold detection tasks
        self._hold_tasks: dict[int, asyncio.Task] = {}
        # Double-click tracking: keycode -> monotonic release time
        self._last_release: dict[int, float] = {}
        # Track which keys are currently pressed (for hold verification)
        self._pressed_keys: set[int] = set()

        self._running = True
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- Lifecycle ---

    async def start(self):
        if not DRY_RUN:
            self.router_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=2.0),
            )
            self.led_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=0.5),
            )
        logger.info("Service started (%s)",
                     "DRY RUN" if DRY_RUN else f"router: {ROUTER_URL}")

    async def stop(self):
        self._running = False
        for task in self._repeat_tasks.values():
            task.cancel()
        self._repeat_tasks.clear()
        for task in self._hold_tasks.values():
            task.cancel()
        self._hold_tasks.clear()
        if not DRY_RUN:
            if self.router_session:
                await self.router_session.close()
                self.router_session = None
            if self.led_session:
                await self.led_session.close()
                self.led_session = None
        logger.info("Service stopped")

    # --- Input reading (runs in thread) ---

    def _read_loop(self, fd, tag, keymap, stop_flag):
        """Blocking select+read loop — pushes events to the async queue."""
        buf = b""
        while not stop_flag[0]:
            try:
                ready, _, _ = select.select([fd], [], [], 1.0)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                data = os.read(fd, EVENT_SIZE * 8)
            except OSError:
                return
            if not data:
                return

            buf += data
            while len(buf) >= EVENT_SIZE:
                raw = buf[:EVENT_SIZE]
                buf = buf[EVENT_SIZE:]
                _sec, _usec, etype, code, value = struct.unpack(EVENT_FORMAT, raw)

                if etype != EV_KEY:
                    continue
                if value == 2:
                    continue  # kernel auto-repeat — we do our own
                if code not in keymap:
                    if value == 1:
                        logger.warning("Unknown %s keycode: %d", tag, code)
                    continue

                action = keymap[code]
                event = {
                    "tag": tag,
                    "keycode": code,
                    "action": action,
                    "value": value,
                }
                if self._loop:
                    self._loop.call_soon_threadsafe(
                        self.event_queue.put_nowait, event
                    )

    async def read_device(self, name, path):
        """Open device, EVIOCGRAB it, run blocking read in executor."""
        tag = "CC" if "Consumer" in name else "BTN"
        keymap = CC_KEYS if "Consumer" in name else BTN_KEYS

        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError as e:
            logger.error("Cannot open %s: %s", path, e)
            return

        try:
            fcntl.ioctl(fd, EVIOCGRAB, 1)
        except OSError as e:
            logger.warning("EVIOCGRAB failed on %s: %s", path, e)

        logger.info("[%s] Listening on %s (%s)", tag, path, name)

        stop_flag = [False]
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None, self._read_loop, fd, tag, keymap, stop_flag
            )
        finally:
            stop_flag[0] = True
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 0)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
            logger.info("[%s] Device disconnected: %s", tag, path)

    # --- Event processing ---

    async def process_events(self):
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self.event_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            await self.handle_event(event)

    async def handle_event(self, event):
        action = event["action"]
        value = event["value"]
        keycode = event["keycode"]
        is_mode_key = keycode in MODE_KEYS

        # --- Release ---
        if value == 0:
            self._pressed_keys.discard(keycode)

            # Cancel repeat and hold tasks
            task = self._repeat_tasks.pop(keycode, None)
            if task:
                task.cancel()
            task = self._hold_tasks.pop(keycode, None)
            if task:
                task.cancel()

            # Record release time for double-click detection
            if not is_mode_key:
                self._last_release[keycode] = time.monotonic()
            return

        # --- Press (value == 1) ---
        self._pressed_keys.add(keycode)
        now = time.monotonic()

        # Double-click detection
        is_double_click = False
        if keycode in self._last_release:
            if now - self._last_release[keycode] < DOUBLE_CLICK_WINDOW:
                is_double_click = True
            del self._last_release[keycode]

        logger.info(
            "[%s] %s (code=%d, mode=%s%s)",
            event["tag"], action, keycode, self.current_mode,
            ", double" if is_double_click else "",
        )

        # LED pulse on every press
        asyncio.create_task(self._pulse_led())

        # --- Mode switching ---
        if is_mode_key:
            if action == "tv":
                self.current_mode = "Video"
                logger.info("Mode -> Video")
                await self._dispatch("tv", "Video")
            elif action == "music":
                self.current_mode = "Audio"
                logger.info("Mode -> Audio")
                await self._dispatch("music", "Audio")
            return

        # --- Dispatch with current mode ---
        device_type = self.current_mode
        extra = {"event_type": "double_click"} if is_double_click else {}
        await self._dispatch(action, device_type, **extra)

        # --- Software repeat for vol/ch ---
        if action in REPEAT_ACTIONS:
            self._repeat_tasks[keycode] = asyncio.create_task(
                self._software_repeat(keycode, action, device_type)
            )
        # --- Hold detection for non-repeat keys ---
        elif not is_double_click:
            self._hold_tasks[keycode] = asyncio.create_task(
                self._hold_timer(keycode, action, device_type)
            )

    async def _software_repeat(self, keycode, action, device_type):
        """Send repeated events while a vol/ch button is held."""
        try:
            await asyncio.sleep(REPEAT_DELAY)
            while keycode in self._pressed_keys:
                await self._dispatch(action, device_type)
                await asyncio.sleep(REPEAT_INTERVAL)
        except asyncio.CancelledError:
            pass

    async def _hold_timer(self, keycode, action, device_type):
        """Fire a hold event if the key is still pressed after threshold."""
        try:
            await asyncio.sleep(HOLD_THRESHOLD)
            if keycode in self._pressed_keys:
                logger.info("HOLD: %s (%s)", action, device_type)
                await self._dispatch(action, device_type, event_type="hold")
        except asyncio.CancelledError:
            pass

    # --- Dispatch ---

    async def _dispatch(self, action, device_type, **extra):
        payload = {
            "device_name": DEVICE_NAME,
            "source": "bluetooth",
            "action": action,
            "device_type": device_type,
        }
        payload.update(extra)

        et = f" [{extra['event_type']}]" if "event_type" in extra else ""
        logger.info("-> %s (%s)%s", payload["action"], payload["device_type"], et)
        if not DRY_RUN:
            await self._send_to_router(payload)

    async def _send_to_router(self, payload):
        """POST event to the router service."""
        if not self.router_session:
            return
        try:
            async with self.router_session.post(
                ROUTER_URL, json=payload,
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Router returned HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Router unreachable: %s", e)

    async def _pulse_led(self):
        if DRY_RUN or not self.led_session:
            return
        try:
            async with self.led_session.get(
                "http://localhost:8767/led?mode=pulse"
            ):
                pass
        except Exception:
            pass

    # --- BT status (one-shot on connect) ---

    async def _log_bt_status(self):
        """Log RSSI, battery, and device info from bluetoothctl on connect."""
        if not BEOREMOTE_MAC:
            logger.info("BEOREMOTE_MAC not set — skipping BT status")
            return
        mac = BEOREMOTE_MAC.upper()
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "info", mac,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode(errors="replace")

            info = {}
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("RSSI:"):
                    m = re.search(r"(-?\d+)", line)
                    if m:
                        info["RSSI"] = f"{m.group(1)} dBm"
                elif line.startswith("Battery Percentage:"):
                    m = re.search(r"(\d+)", line)
                    if m:
                        info["Battery"] = f"{m.group(1)}%"
                elif line.startswith("Name:"):
                    info["Name"] = line.split(":", 1)[1].strip()
                elif line.startswith("Alias:"):
                    info.setdefault("Name", line.split(":", 1)[1].strip())
                elif line.startswith("ManufacturerData Key:"):
                    info["Manufacturer"] = line.split(":", 1)[1].strip()

            if info:
                logger.info("BT remote: %s",
                            ", ".join(f"{k}: {v}" for k, v in info.items()))
            else:
                logger.info("BT remote: no info available for %s", mac)
        except Exception as e:
            logger.debug("BT status query failed: %s", e)

    # --- Main loop ---

    async def monitor(self):
        self._loop = asyncio.get_event_loop()
        await self.start()
        asyncio.create_task(watchdog_loop())

        logger.info("==========================================")
        logger.info("BeoRemote One HID Service%s", " (DRY RUN)" if DRY_RUN else "")
        logger.info("==========================================")
        logger.info("Device name: %s", DEVICE_NAME)
        if DRY_RUN:
            logger.info("Router:      DISABLED (dry run)")
        else:
            logger.info("Router:      %s", ROUTER_URL)
        logger.info("==========================================")
        logger.info("Remote must be paired via BlueZ.")
        logger.info("Waiting for BEORC input devices...")

        try:
            while self._running:
                # Wait for BEORC devices to appear
                devices = []
                while not devices and self._running:
                    devices = find_beorc_devices()
                    if not devices:
                        await asyncio.sleep(1)

                if not self._running:
                    break

                # Stabilise device count (BlueZ creates them with slight delay)
                prev_count = 0
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    devices = find_beorc_devices()
                    if len(devices) == prev_count and len(devices) > 0:
                        break
                    prev_count = len(devices)

                if not devices:
                    logger.warning("Devices disappeared during stabilisation")
                    await asyncio.sleep(2)
                    continue

                logger.info("Found %d BEORC device(s):", len(devices))
                for name, path in devices:
                    logger.info("  %s  %s", path, name)

                # Drain stale events
                while not self.event_queue.empty():
                    try:
                        self.event_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                # Log BT remote info once on connect
                await self._log_bt_status()

                # Start device readers + event processor
                processor = asyncio.create_task(self.process_events())
                readers = [
                    asyncio.create_task(self.read_device(name, path))
                    for name, path in devices
                ]

                logger.info("=== READY — listening for buttons ===")

                # Wait for any reader to finish (= remote disconnected)
                done, pending = await asyncio.wait(
                    readers, return_when=asyncio.FIRST_COMPLETED
                )

                # Cancel everything
                processor.cancel()
                for t in pending:
                    t.cancel()
                for t in [processor] + list(pending):
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

                # Cancel active repeat/hold tasks
                for task in self._repeat_tasks.values():
                    task.cancel()
                self._repeat_tasks.clear()
                for task in self._hold_tasks.values():
                    task.cancel()
                self._hold_tasks.clear()
                self._pressed_keys.clear()
                self._last_release.clear()

                logger.info("Remote disconnected. Waiting for reconnect...")
                await asyncio.sleep(2)

        finally:
            await self.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    # Check /dev/input access (requires 'input' group membership or root)
    import glob as _glob
    input_devs = _glob.glob('/dev/input/event*')
    if input_devs and not os.access(input_devs[0], os.R_OK):
        print(
            f"No read access to /dev/input devices. "
            f"Ensure user is in the 'input' group: sudo usermod -aG input $USER"
        )
        sys.exit(1)

    loop = asyncio.new_event_loop()
    service = BluetoothHIDService()

    def handle_signal():
        logger.info("Signal received — shutting down")
        service._running = False
        for task in asyncio.all_tasks(loop):
            task.cancel()

    loop.add_signal_handler(signal.SIGINT, handle_signal)
    loop.add_signal_handler(signal.SIGTERM, handle_signal)

    try:
        loop.run_until_complete(service.monitor())
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()
        logger.info("Done.")


if __name__ == "__main__":
    main()
