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
    """Control screen power using multiple methods for better compatibility."""
    do_click()
    
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
    
    # Set up environment for X11 commands
    env = os.environ.copy()
    # Make sure DISPLAY is set
    if 'DISPLAY' not in env:
        env['DISPLAY'] = ':0'
    
    # Print diagnostic information
    print(f"[SCREEN] Current environment: DISPLAY={env.get('DISPLAY', 'not set')}, XAUTHORITY={env.get('XAUTHORITY', 'not set')}")
    
    try:
        # Try to get system information
        try:
            uname_output = subprocess.run(["uname", "-a"], capture_output=True, text=True, check=False).stdout.strip()
            print(f"[SCREEN] System info: {uname_output}")
        except Exception as e:
            print(f"[SCREEN] Could not get system info: {e}")
        
        # Check if we're running in a graphical environment
        try:
            ps_output = subprocess.run(["ps", "aux"], capture_output=True, text=True, check=False).stdout
            has_x11 = "Xorg" in ps_output or "X11" in ps_output
            has_wayland = "wayland" in ps_output.lower()
            print(f"[SCREEN] Display server detection: X11={has_x11}, Wayland={has_wayland}")
        except Exception as e:
            print(f"[SCREEN] Could not detect display server: {e}")
            has_x11 = True  # Assume X11 if we can't detect
            has_wayland = False
        
        if on:
            # Try multiple methods to turn on screen
            
            # 1. Try direct backlight control if available
            for backlight_dir in ["/sys/class/backlight/intel_backlight", 
                                 "/sys/class/backlight/acpi_video0", 
                                 "/sys/class/backlight/rpi_backlight",
                                 "/sys/class/backlight/amdgpu_bl0"]:
                if os.path.exists(backlight_dir):
                    try:
                        print(f"[SCREEN] Found backlight directory: {backlight_dir}")
                        max_brightness_file = os.path.join(backlight_dir, "max_brightness")
                        with open(max_brightness_file, "r") as f:
                            max_brightness = int(f.read().strip())
                            target = max(1, max_brightness // 2)  # 50% brightness or at least 1
                        
                        brightness_file = os.path.join(backlight_dir, "brightness")
                        print(f"[SCREEN] Setting brightness to {target}/{max_brightness}")
                        with open(brightness_file, "w") as f_write:
                            f_write.write(str(target))
                        success_methods.append(f"backlight({os.path.basename(backlight_dir)})")
                    except (IOError, PermissionError) as e:
                        print(f"[SCREEN] Backlight control failed for {backlight_dir}: {str(e)}")
            
            # 2. Try using vcgencmd for Raspberry Pi
            try:
                print("[SCREEN] Trying vcgencmd method")
                result = subprocess.run(
                    ["vcgencmd", "display_power", "1"], 
                    stderr=subprocess.PIPE, 
                    stdout=subprocess.PIPE, 
                    check=False,
                    timeout=2
                )
                print(f"[SCREEN] vcgencmd result: {result.stdout.decode() if result.stdout else 'No output'}")
                success_methods.append("vcgencmd")
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                print(f"[SCREEN] vcgencmd method failed: {str(e)}")
            
            # 3. Try DDC/CI control if available (external monitors)
            try:
                print("[SCREEN] Trying ddcutil method")
                # Find available displays
                result = subprocess.run(
                    ["ddcutil", "detect"], 
                    stderr=subprocess.PIPE, 
                    stdout=subprocess.PIPE, 
                    check=False,
                    timeout=5
                )
                if "Display" in result.stdout.decode():
                    # Turn on all displays
                    subprocess.run(
                        ["ddcutil", "setvcp", "D6", "1"], 
                        stderr=subprocess.PIPE, 
                        stdout=subprocess.PIPE, 
                        check=False,
                        timeout=2
                    )
                    success_methods.append("ddcutil")
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                print(f"[SCREEN] ddcutil method failed: {str(e)}")
            
            # 4. X11 methods if we have X11
            if has_x11:
                # 4a. DPMS method with proper environment
                try:
                    print("[SCREEN] Trying xset method")
                    result = subprocess.run(
                        ["xset", "dpms", "force", "on"], 
                        env=env,
                        stderr=subprocess.PIPE, 
                        stdout=subprocess.PIPE, 
                        check=False,
                        timeout=2
                    )
                    print(f"[SCREEN] xset result: {result.returncode}")
                    
                    # Check if screen is on by querying DPMS state
                    result = subprocess.run(
                        ["xset", "q"], 
                        env=env,
                        capture_output=True, 
                        text=True, 
                        check=False,
                        timeout=2
                    )
                    if "Monitor is On" in result.stdout:
                        success_methods.append("DPMS")
                except subprocess.SubprocessError as e:
                    print(f"[SCREEN] DPMS on failed: {str(e)}")
                
                # 4b. xrandr method - enable all connected outputs
                try:
                    print("[SCREEN] Trying xrandr method")
                    # First query available outputs
                    result = subprocess.run(
                        ["xrandr", "--query"], 
                        env=env,
                        capture_output=True, 
                        text=True, 
                        check=False,
                        timeout=2
                    )
                    print(f"[SCREEN] xrandr outputs: {result.stdout[:200]}...")
                    
                    # Find connected outputs
                    outputs = []
                    for line in result.stdout.splitlines():
                        if " connected " in line:
                            output_name = line.split()[0]
                            outputs.append(output_name)
                    
                    print(f"[SCREEN] Found outputs: {outputs}")
                    
                    # Try to enable each output individually
                    for output in outputs:
                        try:
                            print(f"[SCREEN] Enabling output: {output}")
                            result = subprocess.run(
                                ["xrandr", "--output", output, "--auto"], 
                                env=env,
                                stderr=subprocess.PIPE, 
                                stdout=subprocess.PIPE, 
                                check=False,
                                timeout=2
                            )
                            print(f"[SCREEN] xrandr result for {output}: {result.returncode}")
                        except Exception as e:
                            print(f"[SCREEN] Failed to enable output {output}: {str(e)}")
                    
                    if outputs:
                        success_methods.append("xrandr")
                except subprocess.SubprocessError as e:
                    print(f"[SCREEN] xrandr auto failed: {str(e)}")
        else:
            # Try multiple methods to turn off screen
            
            # 1. Try direct backlight control if available
            for backlight_dir in ["/sys/class/backlight/intel_backlight", 
                                 "/sys/class/backlight/acpi_video0", 
                                 "/sys/class/backlight/rpi_backlight",
                                 "/sys/class/backlight/amdgpu_bl0"]:
                if os.path.exists(backlight_dir):
                    try:
                        print(f"[SCREEN] Found backlight directory: {backlight_dir}")
                        brightness_file = os.path.join(backlight_dir, "brightness")
                        print(f"[SCREEN] Setting brightness to 0")
                        with open(brightness_file, "w") as f:
                            f.write("0")
                        success_methods.append(f"backlight({os.path.basename(backlight_dir)})")
                    except (IOError, PermissionError) as e:
                        print(f"[SCREEN] Backlight control failed for {backlight_dir}: {str(e)}")
            
            # 2. Try using vcgencmd for Raspberry Pi
            try:
                print("[SCREEN] Trying vcgencmd method")
                result = subprocess.run(
                    ["vcgencmd", "display_power", "0"], 
                    stderr=subprocess.PIPE, 
                    stdout=subprocess.PIPE, 
                    check=False,
                    timeout=2
                )
                print(f"[SCREEN] vcgencmd result: {result.stdout.decode() if result.stdout else 'No output'}")
                success_methods.append("vcgencmd")
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                print(f"[SCREEN] vcgencmd method failed: {str(e)}")
            
            # 3. Try DDC/CI control if available (external monitors)
            try:
                print("[SCREEN] Trying ddcutil method")
                # Find available displays
                result = subprocess.run(
                    ["ddcutil", "detect"], 
                    stderr=subprocess.PIPE, 
                    stdout=subprocess.PIPE, 
                    check=False,
                    timeout=5
                )
                if "Display" in result.stdout.decode():
                    # Turn off all displays
                    subprocess.run(
                        ["ddcutil", "setvcp", "D6", "0"], 
                        stderr=subprocess.PIPE, 
                        stdout=subprocess.PIPE, 
                        check=False,
                        timeout=2
                    )
                    success_methods.append("ddcutil")
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                print(f"[SCREEN] ddcutil method failed: {str(e)}")
            
            # 4. X11 methods if we have X11
            if has_x11:
                # 4a. DPMS method with proper environment
                try:
                    print("[SCREEN] Trying xset method")
                    result = subprocess.run(
                        ["xset", "dpms", "force", "off"], 
                        env=env,
                        stderr=subprocess.PIPE, 
                        stdout=subprocess.PIPE, 
                        check=False,
                        timeout=2
                    )
                    print(f"[SCREEN] xset result: {result.returncode}")
                    success_methods.append("DPMS")
                except subprocess.SubprocessError as e:
                    print(f"[SCREEN] DPMS off failed: {str(e)}")
                
                # 4b. xrandr method - disable all outputs
                try:
                    print("[SCREEN] Trying xrandr method")
                    # Get list of connected outputs
                    result = subprocess.run(
                        ["xrandr", "--query"], 
                        env=env,
                        capture_output=True, 
                        text=True, 
                        check=False,
                        timeout=2
                    )
                    print(f"[SCREEN] xrandr outputs: {result.stdout[:200]}...")
                    
                    outputs = []
                    for line in result.stdout.splitlines():
                        if " connected " in line:
                            output_name = line.split()[0]
                            outputs.append(output_name)
                    
                    print(f"[SCREEN] Found outputs: {outputs}")
                    
                    # Turn off each output
                    for output in outputs:
                        print(f"[SCREEN] Disabling output: {output}")
                        result = subprocess.run(
                            ["xrandr", "--output", output, "--off"], 
                            env=env,
                            stderr=subprocess.PIPE, 
                            stdout=subprocess.PIPE, 
                            check=False,
                            timeout=2
                        )
                        print(f"[SCREEN] xrandr result for {output}: {result.returncode}")
                    if outputs:
                        success_methods.append("xrandr")
                except subprocess.SubprocessError as e:
                    print(f"[SCREEN] xrandr off failed: {str(e)}")
        
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