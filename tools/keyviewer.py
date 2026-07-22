#!/usr/bin/env python3
"""
keyviewer — show the keys and key-combos you type, by name.

Puts the terminal into raw mode and decodes each keypress (or escape
sequence) into a human-readable name: printable chars, control keys,
arrows, navigation keys, and F1-F12 — including modifier combinations
(Shift / Ctrl / Alt / Meta) with the F-keys and others.

It only captures input typed into THIS terminal window — it is not a
system-wide/background key logger.

Run:   python3 tools/keyviewer.py
Quit:  Ctrl-C  (or type the word  quit  then Enter... but raw mode has
       no Enter — just hit Ctrl-C)

Note on terminals: what your keyboard actually sends depends on the
terminal emulator. Some combos (e.g. Ctrl-F5) are indistinguishable or
swallowed by the terminal / OS and will never reach this program. Try
it in the terminal you actually care about.
"""

import sys
import os
import select
import termios
import tty

# --- modifier bitmask used by xterm-style CSI ...;<mod> sequences ---
# mod value is 1 + bitmask:  1=Shift  2=Alt  4=Ctrl  8=Meta
def decode_mod(mod):
    if mod is None:
        return []
    bits = mod - 1
    names = []
    if bits & 1:
        names.append("Shift")
    if bits & 2:
        names.append("Alt")
    if bits & 4:
        names.append("Ctrl")
    if bits & 8:
        names.append("Meta")
    return names


# Final byte of "CSI 1 ; mod <final>" sequences -> key name
CSI_FINAL = {
    "A": "Up",
    "B": "Down",
    "C": "Right",
    "D": "Left",
    "H": "Home",
    "F": "End",
    "P": "F1",
    "Q": "F2",
    "R": "F3",
    "S": "F4",
    "Z": "Shift+Tab",
}

# "CSI <num> ; mod ~" sequences -> key name
CSI_TILDE = {
    1: "Home",
    2: "Insert",
    3: "Delete",
    4: "End",
    5: "PageUp",
    6: "PageDown",
    11: "F1",
    12: "F2",
    13: "F3",
    14: "F4",
    15: "F5",
    17: "F6",
    18: "F7",
    19: "F8",
    20: "F9",
    21: "F10",
    23: "F11",
    24: "F12",
    25: "F13",
    26: "F14",
    28: "F15",
    29: "F16",
    31: "F17",
    32: "F18",
    33: "F19",
    34: "F20",
}

# SS3 sequences: ESC O <final>  (F1-F4 in "application" mode)
SS3_FINAL = {
    "P": "F1",
    "Q": "F2",
    "R": "F3",
    "S": "F4",
    "H": "Home",
    "F": "End",
    "A": "Up",
    "B": "Down",
    "C": "Right",
    "D": "Left",
}

CTRL_NAMES = {
    0: "Ctrl+Space (NUL)",
    8: "Backspace (Ctrl+H)",
    9: "Tab",
    10: "Enter (Ctrl+J / LF)",
    13: "Enter (Ctrl+M / CR)",
    27: "Esc",
    127: "Backspace (DEL)",
}


def read_byte(timeout=None):
    """Read one byte; return int or None on timeout."""
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    if not r:
        return None
    b = os.read(sys.stdin.fileno(), 1)
    if not b:
        return None
    return b[0]


def with_mods(mod, base):
    mods = decode_mod(mod)
    return "+".join(mods + [base]) if mods else base


def parse_csi(raw):
    """
    Parse the body of a CSI sequence (everything after ESC [) that we've
    already collected as a string `raw` ending in its final byte.
    Returns a human name.
    """
    final = raw[-1]
    body = raw[:-1]  # parameters, possibly "num;mod"

    params = body.split(";") if body else []
    nums = []
    for p in params:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(None)

    if final == "~":
        num = nums[0] if nums else None
        mod = nums[1] if len(nums) > 1 else None
        name = CSI_TILDE.get(num, f"CSI~{num}")
        return with_mods(mod, name)

    if final in CSI_FINAL:
        # forms: "A", "1;5A" (num=1, mod=5)
        mod = nums[1] if len(nums) > 1 else (nums[0] if nums else None)
        # when only one param it's usually the modifier for the "1;" family;
        # bare "A" has no params
        if len(nums) == 1:
            mod = None if body == "" else nums[0]
        base = CSI_FINAL[final]
        # Shift+Tab already carries its modifier in the name
        if base == "Shift+Tab":
            return base
        return with_mods(mod, base)

    return f"CSI {raw!r}"


def read_escape_sequence():
    """
    We've read ESC (27). Figure out whether it's a lone Esc, Alt+key,
    an SS3 (ESC O ...), or a CSI (ESC [ ...) sequence.
    """
    nxt = read_byte(timeout=0.02)
    if nxt is None:
        return "Esc"

    ch = chr(nxt)

    if ch == "[":
        # CSI: collect until a final byte in @A-Z~ range
        raw = ""
        while True:
            b = read_byte(timeout=0.05)
            if b is None:
                return f"Esc [ {raw!r} (incomplete)"
            c = chr(b)
            raw += c
            if ("@" <= c <= "~") and c not in "0123456789;":
                break
        return parse_csi(raw)

    if ch == "O":
        b = read_byte(timeout=0.05)
        if b is None:
            return "Esc O (incomplete)"
        c = chr(b)
        return SS3_FINAL.get(c, f"SS3 {c!r}")

    # ESC followed by a normal key = Alt+<key>
    if 32 <= nxt < 127:
        return f"Alt+{ch}"
    if nxt < 27:
        return f"Alt+Ctrl+{chr(nxt + 64)}"

    return f"Esc + {nxt}"


def name_for(b):
    if b == 27:
        return read_escape_sequence()
    if b in CTRL_NAMES:
        return CTRL_NAMES[b]
    if b < 27 or b == 28 or b == 29 or b == 30 or b == 31:
        # generic control char -> Ctrl+letter
        return f"Ctrl+{chr(b + 64)}"
    if b == 32:
        return "Space"
    if 33 <= b < 127:
        return f"'{chr(b)}'"
    if b >= 128:
        return f"byte 0x{b:02x} (UTF-8/high)"
    return f"byte {b}"


def main():
    if not sys.stdin.isatty():
        print("keyviewer needs an interactive terminal (a TTY).", file=sys.stderr)
        return 1

    print("keyviewer — press keys to see their names.")
    print("Try: F5, Shift+F5, Ctrl+F12, Alt+F4, arrows, Home/End, Ctrl+A ...")
    print("Quit with Ctrl-C.\n")

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            b = read_byte()
            if b is None:
                continue
            if b == 3:  # Ctrl-C
                break
            name = name_for(b)
            # \r\n because raw mode doesn't translate newlines
            sys.stdout.write(f"  {name}\r\n")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print("\nbye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
