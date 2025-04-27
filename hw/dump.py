#!/usr/bin/env python3
import hid, time, sys

VID = 0x0cd4
PID = 0x1112

# button code → name
BTN_MAP = {
    0x20: 'left',
    0x10: 'right',
    0x40: 'go',
    0x80: 'power',
}

def parse_report(rep):
    """
    rep: list of ints:
      rep[0] = nav wheel delta (0x01=clock speed1, 0x03=clock speed3; 0xFF=counter speed1, 0xFE=counter speed2)
      rep[1] = volume wheel delta, same logic
      rep[2] = laser absolute position (0–100)
      rep[3] = button code (0x20/0x10/0x40/0x80)
    Returns: (nav_evt, vol_evt, btn_evt, laser_pos)
    """
    # NAV
    nav_evt = None
    nav = rep[0]
    if nav != 0:
        if nav < 0x80:
            direction = 'clock'
            speed = nav
        else:
            direction = 'counter'
            speed = 256 - nav
        nav_evt = {'direction': direction, 'speed': speed}
    # VOLUME
    vol_evt = None
    vol = rep[1]
    if vol != 0:
        if vol < 0x80:
            direction = 'clock'
            speed = vol
        else:
            direction = 'counter'
            speed = 256 - vol
        vol_evt = {'direction': direction, 'speed': speed}

    # BUTTON
    btn_evt = None
    btn = rep[3]
    if btn in BTN_MAP:
        btn_evt = {'button': BTN_MAP[btn]}

    # LASER
    laser_pos = rep[2]

    return nav_evt, vol_evt, btn_evt, laser_pos

# ——————————————————————————————————————

dev = hid.device()
dev.open(VID, PID)
print("Opened")
dev.set_nonblocking(True)
print("Configured")

last_laser = None
first = True

try:
    while True:
        rpt = dev.read(64, 50)  # up to 50ms timeout
        if rpt:
            rep = list(rpt)
            nav_evt, vol_evt, btn_evt, laser_pos = parse_report(rep)

            if nav_evt:
                print(f"NAV     → {nav_evt}")
                if(nav_evt['speed'] > 2):
                  dev.write([0x41, 0x00])
            if vol_evt:
                print(f"VOLUME  → {vol_evt}")
            if btn_evt:
                print(f"BUTTON  → {btn_evt}")

            # only emit LASER when it first appears or changes
            if first or laser_pos != last_laser:
                print(f"LASER   → {{'position': {laser_pos}}}")
                last_laser = laser_pos
                first = False

        time.sleep(0.01)

except KeyboardInterrupt:
    print("\nExiting.")
    dev.close()
