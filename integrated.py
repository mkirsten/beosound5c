#!/usr/bin/env python3
import asyncio
import json
import logging
import time
from datetime import datetime

import aiohttp
from aiohttp import web
import hid
import usb.core
import usb.util

# Configure logging
d = logging.getLogger("besound5c_usb")
logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s [%(levelname)s] %(message)s")

# Configuration & endpoints
CONFIG_URL    = "http://homeassistant.local:8123/local/beo_config.json"
WEBHOOK_URL   = "http://homeassistant.local:8123/api/webhook/ir_event"

# USB device IDs
BEOLINK_PC2_VENDOR_ID   = 0x0cd4
BEOLINK_PC2_PRODUCT_ID  = 0x0101

BEOSOUND5_HID_VENDOR_ID  = 0x0cd4
BEOSOUND5_HID_PRODUCT_ID = 0x1112

# HID button map
BEOSOUND5_BTN_MAP = {0x20: 'left', 0x10: 'right', 0x40: 'go', 0x80: 'power'}

# ---- WebSocket Handler ------------------------------------------------------
async def ws_handler(request):
    """WebSocket handler for client connections"""
    try:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        ws_clients.add(ws)
        d.info("WebSocket client connected")
        
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        d.debug(f"Received WebSocket message: {data}")
                        
                        if data.get('type') == 'command':
                            cmd = data.get('command')
                            params = data.get('params', {})
                            mode = params.get('mode', 'on')
                            
                            if cmd == 'click':
                                click()
                                d.info("Executed click command")
                            elif cmd == 'led':
                                set_led(mode)  # supports on/off/blink
                                d.info(f"Set LED mode to: {mode}")
                            elif cmd == 'backlight':
                                # Convert mode string to boolean for backlight
                                set_backlight(mode == 'on')
                                d.info(f"Set backlight to: {mode}")
                            else:
                                d.warning(f"Unknown command: {cmd}")
                    except json.JSONDecodeError:
                        d.warning("Received invalid JSON in WebSocket message")
                    except Exception as e:
                        d.error(f"Error processing WebSocket message: {e}")
                elif msg.type == web.WSMsgType.ERROR:
                    d.warning(f"WebSocket connection closed with exception: {ws.exception()}")
                elif msg.type == web.WSMsgType.CLOSE:
                    d.info("WebSocket connection closed normally")
                    break
        except Exception as e:
            d.error(f"Error in WebSocket connection: {e}")
        finally:
            ws_clients.remove(ws)
            d.info("WebSocket client disconnected")
        
        return ws
    except Exception as e:
        d.error(f"Error setting up WebSocket connection: {e}")
        return web.Response(status=500, text="Internal Server Error")

# Aiohttp app and websocket clients
app = web.Application()
app.router.add_static('/', path='./web', show_index=True)
app.router.add_get('/ws', ws_handler)

# Add CORS middleware
async def cors_middleware(app, handler):
    async def middleware_handler(request):
        response = await handler(request)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
    return middleware_handler

app.middlewares.append(cors_middleware)

# Shared queues & clients
event_queue   = asyncio.Queue()
webhook_queue = asyncio.Queue()
ws_clients    = set()

# Global HID control device
hid_ctrl = None
# Global state byte
state_byte1 = 0x00

# ---- HID Control Helpers ---------------------------------------------------
def click():
    global hid_ctrl
    if not hid_ctrl:
        return
    # send click (0x01) on top of state
    hid_ctrl.write([state_byte1 | 0x01])

def set_led(mode: str):
    global hid_ctrl, state_byte1
    # clear LED bits
    state_byte1 &= ~(0x80 | 0x10)
    if mode == 'on':
        state_byte1 |= 0x80
    elif mode == 'blink':
        state_byte1 |= 0x10
    hid_ctrl.write([state_byte1, 0x00])


def set_backlight(on: bool):
    global hid_ctrl, state_byte1
    if on:
        state_byte1 |= 0x40
    else:
        state_byte1 &= ~0x40
    hid_ctrl.write([state_byte1, 0x00])

# ---- Utility: parse HID report ---------------------------------------------

def parse_report(rep):
    nav_evt = vol_evt = btn_evt = None
    laser_pos = rep[2]
    if rep[0] != 0:
        d0 = rep[0]
        nav_evt = {'direction': 'clock' if d0 < 0x80 else 'counter',
                   'speed': d0 if d0 < 0x80 else 256 - d0}
    if rep[1] != 0:
        d1 = rep[1]
        vol_evt = {'direction': 'clock' if d1 < 0x80 else 'counter',
                   'speed': d1 if d1 < 0x80 else 256 - d1}
    b = rep[3]
    if b in BEOSOUND5_BTN_MAP:
        btn_evt = {'button': BEOSOUND5_BTN_MAP[b]}
    return nav_evt, vol_evt, btn_evt, laser_pos

# ---- Utility: decode IR keycode ---------------------------------------------
DEVICE_TYPE_MAP = {0x00: "Video", 0x01: "Audio", 0x05: "Vmem", 0x1B: "Light"}
KEY_CODE_MAP = {0x00: "0", 0x01: "1", 0x02: "2", 0x03: "3", 0x04: "4",
    0x05: "5", 0x06: "6", 0x07: "7", 0x08: "8", 0x09: "9",
    0x0C: "off", 0x0D: "mute", 0x0F: "alloff", 0x5C: "menu",
    0x1E: "up", 0x1F: "down", 0x32: "left", 0x34: "right",
    0x35: "go", 0x36: "stop", 0x7F: "back", 0x58: "list",
    0x60: "volup", 0x64: "voldown", 0x80: "tv", 0x81: "radio",
    0x85: "vmem", 0x86: "dvd", 0x8A: "dtv", 0x91: "amem",
    0x92: "cd", 0xD4: "yellow", 0xD5: "green", 0xD8: "blue", 0xD9: "red"}

def decode_beo4_keycode(raw_bytes):
    """Decode Beo4 keycode from raw USB data"""
    # Debug log the raw data
    d.debug(f"Raw IR data: {' '.join(f'{b:02X}' for b in raw_bytes)}")
    
    # Check if we have enough data for a basic message
    if len(raw_bytes) < 3:
        d.debug(f"Message too short: {len(raw_bytes)} bytes")
        return None, None
        
    # Check message type
    if raw_bytes[2] != 0x02:
        d.debug(f"Not a Beo4 keycode message (type: 0x{raw_bytes[2]:02X})")
        return None, None
        
    # For Beo4 keycodes, we need at least 7 bytes
    if len(raw_bytes) < 7:
        d.debug(f"Beo4 keycode message too short: {len(raw_bytes)} bytes")
        return None, None
        
    try:
        mode = raw_bytes[4]
        keycode = raw_bytes[6]
        device_type = DEVICE_TYPE_MAP.get(mode, f"Unknown(0x{mode:02X})")
        key_name = KEY_CODE_MAP.get(keycode, f"Unknown(0x{keycode:02X})")
        return device_type, key_name
    except Exception as e:
        d.error(f"Error decoding Beo4 keycode: {e}")
        return None, None

# ---- Predicates -------------------------------------------------------------

def should_send_webhook(evt):
    """Determine if this event should be sent as a webhook"""
    # IR events have device_type and key fields
    if 'key' in evt:
        dt, key = evt.get('device_type'), evt.get('key')
        return dt == 'Audio' or (dt == 'Video' and key == '9')
    
    # HID events have device_type=HID and kind/data fields
    if evt.get('device_type') == 'HID':
        kind = evt.get('kind')
        # Send all HID button, nav and volume events
        return kind in ('button', 'nav', 'volume')
    
    return False

def should_send_ws(evt):
    """Determine if this event should be broadcast via websocket"""
    # IR events
    if 'key' in evt:
        key = evt.get('key', '')
        return not key.startswith('Unknown')
    
    # All HID events should be sent
    if evt.get('device_type') == 'HID':
        return True
    
    return False

# ---- WebSocket handlers -----------------------------------------------------
async def broadcast_ws(evt):
    if not ws_clients: return
    msg = json.dumps(evt)
    await asyncio.gather(*(ws.send_str(msg) for ws in ws_clients), return_exceptions=True)

# ---- Workers ----------------------------------------------------------------
async def webhook_worker():
    async with aiohttp.ClientSession() as session:
        while True:
            evt = await webhook_queue.get()
            if time.time() - evt['timestamp_epoch'] > 1.0:
                webhook_queue.task_done()
                continue
            try:
                await session.post(WEBHOOK_URL, json=evt, timeout=0.5)
            except Exception as e:
                d.warning(f"Webhook error: {e}")
            webhook_queue.task_done()

async def ws_worker():
    while True:
        evt = await event_queue.get()
        if should_send_ws(evt):
            await broadcast_ws(evt)
        if should_send_webhook(evt):
            await webhook_queue.put(evt)
        event_queue.task_done()

# ---- HID Reader -------------------------------------------------------------
async def hid_reader_loop():
    """HID reader loop that handles device connection/disconnection gracefully"""
    global hid_ctrl
    
    while True:
        try:
            # Try to find and open the device
            devices = hid.enumerate(BEOSOUND5_HID_VENDOR_ID, BEOSOUND5_HID_PRODUCT_ID)
            if not devices:
                d.warning("HID device not found, retrying in 5 seconds...")
                await asyncio.sleep(5)
                continue
                
            # Open the device
            hid_ctrl = hid.device()
            hid_ctrl.open(BEOSOUND5_HID_VENDOR_ID, BEOSOUND5_HID_PRODUCT_ID)
            hid_ctrl.set_nonblocking(True)
            d.info(f"Opened HID @ {BEOSOUND5_HID_VENDOR_ID:04x}:{BEOSOUND5_HID_PRODUCT_ID:04x}")
            
            # Reader loop
            last_laser = None
            first = True
            consecutive_errors = 0
            
            while True:
                try:
                    rep = hid_ctrl.read(64)
                    consecutive_errors = 0  # Reset error counter on success
                    
                    if rep:
                        nav, vol, btn, laser = parse_report(list(rep))
                        ts = datetime.utcnow().isoformat()
                        now = time.time()
                        
                        for kind, data in (('nav', nav), ('volume', vol), ('button', btn)):
                            if data:
                                evt = {'timestamp': ts,'timestamp_epoch': now,
                                       'device_type':'HID','kind':kind,'data':data}
                                await event_queue.put(evt)
                                d.info(f"HID event: {evt}")
                               
                        if first or laser != last_laser:
                            evt = {'timestamp': ts,'timestamp_epoch': now,
                                   'device_type':'HID','kind':'laser','data':{'position':laser}}
                            await event_queue.put(evt)
                            last_laser, first = laser, False
                except IOError as e:
                    consecutive_errors += 1
                    if consecutive_errors > 5:
                        d.error(f"Multiple HID errors: {e}, reconnecting...")
                        break
                    d.warning(f"HID error: {e}, retrying...")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    d.error(f"Unexpected error in HID reader: {e}")
                    break
                    
                await asyncio.sleep(0.001)
                
            # If we get here, try to close the device before reconnecting
            try:
                if hid_ctrl:
                    hid_ctrl.close()
                    hid_ctrl = None
                    d.info("Closed HID device")
            except:
                pass
                
        except Exception as e:
            d.error(f"Error setting up HID device: {e}")
            if hid_ctrl:
                try:
                    hid_ctrl.close()
                except:
                    pass
                hid_ctrl = None
                
        # Wait before attempting to reconnect
        await asyncio.sleep(5)

# ---- IR Reader --------------------------------------------------------------
async def ir_reader_loop():
    """IR reader loop that handles device connection/disconnection gracefully"""
    while True:
        try:
            # Try to find and open the device
            dev = usb.core.find(idVendor=BEOLINK_PC2_VENDOR_ID, idProduct=BEOLINK_PC2_PRODUCT_ID)
            if not dev:
                d.warning("Beolink PC2 USB device not found, retrying in 5 seconds...")
                await asyncio.sleep(5)
                continue
                
            # Setup device
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
            dev.set_configuration()
            usb.util.claim_interface(dev, 0)
            d.info("Opened Beolink USB device for IR processing")
            
            # Send init messages
            def send_beolink_message(dev, message):
                """Format and send message to IR device using proper protocol"""
                try:
                    telegram = [0x60, len(message)] + list(message) + [0x61]
                    dev.write(0x01, telegram, timeout=100)
                    d.debug(f"Sent message: {' '.join(f'{b:02X}' for b in telegram)}")
                except Exception as e:
                    d.error(f"Error sending message: {e}")

            def set_address_filter(dev, mode):
                try:
                    if mode == "ALL":
                        send_beolink_message(dev, [0xf6, 0xc0, 0xc1, 0x80, 0x83, 0x05, 0x00, 0x00])
                    elif mode == "AUDIO_MASTER":
                        send_beolink_message(dev, [0xf6, 0x10, 0xc1, 0x80, 0x83, 0x05, 0x00, 0x00])
                    elif mode == "BEOPORT":
                        send_beolink_message(dev, [0xf6, 0x00, 0x82, 0x80, 0x83])
                except Exception as e:
                    d.error(f"Error setting address filter: {e}")

            # Initialize device with retries
            init_success = False
            for attempt in range(3):
                try:
                    send_beolink_message(dev, [0xf1])
                    await asyncio.sleep(0.1)
                    send_beolink_message(dev, [0x80, 0x01, 0x00])
                    await asyncio.sleep(0.1)
                    set_address_filter(dev, "ALL")  # Set to promiscuous mode to capture all signals
                    init_success = True
                    break
                except Exception as e:
                    d.warning(f"Init attempt {attempt + 1} failed: {e}")
                    await asyncio.sleep(0.5)
            
            if not init_success:
                d.error("Failed to initialize device after 3 attempts")
                continue
                
            EP_IN = 0x81
            d.info("Configured Beolink USB device")
            
            # Main read loop
            consecutive_errors = 0
            while True:
                try:
                    # Read with a larger buffer to handle any message size
                    data = dev.read(EP_IN, 64, timeout=100)
                    consecutive_errors = 0  # Reset error counter on success
                    
                    if data:
                        raw = list(data)
                        device_type, key_name = decode_beo4_keycode(raw)
                        if device_type and key_name:  # Only process if we got valid data
                            ts = datetime.utcnow().isoformat()
                            now = time.time()
                            evt = {'timestamp': ts,'timestamp_epoch': now,
                                   'device_type': device_type,'key': key_name,
                                   'raw': " ".join(f"{b:02X}" for b in raw)}
                            await event_queue.put(evt)
                            d.info(f"IR event: {evt}")
                except usb.core.USBTimeoutError:
                    # Timeout is normal, just continue
                    pass
                except usb.core.USBError as e:
                    consecutive_errors += 1
                    if consecutive_errors > 5:
                        d.error(f"Multiple USB errors: {e}, reconnecting...")
                        break
                    d.warning(f"USB error: {e}, retrying...")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    d.error(f"Unexpected error in IR reader: {e}")
                    break
                    
                await asyncio.sleep(0.01)
                
            # If we get here, try to release the device before reconnecting
            try:
                usb.util.release_interface(dev, 0)
                d.info("Released IR device interface")
            except:
                pass
                
        except Exception as e:
            d.error(f"Error setting up IR device: {e}")
            
        # Wait before attempting to reconnect
        await asyncio.sleep(5)

# ---- Config Fetch & App Startup --------------------------------------------
async def fetch_config():
    async with aiohttp.ClientSession() as session:
        async with session.get(CONFIG_URL) as resp:
            return await resp.json()

async def main():
    # cfg = await fetch_config()
    # d.info(f"Loaded config: {cfg}")
    
    # Start background tasks
    workers = []
    workers.append(asyncio.create_task(webhook_worker()))
    workers.append(asyncio.create_task(ws_worker()))
    workers.append(asyncio.create_task(hid_reader_loop()))
    workers.append(asyncio.create_task(ir_reader_loop()))
    
    # Start web server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8765)
    await site.start()
    d.info("Server running at http://0.0.0.0:8765")
    
    # Setup cleanup handlers
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        d.info("Shutting down gracefully...")
    except KeyboardInterrupt:
        d.info("Keyboard interrupt received, shutting down...")
    finally:
        # Cancel all background tasks
        for task in workers:
            task.cancel()
        
        # Shutdown web server
        await runner.cleanup()
        
        # Close HID device if open
        if hid_ctrl:
            try:
                hid_ctrl.close()
                d.info("HID device closed")
            except Exception as e:
                d.error(f"Error closing HID device: {e}")
        
        d.info("Shutdown complete")

if __name__ == '__main__':
    asyncio.run(main())