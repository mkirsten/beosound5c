#!/usr/bin/env python3
import asyncio
import threading
import json
import time
import sys

import hid
import websockets

VID, PID = 0x0cd4, 0x1112
BTN_MAP = {0x20: 'left', 0x10: 'right', 0x40: 'go', 0x80: 'power'}

clients = set()

# --- WebSocket boilerplate --

async def handler(ws, path=None):
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.remove(ws)

async def broadcast(msg: str):
    if not clients:
        return
    # fire-and-forget to all clients
    await asyncio.gather(
        *(ws.send(msg) for ws in clients),
        return_exceptions=True
    )

# --- HID parsing & stubs --

def parse_report(rep: list):
    """
    rep: 4+ length list of ints
    Returns (nav_evt, vol_evt, btn_evt, laser_pos)
    """
    nav_evt = None
    vol_evt = None
    btn_evt = None
    laser_pos = rep[2]

    # nav wheel
    if rep[0] != 0:
        delta = rep[0]
        direction = 'clock' if delta < 0x80 else 'counter'
        speed = delta if delta < 0x80 else 256 - delta
        nav_evt = {'direction': direction, 'speed': speed}

    # volume wheel
    if rep[1] != 0:
        delta = rep[1]
        direction = 'clock' if delta < 0x80 else 'counter'
        speed = delta if delta < 0x80 else 256 - delta
        vol_evt = {'direction': direction, 'speed': speed}

    # button
    b = rep[3]
    if b in BTN_MAP:
        btn_evt = {'button': BTN_MAP[b]}

    return nav_evt, vol_evt, btn_evt, laser_pos

def play_click():
    """
    Stub for click sound—fill in later with your HID write,
    e.g. dev.write([0x41, 0x00])
    """
    pass

# --- HID scan loop in thread --

def scan_loop(loop: asyncio.AbstractEventLoop):
    # find & open
    devices = hid.enumerate(VID, PID)
    if not devices:
        print("BS5 not found (no HID device)") 
        sys.exit(1)

    dev = hid.device()
    dev.open(VID, PID)
    dev.set_nonblocking(True)
    print(f"[HID] Opened BS5 @ VID:PID={VID:04x}:{PID:04x}")

    last_laser = None
    first = True

    while True:
        rpt = dev.read(64, timeout_ms=50)
        if rpt:
            rep = list(rpt)
            nav_evt, vol_evt, btn_evt, laser_pos = parse_report(rep)

            # nav/vol/btn
            for evt_type, evt in (
                ('nav', nav_evt),
                ('volume', vol_evt),
                ('button', btn_evt),
            ):
                if evt:
                    # you can call play_click() here if you want per-wheel scroll
                    msg = json.dumps({'type': evt_type, 'data': evt})
                    asyncio.run_coroutine_threadsafe(broadcast(msg), loop)

            # laser position
            if first or laser_pos != last_laser:
                msg = json.dumps({'type': 'laser', 'data': {'position': laser_pos}})
                asyncio.run_coroutine_threadsafe(broadcast(msg), loop)
                last_laser = laser_pos
                first = False

        time.sleep(0.01)

# --- Main & WebSocket server ---

async def main():
    # start WS server
    server = await websockets.serve(handler, '0.0.0.0', 8765)
    print("WebSocket server listening on ws://0.0.0.0:8765")
    # launch HID scanner in background thread
    threading.Thread(
        target=scan_loop,
        args=(asyncio.get_event_loop(),),
        daemon=True
    ).start()
    await server.wait_closed()

if __name__ == '__main__':
    asyncio.run(main())
