# BeoSound 5c
# Copyright (C) 2024-2026 Markus Kirsten
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Attribution required — see LICENSE, Section 7(b).

import usb.core
import usb.util
import time
import threading
import sys
import json
import os
import aiohttp
import asyncio
import logging
from aiohttp import web
from datetime import datetime
from collections import defaultdict

# Ensure services/ is on the path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import cfg
from lib.watchdog import watchdog_loop

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('beo-masterlink')

# Configuration variables
BEOSOUND_DEVICE_NAME = cfg("device", default="BeoSound5c")
ROUTER_URL = "http://localhost:8770/router/event"
MIXER_PORT = int(os.getenv('MIXER_PORT', '8768'))

# Hardware volume range
RAW_VOL_MAX = 90  # safe max out of 0-127


def _pct_to_raw(pct: float) -> int:
    """Map 0-100% to 0-RAW_VOL_MAX."""
    return max(0, min(RAW_VOL_MAX, int(pct * RAW_VOL_MAX / 100)))


def _raw_to_pct(raw: int) -> float:
    """Map 0-RAW_VOL_MAX back to 0-100%."""
    return round(raw * 100 / RAW_VOL_MAX, 1)

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
            # Add timestamp to the message
            now = time.time()
            message['timestamp'] = now

            # Check if this message should be deduplicated
            command = message.get('key_name')
            if command in DEDUP_COMMANDS:
                # If we already have this command, update its count
                if command in self.last_message_time:
                    # Check if the existing command is still valid (not timed out)
                    if now - self.last_message_time[command] < self.timeout:
                        # Increment count instead of adding a new message
                        self.command_counts[command] += 1

                        # Check if we should send a webhook now based on time interval
                        send_webhook_now = False
                        if command not in self.last_webhook_time or (now - self.last_webhook_time[command] >= WEBHOOK_INTERVAL):
                            send_webhook_now = True
                            self.last_webhook_time[command] = now

                        # Find the existing message and update its count
                        for existing_msg in self.queue:
                            if existing_msg.get('key_name') == command:
                                existing_msg['count'] = self.command_counts[command]
                                # Update timestamp to prevent timeout
                                existing_msg['timestamp'] = now

                                # If we need to send a webhook now, duplicate the message with current count
                                if send_webhook_now:
                                    webhook_msg = existing_msg.copy()
                                    webhook_msg['force_webhook'] = True
                                    webhook_msg['priority'] = True  # Mark as priority
                                    self.queue.append(webhook_msg)

                                return

                # If we didn't find an existing message or it timed out, add a new one
                self.last_message_time[command] = now
                self.last_webhook_time[command] = now
                self.command_counts[command] = 1
                message['count'] = 1

            self.queue.append(message)

            # Limit queue size to prevent memory issues
            if len(self.queue) > MAX_QUEUE_SIZE:
                # Keep priority messages and remove oldest non-priority ones
                priority_msgs = [msg for msg in self.queue if msg.get('priority', False)]
                non_priority_msgs = [msg for msg in self.queue if not msg.get('priority', False)]

                # Sort non-priority by timestamp and keep only newest ones
                non_priority_msgs.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                keep_count = max(0, MAX_QUEUE_SIZE - len(priority_msgs))

                # Rebuild queue with all priority messages and newest non-priority ones
                self.queue = priority_msgs + non_priority_msgs[:keep_count]

    def get(self):
        """Get the next valid message from the queue."""
        with self.lock:
            # Discard messages older than timeout
            now = time.time()
            self.queue = [msg for msg in self.queue if now - msg['timestamp'] < self.timeout]

            # Return None if queue is empty
            if not self.queue:
                return None

            # Return the oldest message
            message = self.queue.pop(0)

            # If this was a deduped command, clear its counter when removed
            command = message.get('key_name')
            if command in DEDUP_COMMANDS:
                # Only clear if this was the last instance of this command
                if all(msg.get('key_name') != command for msg in self.queue):
                    self.command_counts[command] = 0
                    self.last_message_time.pop(command, None)

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
        self.mixer_state = {
            'speakers_on': False,
            'muted': False,
            'local': False,
            'distribute': False,
            'from_ml': False,
            'volume_raw': 0,
        }
        self._mixer_runner = None  # aiohttp AppRunner for cleanup

    def open(self):
        """Find and open the PC2 device"""
        self.dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)

        if self.dev is None:
            raise Exception("PC2 not found")

        # Detach kernel driver if active
        if self.dev.is_kernel_driver_active(0):
            self.dev.detach_kernel_driver(0)

        # Set configuration
        self.dev.set_configuration()

        # Claim interface
        usb.util.claim_interface(self.dev, 0)

        self.connected = True
        logger.info("Opened PC2 device")

    def _release_device(self):
        """Release the USB device handle (best-effort, ignores errors)."""
        self.connected = False
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
        self.send_message([0xf1])
        time.sleep(0.1)
        self.send_message([0x80, 0x01, 0x00])

    def send_message(self, message):
        """Send a message to the device"""
        telegram = [0x60, len(message)] + list(message) + [0x61]
        logger.debug("Sending: %s", " ".join([f"{x:02X}" for x in telegram]))
        self.dev.write(self.EP_OUT, telegram, 0)

    def set_address_filter(self):
        """Set the address filter to capture all data"""
        logger.info("Setting address filter to capture all data")
        self.send_message([0xF6, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])

    def start_sniffing(self):
        """Start sniffing USB messages and sending them via webhook"""
        self.running = True

        # Create an event loop for the sender thread
        self.loop = asyncio.new_event_loop()

        # Start the sniffer thread (reads USB and adds to queue)
        self.sniffer_thread = threading.Thread(target=self._sniff_loop)
        self.sniffer_thread.daemon = True
        self.sniffer_thread.start()

        # Start the sender thread (processes queue and sends messages)
        self.sender_thread = threading.Thread(target=self._sender_loop_wrapper)
        self.sender_thread.daemon = True
        self.sender_thread.start()

        logger.info("USB message sniffer and sender threads started")

    def _sniff_loop(self):
        """Background thread to continuously read USB messages and add to queue.
        Automatically reconnects if the USB device disconnects."""
        while self.running:
            if not self.connected:
                # Device was lost — try to reconnect
                if not self._reconnect():
                    break  # self.running became False
                continue

            try:
                data = self.dev.read(self.EP_IN, 1024, timeout=500)

                if data and len(data) > 0:
                    message = list(data)
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

                    # Process the message (only for Beo4 keycodes)
                    if len(message) > 2 and message[2] == 0x02:
                        msg_data = self.process_beo4_keycode(timestamp, message)
                        if msg_data:
                            self.message_queue.add(msg_data)

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

    def _sender_loop_wrapper(self):
        """Wrapper to run the async sender loop in its own thread"""
        try:
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._init_session())
            self.loop.run_until_complete(self._start_mixer_http())
            self.loop.create_task(watchdog_loop())
            self.loop.run_until_complete(self._async_sender_loop())
        except Exception as e:
            logger.error("Sender loop failed: %s", e, exc_info=True)

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
        # Visual feedback: pulse LED on button press (fire-and-forget)
        try:
            asyncio.create_task(self._pulse_led())
        except Exception:
            pass

        # Prepare payload
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
            async with self.session.get('http://localhost:8767/led?mode=pulse', timeout=aiohttp.ClientTimeout(total=0.5)) as resp:
                pass
        except Exception:
            pass  # Ignore errors - this is just visual feedback

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

        # Key mapping
        key_map = {
            0x00: "0", 0x01: "1", 0x02: "2", 0x03: "3", 0x04: "4",
            0x05: "5", 0x06: "6", 0x07: "7", 0x08: "8", 0x09: "9",
            0x0C: "off",
            0x0D: "mute",
            0x0F: "alloff",
            0x5C: "menu", # Display on Beo1
            0x20: "track",
            0x1E: "up", 0x1F: "down",
            0x32: "left", 0x34: "right",
            0x35: "go", 0x36: "stop", 0x7F: "back",
            0x37: "record",
            0x58: "list",
            0x60: "volup", 0x64: "voldown",
            0x80: "tv",
            0x81: "radio",
            0x85: "vmem",
            0x86: "dvd",
            0x8A: "dtv",
            0x91: "amem",
            0x92: "cd",
            0x94: "n.music",
            0x9B: "light",
            0xBF: "av",
            0xC1: "random",
            0xD4: "yellow", 0xD5: "green", 0xD8: "blue", 0xD9: "red"
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

    # --- Mixer control (PC2 DAC/amp commands) ---

    def speaker_power(self, on):
        """Turn speakers on or off with proper mute sequencing."""
        if on:
            self.send_message([0xea, 0xFF])   # power on
            time.sleep(0.05)
            self.send_message([0xea, 0x81])   # unmute
            self.mixer_state['speakers_on'] = True
            self.mixer_state['muted'] = False
            logger.info("Speakers powered ON")
        else:
            self.send_message([0xea, 0x80])   # mute first
            time.sleep(0.05)
            self.send_message([0xea, 0x00])   # power off
            self.mixer_state['speakers_on'] = False
            self.mixer_state['muted'] = True
            logger.info("Speakers powered OFF")

    def speaker_mute(self, muted):
        """Mute or unmute speakers."""
        if muted:
            self.send_message([0xea, 0x80])
            self.mixer_state['muted'] = True
            logger.info("Speakers MUTED")
        else:
            self.send_message([0xea, 0x81])
            self.mixer_state['muted'] = False
            logger.info("Speakers UNMUTED")

    def volume_adjust(self, steps):
        """Adjust volume by given number of steps (positive=up, negative=down)."""
        cmd = [0xeb, 0x80] if steps > 0 else [0xeb, 0x81]
        for _ in range(abs(steps)):
            self.send_message(cmd)
            time.sleep(0.02)
        # Best-effort tracking (relative, may drift)
        self.mixer_state['volume_raw'] = max(0, min(RAW_VOL_MAX,
            self.mixer_state['volume_raw'] + steps))
        logger.info("Volume %s by %d step(s)", "UP" if steps > 0 else "DOWN", abs(steps))

    def set_volume(self, raw):
        """Set absolute volume via CMD_SET_PARAMS (0xE3).

        raw: 0–RAW_VOL_MAX (clamped). Bass/treble/balance neutral.
        """
        raw = max(0, min(RAW_VOL_MAX, int(raw)))
        self.send_message([0xe3, raw, 0, 0, 0])
        self.mixer_state['volume_raw'] = raw
        logger.info("Volume set to raw %d (%.0f%%)", raw, _raw_to_pct(raw))

    def set_routing(self, local=False, distribute=False, from_ml=False):
        """Set audio routing. All False = audio off."""
        if not local and not distribute and not from_ml:
            # All off
            self.send_message([0xe7, 0x01])
            time.sleep(0.02)
            self.send_message([0xe5, 0x00, 0x00, 0x00, 0x01])
        else:
            self.send_message([0xe7, 0x00])
            time.sleep(0.02)
            # Build locally byte: 0x00=off, 0x01=local, 0x03=local+from_ml, 0x04=from_ml
            if local and from_ml:
                locally = 0x03
            elif from_ml:
                locally = 0x04
            elif local:
                locally = 0x01
            else:
                locally = 0x00
            dist = 0x01 if distribute else 0x00
            self.send_message([0xe5, locally, dist, 0x00, 0x00])

        self.mixer_state['local'] = local
        self.mixer_state['distribute'] = distribute
        self.mixer_state['from_ml'] = from_ml
        logger.info("Routing: local=%s distribute=%s from_ml=%s", local, distribute, from_ml)

    def audio_on(self):
        """Convenience: power on speakers, set safe volume, and route local audio."""
        self.speaker_power(True)
        time.sleep(0.05)
        self.set_volume(_pct_to_raw(20))
        time.sleep(0.02)
        self.set_routing(local=True)

    def audio_off(self):
        """Convenience: disable routing and power off speakers."""
        self.set_routing(local=False, distribute=False, from_ml=False)
        time.sleep(0.05)
        self.speaker_power(False)

    # --- Mixer HTTP API (port 8768) ---

    async def _handle_mixer_volume(self, request):
        """POST /mixer/volume  {"volume": 0-100}"""
        data = await request.json()
        pct = float(data.get('volume', 0))
        raw = _pct_to_raw(pct)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.set_volume, raw)
        return web.json_response({
            'ok': True, 'volume_pct': _raw_to_pct(raw), 'volume_raw': raw,
        })

    async def _handle_mixer_power(self, request):
        """POST /mixer/power  {"on": true/false}"""
        data = await request.json()
        on = data.get('on', False)
        loop = asyncio.get_running_loop()
        if on:
            await loop.run_in_executor(None, self.audio_on)
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
        state['volume_pct'] = _raw_to_pct(state['volume_raw'])
        state['connected'] = self.connected
        return web.json_response(state)

    async def _start_mixer_http(self):
        """Start the mixer HTTP API server (non-blocking)."""
        app = web.Application()
        app.router.add_post('/mixer/volume', self._handle_mixer_volume)
        app.router.add_post('/mixer/power', self._handle_mixer_power)
        app.router.add_post('/mixer/mute', self._handle_mixer_mute)
        app.router.add_get('/mixer/status', self._handle_mixer_status)
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

        # Close aiohttp session
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

    try:
        pc2 = PC2Device()
        pc2.open()
        pc2.start_sniffing()

        logger.info("Starting device initialization")
        pc2.init()

        logger.info("Setting address filter")
        pc2.set_address_filter()

        if audio_test:
            logger.info("Audio test mode. Commands: on, off, vol+ [n], vol- [n], set <pct>, mute, unmute, distribute, local, quit")
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
                    pc2.audio_on()
                elif cmd == 'off':
                    pc2.audio_off()
                elif cmd == 'vol+':
                    n = int(parts[1]) if len(parts) > 1 else 1
                    pc2.volume_adjust(n)
                elif cmd == 'vol-':
                    n = int(parts[1]) if len(parts) > 1 else 1
                    pc2.volume_adjust(-n)
                elif cmd == 'set':
                    pct = float(parts[1]) if len(parts) > 1 else 0
                    pc2.set_volume(_pct_to_raw(pct))
                elif cmd == 'mute':
                    pc2.speaker_mute(True)
                elif cmd == 'unmute':
                    pc2.speaker_mute(False)
                elif cmd == 'distribute':
                    pc2.set_routing(local=True, distribute=True)
                elif cmd == 'local':
                    pc2.set_routing(local=True)
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
