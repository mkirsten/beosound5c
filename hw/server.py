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
last_power_press_time = 0  # For debouncing power button
POWER_DEBOUNCE_TIME = 2.0  # Seconds to ignore repeated power button presses
power_button_state = 0  # 0 = released, 1 = pressed
screen_control_lock = threading.Lock()  # Lock for screen control operations

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
    """Control screen power using vcgencmd on Raspberry Pi."""
    global screen_control_lock
    
    # Try to acquire the lock with a timeout to prevent deadlocks
    if not screen_control_lock.acquire(timeout=0.5):
        print("[SCREEN] Control operation already in progress, skipping")
        return False
    
    try:
        do_click()
        
        action = "on" if on else "off"
        print(f"[SCREEN] Turning screen {action}")
        
        # Set LED state inverse to screen state
        if on:
            # Screen on -> LED off
            set_led("off")
        else:
            # Screen off -> LED on
            set_led("on")
        
        try:
            # If turning on, also disable DPMS
            if on:
                # Set DISPLAY environment variable
                env = os.environ.copy()
                env["DISPLAY"] = ":0"
                
                # Run xset -dpms to disable DPMS
                subprocess.run(
                    ["xset", "-dpms"],
                    env=env,
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    check=False,
                    timeout=2
                )
                print("[SCREEN] DPMS disabled")
            
            print(f"[SCREEN] Screen {action} command sent successfully")
            return True
            
        except Exception as e:
            print(f"[SCREEN] Error controlling screen: {str(e)}")
            return False
    finally:
        # Always release the lock
        screen_control_lock.release()

def set_backlight(on: bool):
    """Turn backlight bit on/off."""
    global state_byte1, backlight_on
    
    # Update state
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
    global backlight_on
    # Get the current state and toggle it
    new_state = not backlight_on
    print(f"[BACKLIGHT] Toggling from {backlight_on} to {new_state}")
    set_backlight(new_state)

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
    global last_power_press_time, power_button_state
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
    
    # Handle power button with state machine
    b = rep[3]
    is_power_pressed = (b == 0x80)  # Check if power button is currently pressed
    
    # Only create button events for non-power buttons
    if b in BTN_MAP and b != 0x80:
        btn_evt = {'button': BTN_MAP[b]}
    
    # State machine for power button
    if is_power_pressed:
        # Button is pressed
        if power_button_state == 0:  # Was released before
            power_button_state = 1  # Now pressed
            print("[BUTTON] Power button pressed")
    else:
        # Button is released
        if power_button_state == 1:  # Was pressed before
            power_button_state = 0  # Now released
            print("[BUTTON] Power button released")
            
            # Check debounce time
            current_time = time.time()
            if current_time - last_power_press_time > POWER_DEBOUNCE_TIME:
                print("[BUTTON] Power button action triggered")
                toggle_backlight()
                last_power_press_time = current_time
                # Create button event for power button release
                btn_evt = {'button': 'power'}
            else:
                print(f"[BUTTON] Power button debounced (pressed too soon)")

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