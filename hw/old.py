#!/usr/bin/env python3
import asyncio, threading, json
import hid, time, sys
import websockets

VID, PID = 0x0cd4, 0x1112
BTN_MAP   = {0x20:'left',0x10:'right',0x40:'go',0x80:'power'}

# — WebSocket clients set
clients = set()

async def handler(ws):
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.remove(ws)

async def broadcast(msg):
    if not clients:
        return
    # send to all clients concurrently, swallowing any errors
    await asyncio.gather(
        *(ws.send(msg) for ws in clients),
        return_exceptions=True
    )


def parse(rep):
    nav,vol,btn,pos = None,None,None,rep[2]
    # nav
    if rep[0]!=0:
        nav = {
          'direction': 'clock' if rep[0]<0x80 else 'counter',
          'speed':     rep[0] if rep[0]<0x80 else 256-rep[0]
        }
    # volume
    if rep[1]!=0:
        vol = {
          'direction': 'clock' if rep[1]<0x80 else 'counter',
          'speed':     rep[1] if rep[1]<0x80 else 256-rep[1]
        }
    # button
    if rep[3] in BTN_MAP:
        btn = {'button': BTN_MAP[rep[3]]}
    return nav, vol, btn, pos

def scan_loop(loop):
    devices = hid.enumerate(VID, PID)
    if not devices:
        print("BS5 not found"); sys.exit(1)
    dev = hid.device()
    dev.open(VID, PID)
    dev.set_nonblocking(True)
    
    last_laser = None
    first = True
    while True:
        rpt = dev.read(64, 50)
        if rpt:
            rep = list(rpt)
            nav_evt, vol_evt, btn_evt, laser_pos = parse(rep)
            for ev_type, ev in (('nav',nav_evt),('volume',vol_evt),('button',btn_evt)):
                if ev:
                    msg = json.dumps({'type':ev_type, 'data':ev})
                    asyncio.run_coroutine_threadsafe(broadcast(msg), loop)
            if first or laser_pos!=last_laser:
                msg = json.dumps({'type':'laser','data':{'position': laser_pos}})
                asyncio.run_coroutine_threadsafe(broadcast(msg), loop)
                last_laser, first = laser_pos, False
        time.sleep(0.01)

async def main():
    # start ws server
    server = await websockets.serve(handler, '0.0.0.0', 8765)
    # start scanner in thread
    threading.Thread(target=scan_loop, args=(asyncio.get_event_loop(),), daemon=True).start()
    print("WebSocket server listening on ws://0.0.0.0:8765")
    await server.wait_closed()

if __name__=='__main__':
    asyncio.run(main())
