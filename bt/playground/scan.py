import asyncio
from bleak import BleakClient, BleakScanner

async def main():
    # replace with your remote’s MAC
    address = "48:D0:CF:BD:CE:35"
    device = await BleakScanner.find_device_by_address(address, timeout=5.0)
    if not device:
        print("Remote not found")
        return

    async with BleakClient(device) as cli:
        for svc in cli.services:
            print(f"[Service] {svc.uuid}:")
            for char in svc.characteristics:
                print(f"  [Char ] {char.uuid} — properties: {char.properties}")

asyncio.run(main())
