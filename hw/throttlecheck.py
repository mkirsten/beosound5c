#!/usr/bin/env python3
import subprocess
import time

def get_throttled_flags():
    out = subprocess.check_output(['vcgencmd', 'get_throttled']).decode().strip()
    hex_val = out.split('=')[1]
    return int(hex_val, 16)

while True:
    flags = get_throttled_flags()
    now = time.strftime('%Y-%m-%d %H:%M:%S')

    status = []

    if flags & 0x1:
        status.append("UNDER-VOLTAGE")
    if flags & 0x2:
        status.append("FREQ CAPPED")
    if flags & 0x4:
        status.append("THROTTLED")
    if flags & 0x8:
        status.append("TEMP LIMIT")

    if status:
        print(f"[{now}] ⚠️  ACTIVE ISSUES: {', '.join(status)}")
    else:
        print(f"[{now}] ✅ All OK")

    time.sleep(2)
