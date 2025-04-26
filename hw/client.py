#!/usr/bin/env python3
import curses, threading, time, json
from websocket import WebSocketApp

WS_URL = "ws://localhost:8765"
HIGHLIGHT_DURATION = 0.2  # seconds

# shared state
state = {
    'nav':      None,        # {'direction','speed'}
    'volume':   None,        # same shape
    'laser':    0,           # 0..100
    'buttons': {             # momentary highlights
        'left': False,
        'right': False,
        'go': False,
        'power': False
    },
    'highlight_ends': {}
}

BTN_MAP = {'left':'[<]', 'right':'[>]', 'go':'[GO]', 'power':'[PWR]'}

def on_message(ws, message):
    evt = json.loads(message)
    t, d = evt['type'], evt['data']
    now = time.time()
    if t == 'nav':
        state['nav'] = d
    elif t == 'volume':
        state['volume'] = d
    elif t == 'laser':
        state['laser'] = d['position']
    elif t == 'button' and d['button'] in state['buttons']:
        k = d['button']
        state['buttons'][k] = True
        state['highlight_ends'][k] = now + HIGHLIGHT_DURATION

def ws_thread():
    ws = WebSocketApp(WS_URL, on_message=on_message)
    ws.run_forever()

def draw_static(stdscr, cy, cx, slider_y, slider_x, length):
    # Draw a bigger wheel (7 rows × ~12 cols)
    stdscr.addstr(cy-3, cx-7, "    ______    ")
    stdscr.addstr(cy-2, cx-7, "   /      \\   ")
    stdscr.addstr(cy-1, cx-7, "  /        \\  ")
    stdscr.addstr(cy,   cx-7, " |          | ")
    stdscr.addstr(cy+1, cx-7, " |          | ")
    stdscr.addstr(cy+2, cx-7, "  \\        /  ")
    stdscr.addstr(cy+3, cx-7, "   \\______/   ")

    # Slider track (length chars) below the wheel
    stdscr.addstr(slider_y, slider_x, "[" + "-"*length + "]")

    # Draw static placeholders for buttons (they'll be highlighted dynamically)
    stdscr.addstr(cy+1, cx-2, BTN_MAP['left'])
    stdscr.addstr(cy+1, cx+4, BTN_MAP['right'])
    stdscr.addstr(cy-1, cx-1, BTN_MAP['go'])
    stdscr.addstr(1, curses.COLS-8,    BTN_MAP['power'])

def draw_buttons(stdscr, cy, cx):
    # Update highlight on left, right, go, power
    for key, (dy, dx) in {
        'left':  (1, cx-2),
        'right': (1, cx+4),
        'go':    (-1, cx-1),
        'power': (1, curses.COLS-8)
    }.items():
        attr = curses.A_REVERSE if state['buttons'][key] else curses.A_NORMAL
        stdscr.addstr(cy+dy, dx, BTN_MAP[key], attr)

def draw_nav_vol(stdscr, h):
    # Only rewrite the single text line at bottom
    nav = state['nav']
    vol = state['volume']
    msg = ""
    if nav:
        msg += f"Nav:{nav['direction'][:3]}@{nav['speed']:<3} "
    else:
        msg += " "*12
    if vol:
        msg += f"Vol:{vol['direction'][:3]}@{vol['speed']:<3}"
    stdscr.addstr(h-2, 2, msg)

def draw_laser(stdscr, slider_y, slider_x, last_pos, length):
    raw = state['laser']
    # scale 0–100 → 0–(length-1)
    pos = int(raw * (length - 1) / 100)
    pos = max(0, min(length-1, pos))

    # clear old knob
    if 0 <= last_pos < length:
        stdscr.addch(slider_y, slider_x+1+last_pos, "-")
    # draw new knob
    stdscr.addch(slider_y, slider_x+1+pos, "|", curses.A_BOLD)
    return pos

def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)

    h, w = stdscr.getmaxyx()
    # center for the wheel
    cy = h//2
    cx = w//2

    # configure a wider slider
    slider_length = 40
    slider_y = cy + 5
    slider_x = cx - slider_length//2

    # draw all the static parts once
    draw_static(stdscr, cy, cx, slider_y, slider_x, slider_length)
    stdscr.refresh()

    # start WebSocket in background
    threading.Thread(target=ws_thread, daemon=True).start()

    last_laser = state['laser']
    try:
        while True:
            now = time.time()
            # expire highlights
            for k, end in list(state['highlight_ends'].items()):
                if now > end:
                    state['buttons'][k] = False
                    del state['highlight_ends'][k]

            # update only the dynamic parts
            draw_buttons(stdscr, cy, cx)
            draw_nav_vol(stdscr, h)
            last_laser = draw_laser(stdscr, slider_y, slider_x, last_laser, slider_length)

            stdscr.refresh()

            # exit on 'q'
            if stdscr.getch() in (ord('q'), ord('Q')):
                break

            time.sleep(0.02)

    except KeyboardInterrupt:
        pass

if __name__=="__main__":
    curses.wrapper(main)
