import asyncio
import websockets
import json

messages = [
    {"desc": "Click", "msg": {"type": "command", "command": "click", "params": {}}},
    {"desc": "LED ON", "msg": {"type": "command", "command": "led", "params": {"mode": "on"}}},
    {"desc": "LED BLINK", "msg": {"type": "command", "command": "led", "params": {"mode": "blink"}}},
    {"desc": "LED OFF", "msg": {"type": "command", "command": "led", "params": {"mode": "off"}}},
    {"desc": "Backlight ON", "msg": {"type": "command", "command": "backlight", "params": {"on": True}}},
    {"desc": "Backlight OFF", "msg": {"type": "command", "command": "backlight", "params": {"on": False}}},
]

async def main():
    ws_url = "ws://localhost:8765"  # Change if needed

    print("Connecting to WebSocket server...")
    async with websockets.connect(ws_url) as ws:
        print("Connected!\n")
        while True:
            print("Select a message to send:")
            for i, item in enumerate(messages):
                print(f"{i}: {item['desc']}")
            print("q: Quit")
            choice = input("Enter number: ").strip()
            if choice.lower() == 'q':
                break
            try:
                idx = int(choice)
                msg = messages[idx]["msg"]
                await ws.send(json.dumps(msg))
                print(f"Sent: {msg}")
            except (ValueError, IndexError):
                print("Invalid choice. Try again.")
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())