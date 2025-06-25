import asyncio
from bleak import BleakScanner

# Your remote’s MAC address
ADDRESS = "48:D0:CF:BD:CE:35"

def detection_callback(device, advertisement_data):
    if device.address.upper() == ADDRESS:
        # manufacturer_data is a dict: {company_id: bytes}
        for mfg_id, payload in advertisement_data.manufacturer_data.items():
            # print the raw bytes as hex
            print(f"Press → company 0x{mfg_id:04X}: {payload.hex()}")

async def main():
    scanner = BleakScanner()
    scanner.register_detection_callback(detection_callback)
    print(f"🔍 Scanning for {ADDRESS}, press buttons now…")
    await scanner.start()
    try:
        # Run indefinitely. Ctrl-C to stop.
        while True:
            await asyncio.sleep(1)
    finally:
        await scanner.stop()

if __name__ == "__main__":
    asyncio.run(main())
