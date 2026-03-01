#!/usr/bin/env python3
"""
PowerLink test script — talks to BeolinkPC2 (0cd4:0101).

Protocol details derived from libpc2 (GPL-3.0) by Tore Sinding Bekkedal;
no source code was copied. See https://github.com/toresbe/libpc2
Volume hard-capped at 40%.
"""

import usb.core
import usb.util
import time
import sys

VENDOR_ID = 0x0cd4
PRODUCT_ID = 0x0101

EP_OUT = 0x01
EP_IN = 0x81

VOL_CAP = 40  # absolute max volume byte we'll send

dev = None


def send(msg):
    """Send a framed message to PC2."""
    telegram = [0x60, len(msg)] + list(msg) + [0x61]
    hexstr = " ".join(f"{x:02X}" for x in telegram)
    print(f"  TX: {hexstr}")
    try:
        dev.write(EP_OUT, telegram, 0)
    except usb.core.USBError as e:
        print(f"  USB write error: {e}")


def drain(timeout_ms=200):
    """Read and display any pending data."""
    count = 0
    while True:
        try:
            data = dev.read(EP_IN, 1024, timeout=timeout_ms)
            if data:
                hexstr = " ".join(f"{x:02X}" for x in data)
                print(f"  RX: {hexstr}")
                count += 1
        except usb.core.USBTimeoutError:
            break
        except usb.core.USBError as e:
            print(f"  USB read error: {e}")
            break
    if count:
        print(f"  (drained {count} msg)")


# --- Commands matching libpc2 mixer.cpp ---

def cmd_speaker_mute(muted):
    """0xEA 0x80=mute, 0x81=unmute"""
    val = 0x80 if muted else 0x81
    print(f">> Speaker {'mute' if muted else 'unmute'}")
    send([0xEA, val])
    time.sleep(0.05)
    drain()


def cmd_speaker_power(on):
    """Power on: 0xEA 0xFF then unmute. Power off: mute then 0xEA 0x00.
    libpc2: 'I have observed the PC2 crashing very hard if this is fudged.'"""
    if on:
        print(">> Speaker power ON")
        send([0xEA, 0xFF])
        time.sleep(0.05)
        cmd_speaker_mute(False)
    else:
        print(">> Speaker power OFF")
        cmd_speaker_mute(True)
        time.sleep(0.05)
        send([0xEA, 0x00])
    time.sleep(0.05)
    drain()


def cmd_routing(local=False, from_ml=False, distribute=False):
    """Send routing state per libpc2 logic."""
    muted_byte = 0x00
    if not (distribute or local):
        muted_byte = 0x01

    dist_byte = 0x01 if distribute else 0x00

    if local and from_ml:
        locally = 0x03
    elif from_ml:
        locally = 0x04
    elif local:
        locally = 0x01
    else:
        locally = 0x00

    print(f">> Routing: local={local} from_ml={from_ml} dist={distribute}")
    print(f"   0xE7 [{muted_byte:02X}], 0xE5 [{locally:02X} {dist_byte:02X} 00 {muted_byte:02X}]")
    send([0xE7, muted_byte])
    time.sleep(0.02)
    send([0xE5, locally, dist_byte, 0x00, muted_byte])
    time.sleep(0.1)
    drain()


def cmd_set_params(volume, treble=0, bass=0, balance=0, loudness=False):
    """0xE3: set volume/treble/bass/balance/loudness directly."""
    volume = max(0, min(VOL_CAP, volume))
    vol_byte = volume | (0x80 if loudness else 0x00)
    print(f">> Set params: vol={volume} treble={treble} bass={bass} bal={balance} loud={loudness}")
    send([0xE3, vol_byte, bass & 0xFF, treble & 0xFF, balance & 0xFF])
    time.sleep(0.1)
    drain()


def cmd_adjust_volume(steps):
    """0xEB step volume up/down (capped)."""
    direction = 0x80 if steps > 0 else 0x81
    n = abs(steps)
    print(f">> Volume {'up' if steps > 0 else 'down'} {n} step(s)")
    for _ in range(n):
        send([0xEB, direction])
        time.sleep(0.02)
    drain()


def cmd_source():
    """0xE4 0x01 activate source (from our original code, not in libpc2)."""
    print(">> Activate source (0xE4 0x01)")
    send([0xE4, 0x01])
    time.sleep(0.1)
    drain()


# --- Compound sequences ---

def cmd_audio_on(vol=30):
    """beoported startup: transmit_locally → speaker_power → set_parameters"""
    print(f"== AUDIO ON (vol={vol}) ==")
    cmd_routing(local=True)
    time.sleep(0.05)
    cmd_speaker_power(True)
    time.sleep(0.05)
    cmd_set_params(vol)
    print("== DONE ==")


def cmd_audio_off():
    """beoported shutdown: transmit_locally(false) → speaker_power(false)"""
    print("== AUDIO OFF ==")
    cmd_routing(local=False)
    time.sleep(0.05)
    cmd_speaker_power(False)
    print("== DONE ==")


def print_help():
    print("""
Commands (based on libpc2):
  on [vol]      Audio on: route local → power on → set_params (default vol=30)
  off           Audio off: route off → power off
  power 1/0     Speaker power on/off
  mute / unmute Speaker mute control
  route [opts]  Routing: 'local', 'ml', 'both', 'off' (default: local)
  params <vol> [treble bass balance loud]  Set parameters via 0xE3
  vol+ [n]      Step volume up
  vol- [n]      Step volume down
  source        Activate source (0xE4, not in libpc2)
  init          Init device (0xF1 + 0x80)
  filter [mode] Address filter: 'all', 'beoport', 'master' (default: beoport)
  hex <bytes>   Send arbitrary framed message
  read          Read pending data
  help          This help
  quit          Exit
""")


def main():
    global dev

    print(f"PowerLink test — BeolinkPC2 ({VENDOR_ID:04x}:{PRODUCT_ID:04x})")
    print(f"Volume cap: {VOL_CAP}\n")

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
        if dev is None:
            print("ERROR: Device gone after reset!")
            sys.exit(1)
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except usb.core.USBError:
            pass
        dev.set_configuration()

    usb.util.claim_interface(dev, 0)
    print("PC2 opened.\n")

    # Init (same as libpc2 pc2device.cpp init)
    print(">> Init")
    send([0xF1])
    time.sleep(0.1)
    drain()
    send([0x80, 0x01, 0x00])
    time.sleep(0.1)
    drain()

    # Set address filter — beoport mode (per libpc2)
    print(">> Address filter (beoport)")
    send([0xF6, 0x00, 0x82, 0x80, 0x83])
    time.sleep(0.1)
    drain()

    speakers_on = False
    print_help()

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        if cmd == "quit":
            break
        elif cmd == "help":
            print_help()
        elif cmd == "on":
            vol = int(parts[1]) if len(parts) > 1 else 30
            vol = min(vol, VOL_CAP)
            cmd_audio_on(vol)
            speakers_on = True
        elif cmd == "off":
            cmd_audio_off()
            speakers_on = False
        elif cmd == "power":
            on = parts[1] in ("1", "on", "true") if len(parts) > 1 else True
            cmd_speaker_power(on)
            speakers_on = on
        elif cmd == "mute":
            cmd_speaker_mute(True)
        elif cmd == "unmute":
            cmd_speaker_mute(False)
        elif cmd == "route":
            mode = parts[1] if len(parts) > 1 else "local"
            if mode == "local":
                cmd_routing(local=True)
            elif mode == "ml":
                cmd_routing(from_ml=True)
            elif mode == "both":
                cmd_routing(local=True, from_ml=True)
            elif mode == "off":
                cmd_routing()
            else:
                print(f"  Unknown route mode: {mode}")
        elif cmd == "params":
            vol = min(int(parts[1]), VOL_CAP) if len(parts) > 1 else 30
            treble = int(parts[2]) if len(parts) > 2 else 0
            bass = int(parts[3]) if len(parts) > 3 else 0
            bal = int(parts[4]) if len(parts) > 4 else 0
            loud = parts[5].lower() in ("1", "true", "on") if len(parts) > 5 else False
            cmd_set_params(vol, treble, bass, bal, loud)
        elif cmd == "vol+":
            cmd_adjust_volume(int(parts[1]) if len(parts) > 1 else 1)
        elif cmd == "vol-":
            cmd_adjust_volume(-(int(parts[1]) if len(parts) > 1 else 1))
        elif cmd == "source":
            cmd_source()
        elif cmd == "init":
            send([0xF1])
            time.sleep(0.1)
            drain()
            send([0x80, 0x01, 0x00])
            time.sleep(0.1)
            drain()
        elif cmd == "filter":
            mode = parts[1] if len(parts) > 1 else "beoport"
            if mode == "beoport":
                send([0xF6, 0x00, 0x82, 0x80, 0x83])
            elif mode == "master":
                send([0xF6, 0x10, 0xC1, 0x80, 0x83, 0x05, 0x00, 0x00])
            elif mode == "all":
                send([0xF6, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
            else:
                print(f"  Unknown filter: {mode}")
            time.sleep(0.1)
            drain()
        elif cmd == "hex" and len(parts) > 1:
            try:
                data = [int(x, 16) for x in parts[1:]]
                send(data)
                time.sleep(0.1)
                drain()
            except ValueError:
                print("  Invalid hex")
        elif cmd == "read":
            drain(1000)
        else:
            print(f"  Unknown: {cmd}")

    if speakers_on:
        print("\nPowering off before exit...")
        cmd_audio_off()

    usb.util.release_interface(dev, 0)
    print("Done.")


if __name__ == "__main__":
    main()
