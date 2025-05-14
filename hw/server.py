#!/usr/bin/env python3
import asyncio, threading, json, time, sys
import hid, websockets
import subprocess  # Add subprocess for xset commands
import os  # For path operations

VID, PID = 0x0cd4, 0x1112
BTN_MAP = {0x20:'left', 0x10:'right', 0x40:'go', 0x80:'power'}
clients = set()

# ——— track current "byte1" state (LED/backlight bits) ———
state_byte1 = 0x00
backlight_on = True  # Track backlight state

def bs5_send(data: bytes):
    """Low-level HID write."""
    try:
        dev.write(data)
    except Exception as e:
        print("HID write failed:", e)

def bs5_send_cmd(byte1, byte2=0x00):
    """Build & send HID report."""
    bs5_send(bytes([byte1, byte2]))

def do_click():
    """Send click bit on top of current state."""
    global state_byte1
    bs5_send_cmd(state_byte1 | 0x01)

def set_led(mode: str):
    """mode in {'on','off','blink'}"""
    global state_byte1
    state_byte1 &= ~(0x80 | 0x10)       # clear LED bits
    if mode == 'on':
        state_byte1 |= 0x80
    elif mode == 'blink':
        state_byte1 |= 0x10
    bs5_send_cmd(state_byte1)

def control_screen(on: bool):
    do_click()
    
    """Control screen power using multiple methods for better compatibility."""
    action = "on" if on else "off"
    print(f"[SCREEN] Attempting to turn screen {action}")
    
    # Set LED state inverse to screen state
    if on:
        # Screen on -> LED off
        set_led("off")
    else:
        # Screen off -> LED on
        set_led("on")
    
    # Track which methods succeeded
    success_methods = []
    
    try:
        if on:
            # Try multiple methods to turn on screen
            # 1. DPMS method
            try:
                subprocess.run(["xset", "dpms", "force", "on"], stderr=subprocess.PIPE, stdout=subprocess.PIPE, check=True, timeout=2)
                success_methods.append("DPMS")
            except subprocess.SubprocessError as e:
                print(f"[SCREEN] DPMS on failed: {str(e)}")
            
            # 2. xrandr method - enable all connected outputs
            try:
                subprocess.run(["xrandr", "--auto"], stderr=subprocess.PIPE, stdout=subprocess.PIPE, check=True, timeout=2)
                success_methods.append("xrandr")
            except subprocess.SubprocessError as e:
                print(f"[SCREEN] xrandr auto failed: {str(e)}")
            
            # 3. Try direct backlight control if available
            for backlight_dir in ["/sys/class/backlight/intel_backlight", "/sys/class/backlight/acpi_video0"]:
                if os.path.exists(backlight_dir):
                    try:
                        max_brightness_file = os.path.join(backlight_dir, "max_brightness")
                        with open(max_brightness_file, "r") as f:
                            max_brightness = int(f.read().strip())
                            target = max(1, max_brightness // 2)  # 50% brightness or at least 1
                            
                        brightness_file = os.path.join(backlight_dir, "brightness")
                        with open(brightness_file, "w") as f_write:
                            f_write.write(str(target))
                        success_methods.append(f"backlight({os.path.basename(backlight_dir)})")
                    except (IOError, PermissionError) as e:
                        print(f"[SCREEN] Backlight control failed for {backlight_dir}: {str(e)}")
        else:
            # Try multiple methods to turn off screen
            # 1. DPMS method
            try:
                subprocess.run(["xset", "dpms", "force", "off"], stderr=subprocess.PIPE, stdout=subprocess.PIPE, check=True, timeout=2)
                success_methods.append("DPMS")
            except subprocess.SubprocessError as e:
                print(f"[SCREEN] DPMS off failed: {str(e)}")
            
            # 2. xrandr method - disable all outputs
            try:
                # Get list of connected outputs
                result = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=2)
                outputs = []
                for line in result.stdout.splitlines():
                    if " connected " in line:
                        output_name = line.split()[0]
                        outputs.append(output_name)
                
                # Turn off each output
                for output in outputs:
                    subprocess.run(["xrandr", "--output", output, "--off"], 
                                stderr=subprocess.PIPE, stdout=subprocess.PIPE, timeout=2)
                if outputs:
                    success_methods.append("xrandr")
            except subprocess.SubprocessError as e:
                print(f"[SCREEN] xrandr off failed: {str(e)}")
            
            # 3. Try direct backlight control if available
            for backlight_dir in ["/sys/class/backlight/intel_backlight", "/sys/class/backlight/acpi_video0"]:
                if os.path.exists(backlight_dir):
                    try:
                        brightness_file = os.path.join(backlight_dir, "brightness")
                        with open(brightness_file, "w") as f:
                            f.write("0")
                        success_methods.append(f"backlight({os.path.basename(backlight_dir)})")
                    except (IOError, PermissionError) as e:
                        print(f"[SCREEN] Backlight control failed for {backlight_dir}: {str(e)}")
        
        if success_methods:
            print(f"[SCREEN] Successfully turned screen {action} using: {', '.join(success_methods)}")
        else:
            print(f"[SCREEN] WARNING: All methods to turn screen {action} failed")
            
    except Exception as e:
        print(f"[SCREEN] Unexpected error controlling screen: {str(e)}")
        import traceback
        traceback.print_exc()

def set_backlight(on: bool):
    """Turn backlight bit on/off."""
    global state_byte1, backlight_on
    backlight_on = on
    if on:
        state_byte1 |= 0x40
    else:
        state_byte1 &= ~0x40
    bs5_send_cmd(state_byte1)
    
    # Control screen separately
    control_screen(on)

def toggle_backlight():
    """Toggle backlight state."""
    set_backlight(not backlight_on)

# ——— WebSocket boilerplate ———

async def handler(ws, path=None):
    clients.add(ws)
    recv_task = asyncio.create_task(receive_commands(ws))
    try:
        await ws.wait_closed()
    finally:
        recv_task.cancel()
        clients.remove(ws)

async def broadcast(msg: str):
    if not clients:
        return
    await asyncio.gather(
        *(ws.send(msg) for ws in clients),
        return_exceptions=True
    )

async def receive_commands(ws):
    async for raw in ws:
        try:
            msg = json.loads(raw)
            print('[WS RECEIVED]', msg)  # Log every received message
            if msg.get('type') != 'command':
                continue
            cmd    = msg.get('command')
            params = msg.get('params', {})
            if cmd == 'click':
                do_click()
            elif cmd == 'led':
                set_led(params.get('mode','on'))
            elif cmd == 'backlight':
                set_backlight(bool(params.get('on',True)))
        except Exception:
            pass

# ——— HID parse & broadcast loop ———

def parse_report(rep: list):
    nav_evt = vol_evt = btn_evt = None
    laser_pos = rep[2]

    if rep[0] != 0:
        d = rep[0]
        nav_evt = {
            'direction': 'clock' if d < 0x80 else 'counter',
            'speed':     d if d < 0x80 else 256-d
        }
    if rep[1] != 0:
        d = rep[1]
        vol_evt = {
            'direction': 'clock' if d < 0x80 else 'counter',
            'speed':     d if d < 0x80 else 256-d
        }
    b = rep[3]
    if b in BTN_MAP:
        btn_evt = {'button': BTN_MAP[b]}
        # Handle power button press by toggling backlight
        if BTN_MAP[b] == 'power':
            toggle_backlight()

    return nav_evt, vol_evt, btn_evt, laser_pos

def scan_loop(loop):
    global dev
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

            for evt_type, evt in (
                ('nav',    nav_evt),
                ('volume', vol_evt),
                ('button', btn_evt),
            ):
                if evt:
                    asyncio.run_coroutine_threadsafe(
                        broadcast(json.dumps({'type':evt_type,'data':evt})),
                        loop
                    )

            if first or laser_pos != last_laser:
                asyncio.run_coroutine_threadsafe(
                    broadcast(json.dumps({'type':'laser','data':{'position':laser_pos}})),
                    loop
                )
                last_laser, first = laser_pos, False

        time.sleep(0.001)

# ——— Main & server start ———

async def main():
    ws_srv = await websockets.serve(handler, '0.0.0.0', 8765)
    print("WebSocket server listening on ws://0.0.0.0:8765")
    threading.Thread(target=scan_loop, args=(asyncio.get_event_loop(),), daemon=True).start()
    await ws_srv.wait_closed()

if __name__ == '__main__':
    asyncio.run(main())