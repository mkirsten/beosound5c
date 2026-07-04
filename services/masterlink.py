# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).
#
# -----------------------------------------------------------------------------
# This file is substantially a derivative work of libpc2 by Tore Sinding
# Bekkedal (GPL-3.0), https://github.com/toresbe/libpc2.  The following parts
# are ports (logic + boilerplate byte sequences) from libpc2 source:
#
#   PC2Device.init                       ← pc2/pc2device.cpp   PC2Device::init
#   PC2Device.set_address_filter         ← pc2/pc2device.cpp   PC2Device::set_address_filter
#   PC2Device.speaker_power              ← pc2/mixer.cpp       PC2Mixer::speaker_power
#   PC2Device.speaker_mute               ← pc2/mixer.cpp       PC2Mixer::speaker_mute
#   PC2Device.set_volume (0xEB stepping) ← pc2/mixer.cpp       PC2Mixer::adjust_volume
#   PC2Device.set_routing                ← pc2/mixer.cpp       PC2Mixer::send_routing_state
#   PC2Device.set_parameters             ← pc2/mixer.cpp       PC2Mixer::set_parameters
#   PC2Device.send_ml_telegram           ← masterlink/telegram.cpp MasterlinkTelegram::serialize
#
# Role-specific telegram handling (master replies, provider source-burst,
# link discovery) lives in lib/masterlink_{master,provider,link}.py and
# carries its own libpc2 attribution.
#
# Decode tables (_ML_TELEGRAM_TYPES, _ML_PAYLOAD_TYPES, _ML_NODES) are the
# same facts libpc2 publishes in masterlink/telegram.hpp and
# masterlink/masterlink.hpp; most values are also in B&O's MLGW02 spec.
#
# Both projects are GPL-3.0-or-later compatible.  See THIRDPARTY.md for the
# repo-wide summary of third-party code this project builds on.
# -----------------------------------------------------------------------------

import usb.core
import usb.util
import time
import threading
import sys
import json
import logging
import os
import shlex
import aiohttp
import asyncio
from aiohttp import web
from datetime import datetime
from collections import defaultdict

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.background_tasks import BackgroundTaskSet
from lib.config import cfg
from lib.correlation import install_logging
from lib.endpoints import INPUT_LED_PULSE, ROUTER_EVENT
from lib.loop_monitor import LoopMonitor
from lib.masterlink_link import LinkRole
from lib.masterlink_master import MasterRole
from lib.masterlink_provider import (
    PC2_SESSION_AUDIO,
    PC2_SESSION_ML,
    ProviderRole,
)
from lib.watchdog import watchdog_loop

logger = install_logging('beo-masterlink')

# Configuration variables
BEOSOUND_DEVICE_NAME = cfg("device", default="BeoSound5c")
ROUTER_URL = ROUTER_EVENT
MIXER_PORT = int(os.getenv('MIXER_PORT', '8768'))

# Volume — the PC2 0xE3 command sets volume as an absolute byte (0-127).
# 0xEB steps increment/decrement by 1 in the same scale.
# Device echoes actual volume via message types 0x03/0x1D at byte[3] & 0x7F.
VOL_MAX = int(cfg("volume", "max", default=70))
VOL_DEFAULT = int(cfg("volume", "default", default=30))

# Four orthogonal concerns this service owns:
#
#   (1) Decode IR — every Beo4 keycode the PC2 sees on USB (msg type 0x02)
#       is forwarded to beo-router.  Always on, regardless of role.  The
#       only knobs are the per-device-class toggles ml.ir.{audio,video}
#       which gate forwarding by device_type byte.
#
#   (2) Audio master role — BS5c is the audio master on the ML bus.  Replies
#       to MASTER_PRESENT / AUDIO_BUS / GOTO_SOURCE, broadcasts clock,
#       forwards Beo4 transport keys from link devices (BeoLab 2000 /
#       3500 / Passive), and engages distribute when a link is present.
#       Default; standalone collapses into this (no link = no-op).
#       Handler: lib/masterlink_master.MasterRole.  BeoLab 2000 verified
#       on Office 05/2026.
#
#   (3) Provider role — N.MUSIC / N.RADIO source-center for an external
#       audio master (BS9000 / BC2).  Streams audio + metadata when the
#       master activates the source.  Handler: lib/masterlink_provider.
#       ProviderRole.  N.MUSIC BC2-verified 04/2026; N.RADIO unverified.
#
#   (4) Link role — BS5c is a link speaker; some other device is master.
#       Receives sources, decodes track metadata for the UI.  Handler:
#       lib/masterlink_link.LinkRole.
#
# Roles 2/3/4 are mutually exclusive — pick exactly one via masterlink.role.
# IR decoding (1) runs alongside whichever role is selected.  Outstanding
# work tracked in docs/plan-masterlink-roles.md.  cfg() is two-level only;
# nested fields go through dict access on the masterlink block.
_ML_CFG = cfg("masterlink", default={}) or {}
ML_ROLE = (_ML_CFG.get("role") or "master").lower()
_ML_IR = _ML_CFG.get("ir") or {}
ML_IR_AUDIO = bool(_ML_IR.get("audio", True))
ML_IR_VIDEO = bool(_ML_IR.get("video", True))
_ML_PROVIDER = _ML_CFG.get("provider") or {}
ML_PROVIDER_NMUSIC = _ML_PROVIDER.get("nmusic_source", "") or ""
ML_PROVIDER_NRADIO = _ML_PROVIDER.get("nradio_source", "") or ""
ML_LINK_SOURCES = (_ML_CFG.get("link") or {}).get("sources") or []

# Message processing settings
MESSAGE_TIMEOUT = 2.0  # Discard messages older than 2 seconds
DEDUP_COMMANDS = ["volup", "voldown", "left", "right"]  # Commands to deduplicate
WEBHOOK_INTERVAL = 0.2  # Send webhook at least every 0.2 seconds for deduped commands
MAX_QUEUE_SIZE = 10  # Maximum number of messages to keep in queue
sys.stdout.reconfigure(line_buffering=True)

class MessageQueue:
    """Thread-safe queue with lossy behavior and deduplication."""
    def __init__(self, timeout=MESSAGE_TIMEOUT):
        self.lock = threading.Lock()
        self.queue = []
        self.timeout = timeout
        self.command_counts = defaultdict(int)  # For deduplication
        self.last_message_time = {}  # Track the last message time for each command
        self.last_webhook_time = {}  # Track the last webhook time for each command

    def add(self, message):
        """Add a message to the queue with timestamp."""
        with self.lock:
            now = time.time()
            message['timestamp'] = now

            command = message.get('key_name')
            if command in DEDUP_COMMANDS:
                if command in self.last_message_time:
                    if now - self.last_message_time[command] < self.timeout:
                        self.command_counts[command] += 1

                        # Throttle: emit one webhook per WEBHOOK_INTERVAL while a
                        # dedup'd command is being held down.
                        send_webhook_now = False
                        if command not in self.last_webhook_time or (now - self.last_webhook_time[command] >= WEBHOOK_INTERVAL):
                            send_webhook_now = True
                            self.last_webhook_time[command] = now

                        for existing_msg in self.queue:
                            if existing_msg.get('key_name') == command:
                                existing_msg['count'] = self.command_counts[command]
                                existing_msg['timestamp'] = now

                                if send_webhook_now:
                                    webhook_msg = existing_msg.copy()
                                    webhook_msg['force_webhook'] = True
                                    webhook_msg['priority'] = True
                                    self.queue.append(webhook_msg)

                                return

                self.last_message_time[command] = now
                self.last_webhook_time[command] = now
                self.command_counts[command] = 1
                message['count'] = 1

            self.queue.append(message)

            # Bound queue size, keeping priority messages and newest non-priority.
            if len(self.queue) > MAX_QUEUE_SIZE:
                priority_msgs = [msg for msg in self.queue if msg.get('priority', False)]
                non_priority_msgs = [msg for msg in self.queue if not msg.get('priority', False)]
                non_priority_msgs.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                keep_count = max(0, MAX_QUEUE_SIZE - len(priority_msgs))
                self.queue = priority_msgs + non_priority_msgs[:keep_count]

    def get(self):
        """Get the next valid message from the queue."""
        with self.lock:
            now = time.time()
            self.queue = [msg for msg in self.queue if now - msg['timestamp'] < self.timeout]

            if not self.queue:
                return None

            message = self.queue.pop(0)

            # Reset dedup bookkeeping once the last instance of this command drains.
            command = message.get('key_name')
            if command in DEDUP_COMMANDS:
                if all(msg.get('key_name') != command for msg in self.queue):
                    self.command_counts[command] = 0
                    self.last_message_time.pop(command, None)
                    self.last_webhook_time.pop(command, None)

            return message

    def size(self):
        """Return the current size of the queue."""
        with self.lock:
            return len(self.queue)


class PC2Device:
    # B&O PC2 device identifiers
    VENDOR_ID = 0x0cd4
    PRODUCT_ID = 0x0101

    # USB endpoints
    EP_OUT = 0x01  # For sending data to device
    EP_IN = 0x81   # For receiving data from device (LIBUSB_ENDPOINT_IN | 1)

    # Default ML bus identity — overridden in __init__ based on ML_ROLE.
    # master   = 0xC1 AUDIO_MASTER (matches the 0xF6 filter and the PC2's
    #            internal audio-output engagement)
    # provider = 0xC2 SOURCE_CENTER (matches the BC2-tested raw frames in
    #            masterlink_provider.py which embed src=0xC2)
    # link     = 0xC2 (no separate "link speaker" ID is enumerated in the
    #            spec; 0xC2 avoids colliding with the bus master at 0xC1)
    # The PC2's USB-side address filter stays in audio-master mode — it
    # still passes broadcasts and 0xC1-addressed traffic.  Receiving traffic
    # specifically addressed to 0xC2 may need a filter flip; tracked in
    # docs/plan-masterlink-roles.md.
    OUR_NODE_ID = 0xC1  # AUDIO_MASTER (default; instance overrides for non-master roles)

    # Reconnect settings
    RECONNECT_BASE_DELAY = 2.0    # Initial retry delay in seconds
    RECONNECT_MAX_DELAY = 30.0    # Max retry delay
    RECONNECT_BACKOFF = 1.5       # Backoff multiplier

    def __init__(self):
        self.dev = None
        self.running = False
        self.connected = False
        self.message_queue = MessageQueue()
        self.sniffer_thread = None
        self.sender_thread = None
        self.session = None
        self.loop = None
        self._background_tasks = BackgroundTaskSet(logger, label="masterlink")
        self.mixer_state = {
            'speakers_on': False,
            'muted': False,
            'local': False,
            'distribute': False,
            'from_ml': False,
            'volume': 0,           # tracked volume
            'volume_confirmed': 0, # last volume read from device feedback
            # Tone state is *what we asked for*, not read from the PC2.
            # Kept here so /mixer/tone GET can report the last applied
            # values.  Bass/treble/balance are signed ints, loudness bool.
            'bass': 0,
            'treble': 0,
            'balance': 0,
            'loudness': False,
        }
        # Enabled via --ml-sniff; logs every USB packet in full hex.
        self.sniff_mode = False
        self._mixer_runner = None  # aiohttp AppRunner for cleanup
        self._vol_lock = threading.Lock()  # serialize step-based volume changes
        # Serializes USB TX across the three threads that send frames:
        # the sniffer thread (ML role replies), the sender-loop thread
        # (clock broadcasts), and default-executor threads (mixer HTTP).
        # Held per-frame in send_message and across the multi-frame
        # sequences whose ordering libpc2 warns about ("the PC2 crashing
        # very hard if this is fudged").  RLock so sequences can nest
        # (audio_on → set_routing → send_message).
        self._tx_lock = threading.RLock()
        # Track unknown USB message types we've already logged at INFO so
        # recurring background heartbeats (e.g. type 0x01, 0x1C every ~30s
        # on Church) don't flood the journal.
        self._unknown_usb_seen = set()

        # Role wiring.  Each of the three roles is a sibling module under
        # lib/masterlink_*.py with the same shape: __init__(pc2), start(loop),
        # handle_telegram(...).  OUR_NODE_ID gets the role-appropriate value
        # so outbound src bytes don't collide with the bus master at 0xC1.
        self._session_mode = None
        if ML_ROLE == "provider":
            self.OUR_NODE_ID = 0xC2
            self._role = ProviderRole(self)
        elif ML_ROLE == "link":
            self.OUR_NODE_ID = 0xC2
            self._role = LinkRole(self)
        else:  # "master" (default) — anything unknown collapses to master
            self._role = MasterRole(self)

    def open(self):
        """Find and open the PC2 device"""
        self.dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)

        if self.dev is None:
            raise Exception("PC2 not found")

        # Detach kernel driver if active
        if self.dev.is_kernel_driver_active(0):
            self.dev.detach_kernel_driver(0)

        self.dev.set_configuration()

        # Claim interface
        usb.util.claim_interface(self.dev, 0)

        self.connected = True
        logger.info("Opened PC2 device")

    def _release_device(self):
        """Release the USB device handle (best-effort, ignores errors)."""
        self.connected = False
        # TX lock so we don't yank the handle out from under a thread
        # that's mid-send_message.
        with self._tx_lock:
            if self.dev is not None:
                try:
                    usb.util.release_interface(self.dev, 0)
                except Exception:
                    pass
                try:
                    usb.util.dispose_resources(self.dev)
                except Exception:
                    pass
                self.dev = None

    def _reconnect(self):
        """Try to reconnect to the PC2 device with exponential backoff."""
        self._release_device()
        delay = self.RECONNECT_BASE_DELAY

        while self.running:
            logger.info("Attempting to reconnect to PC2 in %.1fs...", delay)
            time.sleep(delay)
            if not self.running:
                return False

            try:
                self.open()
                self.init()
                self.set_address_filter()
                logger.info("Reconnected to PC2 successfully")
                return True
            except Exception as e:
                logger.warning("Reconnect failed: %s", e)
                self._release_device()
                delay = min(delay * self.RECONNECT_BACKOFF, self.RECONNECT_MAX_DELAY)

        return False

    def init(self):
        """Initialize the device with required commands"""
        with self._tx_lock:
            self.send_message([0xf1])
            time.sleep(0.1)
            self.send_message([0x80, 0x01, 0x00])

    def send_message(self, message):
        """Send a message to the device"""
        telegram = [0x60, len(message)] + list(message) + [0x61]
        logger.debug("Sending: %s", " ".join([f"{x:02X}" for x in telegram]))
        with self._tx_lock:
            dev = self.dev
            if dev is None:
                raise RuntimeError("PC2 device not connected")
            dev.write(self.EP_OUT, telegram, 0)

    def set_address_filter(self):
        """Claim the Audio Master identity on the ML bus.

        0xF6 is the PC2's address-filter opcode. libpc2's three modes are
        audio-master / promiscuous / beoport-pc2; only audio-master causes
        the PC2 to answer as 0xC1 and engage its audio output path.  The
        previous 0xFF-wildcard value sniffed traffic but the card never
        behaved as a master, which is why control messages flowed but audio
        didn't.
        Constants from libpc2 set_address_filter() (no code copied)."""
        self.send_message([0xF6, 0x10, 0xC1, 0x80, 0x83, 0x05, 0x00, 0x00])
        logger.info("Address filter set (Audio Master mode)")

    def start_sniffing(self):
        """Start sniffing USB messages and sending them via webhook"""
        self.running = True
        self.loop = asyncio.new_event_loop()

        self.sniffer_thread = threading.Thread(target=self._sniff_loop)
        self.sniffer_thread.daemon = True
        self.sniffer_thread.start()

        self.sender_thread = threading.Thread(target=self._sender_loop_wrapper)
        self.sender_thread.daemon = True
        self.sender_thread.start()

        logger.info("USB message sniffer and sender threads started")

    def _sniff_loop(self):
        """Background thread to continuously read USB messages and add to queue.
        Automatically reconnects if the USB device disconnects.

        USB reads are not frame-aligned — the PC2 endpoint can split a single
        ``0x60 LEN ... 0x61`` frame across multiple reads.  We buffer bytes
        and only dispatch when a complete frame is in hand.  Without this,
        link-device pings from a BeoLab 2000 (~17-byte frames) consistently
        arrive as 7+7+3 chunks and get dropped as ``Short ML telegram``."""
        rx_buffer = bytearray()
        while self.running:
            if not self.connected:
                # Device was lost — try to reconnect
                if not self._reconnect():
                    break  # self.running became False
                rx_buffer.clear()
                continue

            try:
                data = self.dev.read(self.EP_IN, 1024, timeout=500)
                if not data:
                    continue
                rx_buffer.extend(data)

                while True:
                    # Drop bytes before the first STX so a desync recovers.
                    stx = rx_buffer.find(0x60)
                    if stx < 0:
                        rx_buffer.clear()
                        break
                    if stx > 0:
                        del rx_buffer[:stx]
                    if len(rx_buffer) < 2:
                        break  # need LEN byte

                    # Frame layout: 0x60 LEN <LEN bytes> 0x61.  Total = LEN+3.
                    frame_len = rx_buffer[1] + 3
                    if len(rx_buffer) < frame_len:
                        break  # wait for more bytes
                    if rx_buffer[frame_len - 1] != 0x61:
                        # Bad framing — discard this STX and resync.
                        del rx_buffer[0]
                        continue

                    message = list(rx_buffer[:frame_len])
                    del rx_buffer[:frame_len]
                    self._process_usb_frame(message)
            except usb.core.USBTimeoutError:
                pass  # Normal — no data within timeout window

            except usb.core.USBError as e:
                if e.errno == 19:  # ENODEV — device disconnected
                    logger.error("PC2 device disconnected (No such device)")
                    self.connected = False
                    # Loop will trigger reconnect on next iteration
                else:
                    logger.error("USB error: %s", e)
                    time.sleep(0.5)

            except Exception as e:
                logger.error("Error in sniffing thread: %s", e)
                time.sleep(1)

    def _process_usb_frame(self, message):
        """Dispatch one fully-framed USB message (0x60 LEN ... 0x61)."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        msg_type = message[2] if len(message) > 2 else None

        if self.sniff_mode:
            hex_str = " ".join(f"{b:02X}" for b in message)
            logger.info("USB RX [type=0x%02X, len=%d]: %s",
                        msg_type or 0, len(message), hex_str)

        if len(message) >= 5 and msg_type in (0x03, 0x1D):
            vol = message[3] & 0x7F
            self.mixer_state['volume_confirmed'] = vol
            self.mixer_state['volume'] = vol
            logger.debug("Mixer feedback: volume=%d", vol)
        elif msg_type == 0x02:
            msg_data = self.process_beo4_keycode(timestamp, message)
            if msg_data and self._ir_passes_filter(msg_data):
                self.message_queue.add(msg_data)
        elif msg_type == 0x00:
            self._log_ml_telegram(message)
        elif msg_type is not None:
            hex_str = " ".join(f"{b:02X}" for b in message[:32])
            level = logging.DEBUG if msg_type in self._unknown_usb_seen else logging.INFO
            self._unknown_usb_seen.add(msg_type)
            logger.log(level, "Unknown USB message [type=0x%02X]: %s%s",
                       msg_type, hex_str,
                       "…" if len(message) > 32 else "")

    def _sender_loop_wrapper(self):
        """Wrapper to run the async sender loop in its own thread"""
        try:
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._init_session())
            self.loop.run_until_complete(self._start_mixer_http())
            self.loop.create_task(self._load_and_apply_tone())
            self.loop.create_task(watchdog_loop())
            self._role.start(self.loop)
            self.loop.run_until_complete(self._async_sender_loop())
        except Exception as e:
            logger.error("Sender loop failed: %s", e, exc_info=True)

    def _set_session_mode(self, mode):
        """Track which conceptual mode the PC2 mixer is configured for.

        Provider role flips its mode tag to ``masterlink`` before sending
        an ML source-assert burst (which raw-writes 0xE5 routing bytes to
        the PC2), and back to ``audio`` after the burst settles so that any
        later code path knows the local audio routing was disturbed.

        Today this is bookkeeping-only.  We don't re-run set_routing()
        here because the assertion burst writes raw 0xE5 bytes that bypass
        mixer_state, so the cached state isn't reliable.  Restoring local
        audio after a burst — when local listeners are present — needs
        proper hardware testing before we layer it in.  Tracked in
        docs/plan-masterlink-roles.md."""
        if self._session_mode == mode:
            return
        prev = self._session_mode
        self._session_mode = mode
        logger.info("PC2 session mode: %s -> %s", prev or "none", mode)

    async def _init_session(self):
        """Initialize aiohttp session for router and LED pulse."""
        try:
            connector = aiohttp.TCPConnector(
                limit=5,
                keepalive_timeout=60,
                force_close=False,
            )
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=2.0),
            )
            # Runs on the sender-thread's dedicated event loop.
            self._loop_monitor = LoopMonitor().start()
            logger.info("Initialized session (router: %s)", ROUTER_URL)
        except Exception as e:
            logger.error("Failed to initialize session: %s", e, exc_info=True)
            raise

    async def _async_sender_loop(self):
        """Asynchronous background thread to process messages from the queue and send them"""
        while self.running:
            try:
                message = self.message_queue.get()

                if message:
                    tasks = [self._send_webhook_async(message)]
                    await asyncio.gather(*tasks, return_exceptions=True)

                await asyncio.sleep(0.001)

            except Exception as e:
                logger.error("Error in sender loop: %s", e, exc_info=True)
                await asyncio.sleep(0.1)

    async def _send_webhook_async(self, message):
        """Send a message to the router service."""
        # Visual feedback: pulse LED on button press (fire-and-forget).
        # Tracked so exceptions land in the journal instead of vanishing.
        self._background_tasks.spawn(self._pulse_led(), name="pulse_led")

        webhook_data = {
            'device_name': BEOSOUND_DEVICE_NAME,
            'source': 'ir',
            'link': message.get('link', ''),
            'action': message.get('key_name', ''),
            'device_type': message.get('device_type', ''),
            'count': message.get('count', 1),
            'timestamp': datetime.now().isoformat()
        }

        try:
            async with self.session.post(
                ROUTER_URL, json=webhook_data,
                timeout=aiohttp.ClientTimeout(total=1.0),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Router returned HTTP %d", resp.status)
        except Exception as e:
            logger.warning("Router unreachable: %s", e)
        logger.info("Event sent: %s", webhook_data['action'])

    async def _pulse_led(self):
        """Pulse LED for visual feedback (fire-and-forget)"""
        try:
            async with self.session.get(INPUT_LED_PULSE, timeout=aiohttp.ClientTimeout(total=0.5)) as resp:
                pass
        except Exception:
            pass  # Ignore errors - this is just visual feedback

    def _ir_passes_filter(self, msg_data):
        """Apply the masterlink.ir.{audio,video} toggles.

        Beo4 telegrams carry a device-type byte (Audio / Video / Light /
        Vmem / All).  We gate Audio and Video against the user's toggles;
        anything else (Light, All, Vmem, unknown) passes through so we
        don't accidentally swallow alloff or unrelated remote traffic."""
        dt = msg_data.get('device_type', '')
        if dt == 'Audio' and not ML_IR_AUDIO:
            logger.info("IR filter: drop Audio key %s (audio IR disabled)",
                        msg_data.get('key_name'))
            return False
        if dt == 'Video' and not ML_IR_VIDEO:
            logger.info("IR filter: drop Video key %s (video IR disabled)",
                        msg_data.get('key_name'))
            return False
        return True

    def process_beo4_keycode(self, timestamp, data):
        """Process and display a received Beo4 keycode USB message"""
        hex_data = " ".join([f"{x:02X}" for x in data])

        # Beo4 link/source mapping (data[3])
        link_map = {
            0x00: "Beo4",
            0x05: "BeoSound 8",
            0x80: "link",
        }

        # Device type mapping
        device_type_map = {
            0x00: "Video",
            0x01: "Audio",
            0x05: "Vmem",
            0x0F: "All",
            0x1B: "Light"
        }

        # Key mapping (Beo4 IR keycodes)
        # Reference: B&O MLGW protocol + own hardware testing
        key_map = {
            # Digits
            0x00: "0", 0x01: "1", 0x02: "2", 0x03: "3", 0x04: "4",
            0x05: "5", 0x06: "6", 0x07: "7", 0x08: "8", 0x09: "9",
            # Power / standby
            0x0C: "off",
            0x0D: "mute",
            0x0F: "alloff",
            # Source control (arrow keys on non-joystick, joystick in MODE 3)
            0x1E: "up", 0x1F: "down",
            0x32: "left", 0x33: "return", 0x34: "right",
            0x35: "go", 0x36: "stop",
            0x37: "record", 0x38: "shift-stop",
            # 0x53 is the Beo4 PLAY button (distinct from GO at 0x35), and
            # it's also what the BeoLab 2000 panel emits on its own PLAY
            # press.  Mapped to "go" so the router toggles play/pause —
            # matches user expectation of press-once-pause / press-again-
            # resume.  The router action "play" is "resume only" which
            # doesn't toggle, so go is the better fit here.
            0x53: "go",
            # Cursor (joystick in MODE 1)
            0xCA: "cursor_up", 0xCB: "cursor_down",
            0xCC: "cursor_left", 0xCD: "cursor_right",
            0x13: "select",
            # Navigation
            0x7F: "back",
            0x58: "list",
            0x5C: "menu",
            0x20: "track",
            0x40: "guide",
            0x43: "info",
            # Volume
            0x60: "volup", 0x64: "voldown",
            # Sound / picture
            0x2A: "format",
            0x44: "speaker",
            0x46: "sound",
            0xF7: "stand",
            0xDA: "cinema_on", 0xDB: "cinema_off",
            0xAD: "2d", 0xAE: "3d",
            0x1C: "p.mute",
            # Sources — audio
            0x81: "radio",
            0x91: "amem",
            0x92: "cd",
            0x93: "n.radio",
            0x94: "n.music",
            0x95: "server",
            0x96: "spotify",
            0x97: "join",
            # Sources — video
            0x80: "tv",
            0x82: "v.aux",
            0x83: "a.aux",
            0x84: "media",
            0x85: "vmem",
            0x86: "dvd",
            0x87: "camera",
            0x88: "text",
            0x8A: "dtv",
            0x8B: "pc",
            0x8C: "youtube",
            0x8D: "doorcam",
            0x8E: "photo",
            0x90: "usb2",
            0xBF: "av",
            0xFA: "p-in-p",
            # Color keys
            0xD4: "yellow", 0xD5: "green", 0xD8: "blue", 0xD9: "red",
            # Shift combos
            0x17: "shift-cd",
            0x22: "shift-play",
            0x24: "shift-goto",
            0x28: "clock",
            0xC0: "edit",
            0xC1: "random",
            0xC2: "shift-2",
            0xC3: "repeat",
            0xC4: "shift-4",
            0xC5: "shift-5",
            0xC6: "shift-6",
            0xC7: "shift-7",
            0xC8: "shift-8",
            0xC9: "shift-9",
            # Other
            0x0A: "clear",
            0x0B: "store",
            0x0E: "reset",
            0x14: "back2",
            0x15: "mots",
            0x2D: "eject",
            0x3F: "select2",
            0x47: "sleep",
            0x4B: "app",
            0x9B: "light",
            0x9C: "command",
            0xF2: "mots2",
            # Repeat/hold codes
            0x70: "rewind_repeat", 0x71: "wind_repeat",
            0x72: "step_up_repeat", 0x73: "step_down_repeat",
            0x75: "go_repeat",
            0x76: "green_repeat", 0x77: "yellow_repeat",
            0x78: "blue_repeat", 0x79: "red_repeat",
            0x7E: "key_release",
        }

        # Parse link, mode and keycode
        link = data[3]
        mode = data[4]
        keycode = data[6]

        link_name = link_map.get(link, f"Unknown(0x{link:02x})")
        device_type = device_type_map.get(mode, f"Unknown(0x{mode:02x})")
        key_name = key_map.get(keycode, f"Unknown(0x{keycode:02x})")

        logger.info("[%s] [%s] %s -> %s", timestamp, link_name, device_type, key_name)

        if key_name.startswith("Unknown("):
            logger.warning("Unknown keycode: %s | Link: %s | Device: %s | Keycode: 0x%02X",
                           hex_data, link_name, device_type, keycode)

        return {
            'timestamp_str': timestamp,
            'link': link_name,
            'device_type': device_type,
            'key_name': key_name,
            'keycode': f"0x{keycode:02X}",
            'raw_data': hex_data
        }

    # --- MasterLink telegram decoding / transmission ---
    #
    # MasterLink is B&O's multiroom bus.  Two data domains matter here:
    #
    # 1. The *raw ML bus* — differential serial at 19200 baud on pins 1-2
    #    of the 16-pin connector.  The PC2 sniffs this bus and forwards
    #    whole telegrams to the host over USB.  Bus-level semantics
    #    (telegram types 0x0A/0x0B/0x14/0x2C/0x5E, payload types such as
    #    MASTER_PRESENT=0x04 / BEO4_KEY=0x0D / STATUS_INFO=0x87, and the
    #    device-ID addressing at 0xC0/0xC1/0xC2/0x80-0x83/0xF0) are NOT
    #    documented in any B&O publication.  The tables below for these
    #    are compiled from community reverse engineering — principally
    #    the decoder dicts in giachello/mlgw's HA integration and
    #    longstanding BeoWorld forum write-ups.  Treat as "observed from
    #    field captures, not guaranteed complete".
    #
    # 2. The *MLGW integration protocol* (B&O doc "MLGW Protocol
    #    specification, MLGW02, rev 3, 12-Nov-2014") — a completely
    #    different, higher-level protocol spoken between a 3rd-party
    #    controller and the MLGW product over TCP or RS232.  It is NOT
    #    what the PC2 emits.  However, a handful of value tables inside
    #    MLGW02 happen to match what we see in *raw bus* payload bytes
    #    (because the MLGW forwards them through unchanged): source IDs
    #    (§7.2), source activity (§7.5), picture format (§7.6), and the
    #    Beo4 key-code table (§4.5).  Those are treated as authoritative
    #    below and labelled "MLGW02 §x".
    #
    # PC2-specific USB framing — the outer 0x60 LEN … 0x61 and the class
    # byte at [2] — is specific to B&O's USB bridges and not covered by
    # MLGW02.  The BM5 PC2 card (PCB51) shares VID/PID 0CD4/0101 with the
    # standalone Beolink PC2 box but is a different PCB and firmware, so
    # framing specifics are "hypothesis confirmed for keys+volume,
    # unverified elsewhere" until sniffer captures say otherwise.
    #
    # Suspected incoming ML telegram layout (USB frame, message[2] == 0x00):
    #   [0]=0x60  [1]=len  [2]=0x00 (class=ML tgram)
    #   [3]=dest_node  [4]=src_node  [5]=0x01 (SOT)  [6]=telegram_type
    #   [7]=dest_src   [8]=src_src   [9]=0x00 (spare) [10]=payload_type
    #   [11]=payload_size  [12]=payload_version  [13..13+size]=payload
    #   [..]=checksum  [..]=0x00 (EOT)  [last]=0x61

    # --- Raw-bus decode tables (community reverse engineering) ---

    _ML_TELEGRAM_TYPES = {
        0x0A: "COMMAND", 0x0B: "REQUEST", 0x14: "RESPONSE",
        0x2C: "INFO", 0x5E: "CONFIG",
    }

    _ML_PAYLOAD_TYPES = {
        0x04: "MASTER_PRESENT",
        0x06: "DISPLAY_SOURCE",
        0x07: "START_VIDEO_DISTRIBUTION",
        0x08: "AUDIO_BUS",
        0x0B: "EXTENDED_SOURCE_INFORMATION",
        0x0D: "BEO4_KEY",
        0x10: "STANDBY",
        0x11: "RELEASE",
        0x20: "MLGW_REMOTE_BEO4",
        0x30: "REQUEST_LOCAL_SOURCE",
        0x3C: "TIMER",
        0x40: "CLOCK",
        0x44: "TRACK_INFO",
        0x45: "GOTO_SOURCE",
        0x5C: "LOCK_MANAGER_COMMAND",
        0x6C: "DISTRIBUTION_REQUEST",
        0x82: "TRACK_INFO_LONG",
        0x87: "STATUS_INFO",
        0x94: "VIDEO_TRACK_INFO",
        0x96: "PC_PRESENT",
        0x98: "PICT_SOUND_STATUS",
    }

    _ML_NODES = {
        0x80: "ALL",
        0x81: "ALL_AUDIO_LINK_DEVICES",
        0x82: "ALL_VIDEO_LINK_DEVICES",
        0x83: "ALL_LINK_DEVICES",
        0xC0: "VIDEO_MASTER",
        0xC1: "AUDIO_MASTER",
        0xC2: "SOURCE_CENTER",
        0xF0: "MLGW",
    }

    # --- Authoritative tables from MLGW02 spec ---
    # Source IDs that appear in STATUS_INFO (0x87) and GOTO_SOURCE (0x45)
    # payloads.  From MLGW02 §7.2 (Source status telegram payload).
    # Source-id maps live in lib/masterlink_master.py (ML_SOURCE_LABELS,
    # ML_SOURCE_TO_ACTION).  Provider/link reverse-maps live in their own
    # modules.  This file owns only the protocol-level decode tables below.

    # Source activity byte — byte 21 (0-indexed) of a STATUS_INFO payload.
    # From MLGW02 §7.5.
    _ML_SOURCE_ACTIVITY = {
        0x00: "Unknown",
        0x01: "Stop",
        0x02: "Playing",
        0x03: "Wind",
        0x04: "Rewind",
        0x05: "Record lock",
        0x06: "Standby",
        0x07: "No medium",
        0x08: "Still picture",
        0x14: "Scan-play forward",
        0x15: "Scan-play reverse",
        0xFF: "Blank status",
    }

    # Picture format — for video products.  From MLGW02 §7.6.
    _ML_PICTURE_FORMAT = {
        0x00: "Not known",
        0x01: "Known by decoder",
        0x02: "4:3",
        0x03: "16:9",
        0x04: "4:3 Letterbox middle",
        0x05: "4:3 Letterbox top",
        0x06: "4:3 Letterbox bottom",
        0xFF: "Blank picture",
    }

    def _log_ml_telegram(self, msg):
        """Parse and log an incoming ML telegram (message[2] == 0x00),
        then dispatch the audio-master replies we're responsible for."""
        if len(msg) < 14:
            logger.warning("Short ML telegram: %s", " ".join(f"{b:02X}" for b in msg))
            return
        dest_node = msg[3]
        src_node = msg[4]
        ttype = msg[6]
        dest_src = msg[7]
        src_src = msg[8]
        ptype = msg[10]
        psize = msg[11]
        pver = msg[12]
        payload = msg[13:13 + psize]

        src_name = self._ML_NODES.get(src_node, f"0x{src_node:02X}")
        dst_name = self._ML_NODES.get(dest_node, f"0x{dest_node:02X}")
        tname = self._ML_TELEGRAM_TYPES.get(ttype, f"0x{ttype:02X}")
        pname = self._ML_PAYLOAD_TYPES.get(ptype, f"0x{ptype:02X}")

        logger.info("ML RX raw: %s",
                    " ".join(f"{b:02X}" for b in msg))
        logger.info("ML RX %s->%s %s/%s v%d [%d] dst_src=0x%02X src_src=0x%02X payload=%s",
                    src_name, dst_name, tname, pname, pver, psize,
                    dest_src, src_src,
                    " ".join(f"{b:02X}" for b in payload))

        try:
            self._dispatch_ml(ttype, ptype, src_node, dest_node, src_src,
                              payload, pver)
        except Exception as e:
            logger.warning("ML dispatch failed (t=0x%02X p=0x%02X): %s",
                           ttype, ptype, e, exc_info=True)

    def send_ml_telegram(self, dest_node, src_node, telegram_type, payload_type,
                         payload_version, payload, dest_src=0x00, src_src=0x00):
        """Serialize and send a MasterLink telegram on the bus.

        Outer USB frame: 0x60 LEN <data> 0x61 (supplied by send_message).
        <data> begins with 0xE0, which on USB B&O bridges is understood as
        the 'transmit ML telegram' opcode.  The BM5 PC2 card (PCB51) is a
        different board than the standalone Beolink PC2 box, so although
        it shares VID/PID the opcode acceptance set is not guaranteed to
        match 1:1 — call this via /ml/send and confirm with a sniffer."""
        frame = [
            dest_node, src_node, 0x01,         # SOT
            telegram_type & 0xFF,
            dest_src & 0xFF, src_src & 0xFF,
            0x00,                              # spare
            payload_type & 0xFF,
            len(payload) & 0xFF,
            payload_version & 0xFF,
        ]
        frame.extend(payload)
        checksum = sum(frame) & 0xFF
        frame.append(checksum)
        frame.append(0x00)                     # EOT
        usb_frame = [0xE0] + frame
        self.send_message(usb_frame)
        dst_name = self._ML_NODES.get(dest_node, f"0x{dest_node:02X}")
        tname = self._ML_TELEGRAM_TYPES.get(telegram_type, f"0x{telegram_type:02X}")
        pname = self._ML_PAYLOAD_TYPES.get(payload_type, f"0x{payload_type:02X}")
        logger.info("ML TX %s %s/%s v%d [%d] dst_src=0x%02X src_src=0x%02X payload=%s",
                    dst_name, tname, pname, payload_version, len(payload),
                    dest_src, src_src,
                    " ".join(f"{b:02X}" for b in payload))
        logger.info("ML TX raw: %s",
                    " ".join(f"{b:02X}" for b in usb_frame))

    # --- ML dispatch ---
    # All role-specific handling lives in lib/masterlink_{master,provider,
    # link}.py.  This dispatcher just gates on addressing and delegates.

    def _dispatch_ml(self, ttype, ptype, src_node, dest_node, src_src,
                     payload, pver):
        """Filter inbound ML telegrams and hand the role module the ones
        addressed to us.  Drops echoes of our own TX and traffic destined
        for another node."""
        if src_node == self.OUR_NODE_ID:
            logger.debug("ML dispatch: DROP echo (src=us=0x%02X)", src_node)
            return
        addressed_to_us = dest_node in (self.OUR_NODE_ID, 0x80, 0x81, 0x83)
        if not addressed_to_us:
            logger.info("ML dispatch: DROP not-addressed-to-us "
                        "(dest=0x%02X not in {0x%02X,0x80,0x81,0x83}, "
                        "t=0x%02X p=0x%02X)",
                        dest_node, self.OUR_NODE_ID, ttype, ptype)
            return
        logger.info("ML dispatch: ACCEPT from 0x%02X -> 0x%02X t=0x%02X p=0x%02X",
                    src_node, dest_node, ttype, ptype)
        self._role.handle_telegram(ttype, ptype, src_node, dest_node,
                                   src_src, payload)

    # --- Helpers exposed to role modules ---

    def is_powerlink_device(self):
        """True when audio physically passes through the PC2 mixer — the
        only output type where ML distribute does anything audible."""
        return cfg("volume", "type", default="") == "powerlink"

    def node_label(self, node_id):
        """Human-readable label for an ML node id, falling back to hex."""
        return self._ML_NODES.get(node_id, f"0x{node_id:02X}")

    # --- Mixer control (PC2 commands) ---
    # Protocol details derived from libpc2 (GPL-3.0) by Tore Sinding Bekkedal.
    # See https://github.com/toresbe/libpc2

    def speaker_power(self, on):
        """Turn speakers on or off with proper mute sequencing.

        libpc2: "I have observed the PC2 crashing very hard if this is fudged."
        Power on: 0xEA 0xFF then unmute.  Power off: mute then 0xEA 0x00.
        """
        with self._tx_lock:
            if on:
                self.send_message([0xea, 0xFF])
                time.sleep(0.05)
                self.send_message([0xea, 0x81])  # unmute
                self.mixer_state['speakers_on'] = True
                self.mixer_state['muted'] = False
                logger.info("Speakers powered ON")
            else:
                self.send_message([0xea, 0x80])  # mute first
                time.sleep(0.05)
                self.send_message([0xea, 0x00])
                self.mixer_state['speakers_on'] = False
                self.mixer_state['muted'] = True
                logger.info("Speakers powered OFF")

    def speaker_mute(self, muted):
        """Mute or unmute speakers."""
        self.send_message([0xea, 0x80 if muted else 0x81])
        self.mixer_state['muted'] = muted
        logger.info("Speakers %s", "MUTED" if muted else "UNMUTED")

    def set_volume(self, target):
        """Set volume to an absolute value using 0xEB step commands.

        Steps from the device-confirmed volume to the target.
        0xE3 (set_parameters) only works at power-on, so live changes
        must use 0xEB 0x80 (up) / 0xEB 0x81 (down) one step at a time.
        """
        target = max(0, min(VOL_MAX, int(target)))
        with self._vol_lock:
            current = self.mixer_state['volume_confirmed']
            diff = target - current
            if diff == 0:
                return
            direction = [0xeb, 0x80] if diff > 0 else [0xeb, 0x81]
            for _ in range(abs(diff)):
                self.send_message(direction)
                time.sleep(0.02)
            # Update both tracked and confirmed so queued requests don't
            # re-step from a stale baseline (USB feedback may lag).
            self.mixer_state['volume'] = target
            self.mixer_state['volume_confirmed'] = target
        logger.info("Volume set to %d (%d steps from confirmed %d)",
                     target, abs(diff), current)

    def set_routing(self, local=False, distribute=False, from_ml=False):
        """Set audio routing per libpc2 logic. All False = audio off."""
        muted_byte = 0x00 if (distribute or local) else 0x01
        dist_byte = 0x01 if distribute else 0x00

        if local and from_ml:
            locally = 0x03
        elif from_ml:
            locally = 0x04
        elif local:
            locally = 0x01
        else:
            locally = 0x00

        with self._tx_lock:
            self.send_message([0xe7, muted_byte])
            time.sleep(0.02)
            self.send_message([0xe5, locally, dist_byte, 0x00, muted_byte])

        self.mixer_state['local'] = local
        self.mixer_state['distribute'] = distribute
        self.mixer_state['from_ml'] = from_ml
        logger.info("Routing: local=%s distribute=%s from_ml=%s", local, distribute, from_ml)

    def activate_source(self):
        """Tell PC2 to connect local audio source to the PowerLink bus."""
        self.send_message([0xe4, 0x01])
        logger.info("Audio source activated")

    def set_parameters(self, volume, bass=0, treble=0, balance=0, loudness=False):
        """Set mixer parameters via 0xE3. Only effective at power-on."""
        volume = max(0, min(VOL_MAX, volume))
        vol_byte = volume | (0x80 if loudness else 0x00)
        self.send_message([0xe3, vol_byte, bass & 0xFF, treble & 0xFF, balance & 0xFF])
        self.mixer_state['volume'] = volume
        self.mixer_state['volume_confirmed'] = volume
        logger.info("Parameters: vol=%d bass=%d treble=%d bal=%d loud=%s",
                     volume, bass, treble, balance, loudness)

    def audio_on(self, volume=None):
        """Power on speakers: source → route → power → set_parameters.

        0xE3 sets initial volume directly at power-on (no stepping needed).
        """
        if volume is None:
            volume = VOL_DEFAULT
        volume = max(0, min(VOL_MAX, volume))

        with self._tx_lock:
            self.activate_source()
            time.sleep(0.1)
            # Ask the role whether to also drive audio onto the ML bus.  Master
            # role says yes when a link device has been seen; provider/link say
            # no (their audio paths are handled separately).
            wants = getattr(self._role, 'wants_distribute', None)
            distribute = bool(wants and wants())
            self.set_routing(local=True, distribute=distribute)
            time.sleep(0.1)
            self.speaker_power(True)
            time.sleep(0.05)
            self.set_parameters(volume)
            time.sleep(0.1)
        logger.info("Audio ON at volume %d", volume)

    def audio_off(self):
        """Power off: route off → power off."""
        with self._tx_lock:
            self.set_routing(local=False, distribute=False, from_ml=False)
            time.sleep(0.05)
            self.speaker_power(False)
        self.mixer_state['volume'] = 0
        logger.info("Audio OFF")

    # --- Mixer HTTP API (port 8768) ---

    async def _handle_mixer_volume(self, request):
        """POST /mixer/volume  {"volume": 0-70}"""
        data = await request.json()
        vol = int(data.get('volume', 0))
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.set_volume, vol)
        return web.json_response({
            'ok': True,
            'volume': self.mixer_state['volume'],
            'volume_confirmed': self.mixer_state['volume_confirmed'],
        })

    async def _handle_mixer_power(self, request):
        """POST /mixer/power  {"on": true/false, "volume": optional}"""
        data = await request.json()
        on = data.get('on', False)
        loop = asyncio.get_running_loop()
        if on:
            vol = data.get('volume', None)
            await loop.run_in_executor(None, self.audio_on, vol)
        else:
            await loop.run_in_executor(None, self.audio_off)
        return web.json_response({'ok': True, 'speakers_on': on})

    async def _handle_mixer_mute(self, request):
        """POST /mixer/mute  {"muted": true/false}"""
        data = await request.json()
        muted = data.get('muted', False)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.speaker_mute, muted)
        return web.json_response({'ok': True, 'muted': muted})

    async def _handle_mixer_status(self, request):
        """GET /mixer/status"""
        state = dict(self.mixer_state)
        state['volume_pct'] = state['volume']  # volume is already absolute
        state['connected'] = self.connected
        return web.json_response(state)

    async def _handle_mixer_distribute(self, request):
        """POST /mixer/distribute  {"on": true/false}
        Flips the PC2's routing to send local audio onto the MasterLink bus
        (or stop). Does NOT transmit source-announcement telegrams — link
        rooms won't auto-tune unless something else on ML advertises us."""
        data = await request.json()
        on = bool(data.get('on', False))
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self.set_routing,
            self.mixer_state['local'], on, self.mixer_state['from_ml'])
        return web.json_response({'ok': True, 'distribute': on})

    async def _handle_mixer_tone(self, request):
        """GET /mixer/tone               – read current tone state
        POST /mixer/tone  body: {bass?, treble?, balance?, loudness?}

        Applied via a PipeWire filter-chain named ``beo_tone_sink``
        (see install/configs/53-beosound5c-tone.conf).  The PC2's TDA7409
        ignores 0xE3 mid-session over USB, so the DSP path is the only
        one that takes effect.  We still push 0xE3 on a best-effort
        basis for any hardware that honours it."""
        if request.method == 'GET':
            return web.json_response({
                'bass': self.mixer_state['bass'],
                'treble': self.mixer_state['treble'],
                'balance': self.mixer_state['balance'],
                'loudness': self.mixer_state['loudness'],
            })

        data = await request.json()
        applied = {}
        for key in ('bass', 'treble', 'balance'):
            if key in data:
                val = max(-10, min(10, int(data[key])))
                self.mixer_state[key] = val
                applied[key] = val
                await self._apply_pw_tone(key, val)
        if 'loudness' in data:
            val = bool(data['loudness'])
            self.mixer_state['loudness'] = val
            applied['loudness'] = val
            await self._apply_pw_tone('loudness', val)
        if applied:
            self._schedule_tone_save()

        # Best-effort: also push to PC2 via 0xE3 with the current volume.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._push_e3)

        return web.json_response({'ok': True, 'applied': applied,
                                  'state': {
                                      'bass': self.mixer_state['bass'],
                                      'treble': self.mixer_state['treble'],
                                      'balance': self.mixer_state['balance'],
                                      'loudness': self.mixer_state['loudness'],
                                  }})

    def _push_e3(self):
        """Re-send 0xE3 with current cached mixer values.  Best-effort;
        the PC2 is suspected to ignore 0xE3 after power-on."""
        try:
            self.set_parameters(
                self.mixer_state['volume_confirmed'] or self.mixer_state['volume'],
                bass=self.mixer_state['bass'],
                treble=self.mixer_state['treble'],
                balance=self.mixer_state['balance'],
                loudness=self.mixer_state['loudness'],
            )
        except Exception as e:
            logger.debug("0xE3 push failed (expected if PC2 ignores runtime): %s", e)

    # ── PipeWire filter-chain tone control ─────────────────────────────
    #
    # Bass / treble / loudness live as biquad shelf filters inside the
    # ``beo_tone_sink`` virtual sink installed by
    # ``install/configs/53-beosound5c-tone.conf``.  Balance is a pair of
    # per-channel volumes on the same node.
    #
    # Runtime control is over ``pw-cli s <node-id> Props '{...}'``.  The
    # node id changes across pipewire restarts, so we resolve it by name
    # with a short cache.

    PW_TONE_NODE = "beo_tone_sink"
    # Fixed loudness curve when the switch is on.  Low shelf boost at
    # 100 Hz (+6 dB) + high shelf at 10 kHz (+3 dB).  Independent of
    # current volume — a simple "smile" tilt the user can toggle.
    PW_LOUD_BASS_DB = 6.0
    PW_LOUD_TREBLE_DB = 3.0

    def _pw_env(self):
        """Environment for pw-cli subprocesses — the masterlink service
        runs as user ``kirsten`` but outside kirsten's login session, so
        we need ``XDG_RUNTIME_DIR`` set to reach the pipewire socket."""
        env = os.environ.copy()
        env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
        return env

    async def _find_pw_node(self, name):
        """Resolve a pipewire node id by ``node.name``.  Short cache —
        cleared on failure so a restart is picked up on next change."""
        cached = getattr(self, '_pw_node_cache', {}).get(name)
        if cached is not None:
            return cached
        try:
            proc = await asyncio.create_subprocess_exec(
                'pw-dump',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._pw_env(),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            data = json.loads(out)
        except Exception as e:
            logger.warning("pw-dump failed: %s", e)
            return None
        for obj in data:
            info = obj.get('info') or {}
            props = info.get('props') or {}
            if props.get('node.name') == name:
                node_id = obj.get('id')
                self._pw_node_cache = {name: node_id}
                return node_id
        return None

    async def _pw_set_props(self, spec):
        """Run ``pw-cli s <id> Props '{<spec>}'`` against the tone node.
        Re-resolves node id once on failure in case pipewire restarted."""
        for attempt in (1, 2):
            node_id = await self._find_pw_node(self.PW_TONE_NODE)
            if node_id is None:
                logger.warning("PipeWire filter-chain '%s' not found",
                               self.PW_TONE_NODE)
                return False
            cmd = ['pw-cli', 's', str(node_id), 'Props', '{ ' + spec + ' }']
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._pw_env(),
                )
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=2.0)
                if proc.returncode == 0:
                    return True
                logger.warning("pw-cli rc=%d: %s | %s",
                               proc.returncode, ' '.join(cmd),
                               stderr.decode(errors='replace').strip())
            except Exception as e:
                logger.warning("pw-cli exception (attempt %d): %s", attempt, e)
            # First attempt failed — clear the cached id and retry once.
            self._pw_node_cache = {}
        return False

    async def _apply_pw_tone(self, kind, value):
        """Map a tone axis to a PipeWire Props update.  The filter-chain
        has separate FL/FR nodes (see install/configs/53-beosound5c-tone.conf)
        so we drive both in one call.

          bass/treble           (-10..+10 → same dB via shelf Gain)
          balance               (-10..+10 → per-channel mixer gain,
                                 quieter side scales 1 → 0)
          loudness              (bool → fixed shelf pair on/off)
        """
        if kind in ('bass', 'treble'):
            g = float(value)
            return await self._pw_set_props(
                f'params = [ "{kind}_FL:Gain" {g:.2f} '
                f'"{kind}_FR:Gain" {g:.2f} ]')
        if kind == 'loudness':
            lb = self.PW_LOUD_BASS_DB if value else 0.0
            lt = self.PW_LOUD_TREBLE_DB if value else 0.0
            return await self._pw_set_props(
                f'params = [ "loud_bass_FL:Gain" {lb:.2f} '
                f'"loud_bass_FR:Gain" {lb:.2f} '
                f'"loud_treble_FL:Gain" {lt:.2f} '
                f'"loud_treble_FR:Gain" {lt:.2f} ]')
        if kind == 'balance':
            b = max(-10, min(10, int(value)))
            if b <= 0:
                fl, fr = 1.0, 1.0 + b / 10.0
            else:
                fl, fr = 1.0 - b / 10.0, 1.0
            return await self._pw_set_props(
                f'params = [ "bal_FL:Gain 1" {fl:.3f} '
                f'"bal_FR:Gain 1" {fr:.3f} ]')
        logger.warning("Unknown tone axis: %s", kind)
        return False

    # ── Tone persistence to config.json ───────────────────────────────
    # Debounced — repeated slider drags only trigger one write after the
    # user stops moving.  Atomic replace so a concurrent write from
    # beo-input's /config handler can't corrupt the file.

    _TONE_SAVE_DEBOUNCE = 2.0

    def _schedule_tone_save(self):
        existing = getattr(self, '_tone_save_task', None)
        if existing and not existing.done():
            existing.cancel()
        if self.loop:
            self._tone_save_task = asyncio.run_coroutine_threadsafe(
                self._delayed_tone_save(), self.loop)

    async def _delayed_tone_save(self):
        try:
            await asyncio.sleep(self._TONE_SAVE_DEBOUNCE)
            await self._save_tone_to_config()
        except asyncio.CancelledError:
            pass

    async def _save_tone_to_config(self):
        from lib.config import _SEARCH_PATHS
        path = next((p for p in _SEARCH_PATHS if os.path.exists(p)), None)
        if not path:
            logger.warning("No config.json found; tone not persisted")
            return
        snapshot = {
            'bass': int(self.mixer_state['bass']),
            'treble': int(self.mixer_state['treble']),
            'balance': int(self.mixer_state['balance']),
            'loudness': bool(self.mixer_state['loudness']),
        }

        def _do_save():
            with open(path) as f:
                data = json.load(f)
            data.setdefault('volume', {})['tone'] = snapshot
            tmp = path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_save)
            logger.info("Persisted tone: %s", snapshot)
        except PermissionError as e:
            logger.warning("Tone persist permission denied (%s): %s",
                           path, e)
        except Exception as e:
            logger.warning("Tone persist failed: %s", e)

    async def _load_and_apply_tone(self):
        """Read saved tone values from config and push them to the
        filter-chain.  Called once at startup after the mixer HTTP API
        is up."""
        tone = cfg('volume', 'tone', default={}) or {}
        for key in ('bass', 'treble', 'balance'):
            val = int(tone.get(key, 0))
            self.mixer_state[key] = max(-10, min(10, val))
        self.mixer_state['loudness'] = bool(tone.get('loudness', False))
        for key in ('bass', 'treble', 'balance', 'loudness'):
            await self._apply_pw_tone(key, self.mixer_state[key])
        logger.info("Loaded tone from config: bass=%d treble=%d bal=%d loud=%s",
                    self.mixer_state['bass'], self.mixer_state['treble'],
                    self.mixer_state['balance'], self.mixer_state['loudness'])

    async def _handle_link_source(self, request):
        """POST /link/source — link role only: ask the discovered master
        to switch to a source.

        Body: {"source": "n.music"} (router-style action name) or
              {"source_byte": 0x7A} (raw ML source byte).

        Returns 409 when role != "link", 503 when the master hasn't been
        discovered yet, 400 on bad input.
        """
        if not isinstance(self._role, LinkRole):
            return web.json_response(
                {'ok': False, 'error': 'role is not "link"'}, status=409)
        try:
            data = await request.json()
        except Exception as e:
            return web.json_response(
                {'ok': False, 'error': f'bad JSON: {e}'}, status=400)

        source_byte = data.get('source_byte')
        if source_byte is None:
            name = (data.get('source') or '').strip().lower()
            if not name:
                return web.json_response(
                    {'ok': False, 'error': 'missing "source" or "source_byte"'},
                    status=400)
            # Reverse the action→byte map.  Single source byte per action
            # (the multi-mapping in _ML_SOURCE_TO_ACTION is byte→action;
            # here we want action→byte and the duplicates collapse to the
            # canonical byte: amem→0x79, dvd→0x29).
            reverse = {
                "tv": 0x0B, "vmem": 0x15, "dvd": 0x29, "dtv": 0x1F,
                "v.aux": 0x33, "v.aux2": 0x3E, "pc": 0x47,
                "radio": 0x6F, "amem": 0x79, "n.music": 0x7A,
                "cd": 0x8D, "a.aux": 0x97, "n.radio": 0xA1,
            }
            source_byte = reverse.get(name)
            if source_byte is None:
                return web.json_response(
                    {'ok': False, 'error': f'unknown source name {name!r}'},
                    status=400)
        try:
            source_byte = int(source_byte) & 0xFF
        except (TypeError, ValueError):
            return web.json_response(
                {'ok': False, 'error': '"source_byte" must be an int'},
                status=400)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, self._role.request_source, source_byte)
        except RuntimeError as e:
            return web.json_response(
                {'ok': False, 'error': str(e)}, status=503)
        return web.json_response(
            {'ok': True, 'source_byte': source_byte,
             'master_node_id': self._role.master_node_id})

    async def _handle_ml_standby(self, request):
        """POST /ml/standby — broadcast STANDBY to all link devices.

        Used for "all standby": long-press on the BS5c power button and
        the Beo4 ALL-off key both route here via the router.  Telegram
        shape (COMMAND/STANDBY, empty payload, broadcast to
        ALL_LINK_DEVICES) follows community captures — like /ml/send,
        confirm against a sniffer when validating on new hardware.
        """
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self.send_ml_telegram,
                0x83,                 # ALL_LINK_DEVICES
                self.OUR_NODE_ID,
                0x0A,                 # COMMAND
                0x10,                 # STANDBY
                1,
                [],
            )
            logger.info("ML all-standby broadcast sent")
            return web.json_response({'ok': True})
        except Exception as e:
            logger.error("ML all-standby TX failed: %s", e)
            return web.json_response({'ok': False, 'error': str(e)}, status=500)

    async def _handle_ml_send(self, request):
        """POST /ml/send — raw ML telegram TX for experimentation.

        Body: {
          "dest_node": 0x80, "src_node": 0xC2,
          "telegram_type": 0x0A,   (0x0A=COMMAND 0x0B=REQUEST 0x14=STATUS
                                    0x2C=INFO 0x5E=CONFIG)
          "payload_type": 0x04,    (0x04=MASTER_PRESENT 0x44=TRACK_INFO
                                    0x87=STATUS_INFO 0x45=GOTO_SOURCE ...)
          "payload_version": 1,
          "payload": [0x01, 0x01, 0x01],
          "dest_src": 0x00, "src_src": 0x00
        }
        """
        data = await request.json()
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self.send_ml_telegram,
                int(data['dest_node']),
                int(data['src_node']),
                int(data['telegram_type']),
                int(data['payload_type']),
                int(data.get('payload_version', 1)),
                [int(b) for b in data.get('payload', [])],
                int(data.get('dest_src', 0)),
                int(data.get('src_src', 0)),
            )
            return web.json_response({'ok': True})
        except (KeyError, ValueError) as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)

    async def _start_mixer_http(self):
        """Start the mixer HTTP API server (non-blocking)."""
        @web.middleware
        async def cors_middleware(request, handler):
            if request.method == "OPTIONS":
                return web.Response(headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"})
            resp = await handler(request)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp

        app = web.Application(middlewares=[cors_middleware])
        app.router.add_post('/mixer/volume', self._handle_mixer_volume)
        app.router.add_post('/mixer/power', self._handle_mixer_power)
        app.router.add_post('/mixer/mute', self._handle_mixer_mute)
        app.router.add_get('/mixer/status', self._handle_mixer_status)
        app.router.add_post('/mixer/distribute', self._handle_mixer_distribute)
        app.router.add_get('/mixer/tone', self._handle_mixer_tone)
        app.router.add_post('/mixer/tone', self._handle_mixer_tone)
        app.router.add_post('/ml/send', self._handle_ml_send)
        app.router.add_post('/ml/standby', self._handle_ml_standby)
        app.router.add_post('/link/source', self._handle_link_source)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', MIXER_PORT)
        await site.start()
        self._mixer_runner = runner
        logger.info("Mixer HTTP API listening on port %d", MIXER_PORT)

    def stop_sniffing(self):
        """Stop the USB sniffer"""
        self.running = False

        # Clean up mixer HTTP server
        if self.loop and self._mixer_runner:
            asyncio.run_coroutine_threadsafe(self._mixer_runner.cleanup(), self.loop)

        # Cancel any pending LED pulse tasks.  run_coroutine_threadsafe
        # returns a concurrent.futures.Future; we don't wait on it since
        # shutdown is already racing the loop thread.
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self._background_tasks.cancel_all(), self.loop)

        if self.loop and self.session:
            asyncio.run_coroutine_threadsafe(self.session.close(), self.loop)

        if self.sniffer_thread:
            self.sniffer_thread.join(timeout=1.0)
        if self.sender_thread:
            self.sender_thread.join(timeout=1.0)

    def close(self):
        """Close the device"""
        if self.running:
            self.stop_sniffing()

        if self.dev:
            try:
                self.send_message([0xa7])
            except Exception:
                pass
            self._release_device()
            logger.info("Device closed")


if __name__ == "__main__":
    audio_test = '--audio-test' in sys.argv
    ml_sniff = '--ml-sniff' in sys.argv

    # Notify systemd early so Type=notify doesn't fail if USB device is missing
    from lib.watchdog import sd_notify
    sd_notify("READY=1")

    try:
        pc2 = PC2Device()
        pc2.sniff_mode = ml_sniff
        # PC2 dongle is optional — devices without it (e.g. Sonos-only setups
        # like Church) still need masterlink running for the mixer HTTP API
        # (tone controls). If open() fails we skip init/filter, but still
        # start_sniffing() so the sender thread boots the mixer HTTP and the
        # sniffer thread enters its reconnect loop in case a PC2 appears later.
        try:
            pc2.open()
            pc2_ready = True
        except Exception as e:
            logger.warning("PC2 open failed: %s — running tone API only; "
                           "sniffer will retry in background", e)
            pc2_ready = False

        pc2.start_sniffing()

        logger.info("Master Link config: role=%s ir_audio=%s ir_video=%s "
                    "provider.nmusic=%r provider.nradio=%r link.sources=%s",
                    ML_ROLE, ML_IR_AUDIO, ML_IR_VIDEO,
                    ML_PROVIDER_NMUSIC, ML_PROVIDER_NRADIO, ML_LINK_SOURCES)

        if pc2_ready:
            logger.info("Starting device initialization")
            pc2.init()

            logger.info("Setting address filter")
            pc2.set_address_filter()
            if ml_sniff:
                logger.info("ML sniffer ON — every USB packet will be logged in full hex.")

        if audio_test:
            logger.info("Audio test mode. Commands: on [vol], off, vol <n>, vol+ [n], vol- [n], mute, unmute, status, quit")
            while True:
                try:
                    line = input("> ").strip().lower()
                except EOFError:
                    break
                if not line:
                    continue
                parts = line.split()
                cmd = parts[0]

                if cmd == 'quit':
                    break
                elif cmd == 'on':
                    vol = int(parts[1]) if len(parts) > 1 else None
                    pc2.audio_on(vol)
                elif cmd == 'off':
                    pc2.audio_off()
                elif cmd == 'vol' and len(parts) > 1:
                    pc2.set_volume(int(parts[1]))
                elif cmd == 'vol+':
                    n = int(parts[1]) if len(parts) > 1 else 1
                    pc2.set_volume(pc2.mixer_state['volume'] + n)
                elif cmd == 'vol-':
                    n = int(parts[1]) if len(parts) > 1 else 1
                    pc2.set_volume(pc2.mixer_state['volume'] - n)
                elif cmd == 'mute':
                    pc2.speaker_mute(True)
                elif cmd == 'unmute':
                    pc2.speaker_mute(False)
                elif cmd == 'status':
                    print(pc2.mixer_state)
                else:
                    print(f"Unknown command: {cmd}")
        else:
            logger.info("Device initialized. Sniffing USB messages... (Ctrl+C to exit)")
            while True:
                time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Exiting...")
    except Exception as e:
        logger.error("Error: %s", e)
    finally:
        if 'pc2' in locals():
            if audio_test and pc2.mixer_state['speakers_on']:
                logger.info("Cleaning up: powering off speakers")
                pc2.audio_off()
            pc2.close()
        logger.info("Exiting sniffer")
