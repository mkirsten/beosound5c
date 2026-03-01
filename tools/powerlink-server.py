#!/usr/bin/env python3
"""
PowerLink HTTP control server — holds the PC2 USB device open
and exposes mixer commands over HTTP on port 8780.

Protocol details derived from libpc2 (GPL-3.0) by Tore Sinding Bekkedal;
no source code was copied. See https://github.com/toresbe/libpc2

Methods:
  power_on(vol)  — activate source, route, power on, set initial vol via 0xE3
  power_off()    — route off, mute, power off
  set_volume(v)  — step from current to target via 0xEB, with device feedback
  get_volume()   — return current volume (tracked + device-confirmed)
  vol_up(n)      — step up n
  vol_down(n)    — step down n

Endpoints:
  GET  /status
  POST /on              {"vol": 30}
  POST /off
  POST /volume          {"vol": 40}
  POST /volume/up       {"steps": 3}
  POST /volume/down     {"steps": 3}
  GET  /volume
  POST /power           {"on": true}
  POST /mute            {"muted": true}
  POST /route           {"local": true, "from_ml": false, "distribute": false}
  POST /source
  POST /params          {"vol": 30, "bass": 0, "treble": 0, "bal": 0, "loud": false}
  POST /hex             {"bytes": "E3 1E 00 00 00"}
  GET  /read
"""

import usb.core
import usb.util
import time
import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

VENDOR_ID = 0x0cd4
PRODUCT_ID = 0x0101
EP_OUT = 0x01
EP_IN = 0x81
PORT = 8780
VOL_CAP = 70
DEFAULT_VOL = 30

dev = None

state = {
    "speakers_on": False,
    "muted": True,
    "volume": 0,
    "volume_confirmed": 0,  # last volume read from device
    "bass": 0,
    "treble": 0,
    "balance": 0,
    "loudness": False,
    "routing_local": False,
    "routing_from_ml": False,
    "routing_distribute": False,
    "source_active": False,
}


# --- Low-level USB ---

def send(msg):
    """Send framed message. Returns TX hex string."""
    telegram = [0x60, len(msg)] + list(msg) + [0x61]
    dev.write(EP_OUT, telegram, 0)
    return " ".join(f"{x:02X}" for x in telegram)


def read_one(timeout_ms=100):
    """Read one message, parse mixer state if applicable. Returns raw bytes or None."""
    try:
        data = list(dev.read(EP_IN, 1024, timeout=timeout_ms))
        if len(data) >= 5:
            msg_type = data[2]
            if msg_type in (0x03, 0x1D):
                # Mixer state: byte[3] = vol (bit7=loudness), [4]=bass, [5]=treble, [6]=balance
                state["volume_confirmed"] = data[3] & 0x7F
                state["loudness"] = bool(data[3] & 0x80)
                if len(data) >= 7:
                    state["bass"] = _signed(data[4])
                    state["treble"] = _signed(data[5])
                    state["balance"] = _signed(data[6])
        return data
    except usb.core.USBTimeoutError:
        return None
    except usb.core.USBError:
        return None


def _signed(b):
    """Convert unsigned byte to signed int8."""
    return b if b < 128 else b - 256


def drain():
    """Read all pending messages, parsing mixer state. Returns list of hex strings."""
    msgs = []
    while True:
        data = read_one(100)
        if data is None:
            break
        msgs.append(" ".join(f"{x:02X}" for x in data))
    return msgs


# --- Mixer commands (per libpc2) ---

def speaker_mute(muted):
    state["muted"] = muted
    return send([0xEA, 0x80 if muted else 0x81])


def speaker_power(on):
    """Power on: 0xEA 0xFF then unmute. Off: mute then 0xEA 0x00.
    libpc2: crashing observed if sequence is wrong."""
    txs = []
    if on:
        txs.append(send([0xEA, 0xFF]))
        time.sleep(0.05)
        txs.append(speaker_mute(False))
        state["speakers_on"] = True
    else:
        txs.append(speaker_mute(True))
        time.sleep(0.05)
        txs.append(send([0xEA, 0x00]))
        state["speakers_on"] = False
    return txs


def set_routing(local=False, from_ml=False, distribute=False):
    muted_byte = 0x00 if (distribute or local) else 0x01
    dist_byte = 0x01 if distribute else 0x00
    if local and from_ml:
        locally = 0x03
    elif from_ml:
        locally = 0x04
    elif local:
        locally = 0x01
    else:
        locally = 0x00
    state["routing_local"] = local
    state["routing_from_ml"] = from_ml
    state["routing_distribute"] = distribute
    tx1 = send([0xE7, muted_byte])
    time.sleep(0.02)
    tx2 = send([0xE5, locally, dist_byte, 0x00, muted_byte])
    return [tx1, tx2]


def activate_source():
    state["source_active"] = True
    return send([0xE4, 0x01])


def set_params(vol, bass=0, treble=0, balance=0, loudness=False):
    """0xE3 — set mixer parameters. Only effective at power-on/init."""
    vol = max(0, min(VOL_CAP, vol))
    vol_byte = vol | (0x80 if loudness else 0x00)
    state["volume"] = vol
    state["bass"] = bass
    state["treble"] = treble
    state["balance"] = balance
    state["loudness"] = loudness
    return send([0xE3, vol_byte, bass & 0xFF, treble & 0xFF, balance & 0xFF])


# --- High-level adapter methods ---

def power_on(vol=None):
    """Full audio on: source → route → power → set_params.
    0xE3 sets initial volume at power-on."""
    if vol is None:
        vol = DEFAULT_VOL
    vol = max(0, min(VOL_CAP, vol))
    txs = []
    txs.append(activate_source())
    time.sleep(0.1)
    txs.extend(set_routing(local=True))
    time.sleep(0.1)
    txs.extend(speaker_power(True))
    time.sleep(0.05)
    txs.append(set_params(vol))
    time.sleep(0.1)
    drain()  # consume init feedback
    state["volume"] = vol
    state["volume_confirmed"] = vol
    return txs


def power_off():
    """Full audio off: route off → power off."""
    txs = []
    txs.extend(set_routing(local=False))
    time.sleep(0.05)
    txs.extend(speaker_power(False))
    state["source_active"] = False
    state["volume"] = 0
    drain()
    return txs


def set_volume(target):
    """Set volume to absolute value using 0xEB steps from current position."""
    target = max(0, min(VOL_CAP, target))
    current = state["volume"]
    diff = target - current
    if diff == 0:
        return {"steps": 0, "volume": current}

    direction = 0x80 if diff > 0 else 0x81
    steps = abs(diff)
    for i in range(steps):
        send([0xEB, direction])
        time.sleep(0.02)

    state["volume"] = target
    # Read feedback to confirm
    time.sleep(0.05)
    drain()
    return {"steps": steps, "direction": "up" if diff > 0 else "down", "volume": target,
            "volume_confirmed": state["volume_confirmed"]}


def get_volume():
    """Return current tracked volume and device-confirmed volume."""
    drain()  # process any pending mixer state messages
    return {"volume": state["volume"], "volume_confirmed": state["volume_confirmed"]}


def vol_up(steps=1):
    """Step volume up, respecting cap."""
    target = min(state["volume"] + steps, VOL_CAP)
    return set_volume(target)


def vol_down(steps=1):
    """Step volume down."""
    target = max(state["volume"] - steps, 0)
    return set_volume(target)


# --- HTTP server ---

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  {args[0]}")

    def _respond(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_GET(self):
        if self.path == "/status":
            drain()
            self._respond({"state": state})
        elif self.path == "/volume":
            self._respond(get_volume())
        elif self.path == "/read":
            self._respond({"rx": drain()})
        else:
            self._respond({"error": "not found"}, 404)

    def do_POST(self):
        body = self._body()
        try:
            if self.path == "/on":
                vol = body.get("vol", DEFAULT_VOL)
                tx = power_on(vol)
                self._respond({"ok": True, "tx": tx, "state": state})

            elif self.path == "/off":
                tx = power_off()
                self._respond({"ok": True, "tx": tx, "state": state})

            elif self.path == "/volume":
                vol = body.get("vol", state["volume"])
                result = set_volume(vol)
                self._respond({"ok": True, **result, "state": state})

            elif self.path == "/volume/up":
                steps = body.get("steps", 1)
                result = vol_up(steps)
                self._respond({"ok": True, **result, "state": state})

            elif self.path == "/volume/down":
                steps = body.get("steps", 1)
                result = vol_down(steps)
                self._respond({"ok": True, **result, "state": state})

            elif self.path == "/power":
                on = body.get("on", True)
                if on:
                    tx = speaker_power(True)
                else:
                    tx = speaker_power(False)
                self._respond({"ok": True, "tx": tx, "state": state})

            elif self.path == "/mute":
                muted = body.get("muted", True)
                tx = speaker_mute(muted)
                self._respond({"ok": True, "tx": tx, "state": state})

            elif self.path == "/route":
                tx = set_routing(
                    local=body.get("local", False),
                    from_ml=body.get("from_ml", False),
                    distribute=body.get("distribute", False),
                )
                self._respond({"ok": True, "tx": tx, "state": state})

            elif self.path == "/source":
                tx = activate_source()
                self._respond({"ok": True, "tx": tx, "state": state})

            elif self.path == "/params":
                tx = set_params(
                    vol=body.get("vol", state["volume"]),
                    bass=body.get("bass", state["bass"]),
                    treble=body.get("treble", state["treble"]),
                    balance=body.get("bal", state["balance"]),
                    loudness=body.get("loud", state["loudness"]),
                )
                self._respond({"ok": True, "tx": tx, "state": state, "rx": drain()})

            elif self.path == "/hex":
                hexstr = body.get("bytes", "")
                data = [int(x, 16) for x in hexstr.split()]
                tx = send(data)
                time.sleep(0.1)
                self._respond({"ok": True, "tx": tx, "rx": drain()})

            else:
                self._respond({"error": "not found"}, 404)

        except Exception as e:
            self._respond({"error": str(e)}, 500)


def main():
    global dev

    print(f"PowerLink server — BeolinkPC2 ({VENDOR_ID:04x}:{PRODUCT_ID:04x})")
    print(f"Volume cap: {VOL_CAP}, default: {DEFAULT_VOL}")

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("ERROR: BeolinkPC2 not found!")
        sys.exit(1)

    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except usb.core.USBError:
        pass

    try:
        dev.set_configuration()
    except usb.core.USBError:
        dev.reset()
        time.sleep(1)
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except usb.core.USBError:
            pass
        dev.set_configuration()

    usb.util.claim_interface(dev, 0)
    print("PC2 opened.")

    # Init (per libpc2 pc2device.cpp)
    send([0xF1])
    time.sleep(0.1)
    send([0x80, 0x01, 0x00])
    time.sleep(0.1)
    # Address filter — beoport mode (per libpc2)
    send([0xF6, 0x00, 0x82, 0x80, 0x83])
    time.sleep(0.1)
    drain()
    print("PC2 initialized.\n")

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Listening on :{PORT}")
    print(f"  POST /on          {{\"vol\":30}}")
    print(f"  POST /off")
    print(f"  POST /volume      {{\"vol\":40}}")
    print(f"  POST /volume/up   {{\"steps\":3}}")
    print(f"  POST /volume/down {{\"steps\":3}}")
    print(f"  GET  /volume")
    print(f"  GET  /status")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        if state["speakers_on"]:
            power_off()
        usb.util.release_interface(dev, 0)
        print("Done.")


if __name__ == "__main__":
    main()
