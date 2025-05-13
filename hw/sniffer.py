import usb.core
import usb.util
import time
import threading
import queue
import sys
import json
import websocket
import requests
from datetime import datetime
from collections import defaultdict

# Configuration variables
# Home Assistant webhook and WebSocket URLs
WEBHOOK_URL = "http://homeassistant.local:8123/api/webhook/beosound5c"
WEBSOCKET_URL = "ws://localhost:8765"

# Message processing settings
MESSAGE_TIMEOUT = 2.0  # Discard messages older than 2 seconds
DEDUP_COMMANDS = ["volup", "voldown", "left", "right"]  # Commands to deduplicate

sys.stdout.reconfigure(line_buffering=True)

class MessageQueue:
    """Thread-safe queue with lossy behavior and deduplication."""
    def __init__(self, timeout=MESSAGE_TIMEOUT):
        self.lock = threading.Lock()
        self.queue = []
        self.timeout = timeout
        self.command_counts = defaultdict(int)  # For deduplication
        self.last_message_time = {}  # Track the last message time for each command
    
    def add(self, message):
        """Add a message to the queue with timestamp."""
        with self.lock:
            # Add timestamp to the message
            message['timestamp'] = time.time()
            
            # Check if this message should be deduplicated
            command = message.get('key_name')
            if command in DEDUP_COMMANDS:
                # If we already have this command, update its count
                if command in self.last_message_time:
                    # Check if the existing command is still valid (not timed out)
                    if time.time() - self.last_message_time[command] < self.timeout:
                        # Increment count instead of adding a new message
                        self.command_counts[command] += 1
                        # Find the existing message and update its count
                        for existing_msg in self.queue:
                            if existing_msg.get('key_name') == command:
                                existing_msg['count'] = self.command_counts[command]
                                # Update timestamp to prevent timeout
                                existing_msg['timestamp'] = time.time()
                                return
                
                # If we didn't find an existing message or it timed out, add a new one
                self.last_message_time[command] = time.time()
                self.command_counts[command] = 1
                message['count'] = 1
            
            self.queue.append(message)
    
    def get(self):
        """Get the next valid message from the queue."""
        with self.lock:
            # Discard messages older than timeout
            now = time.time()
            self.queue = [msg for msg in self.queue if now - msg['timestamp'] < self.timeout]
            
            # Return None if queue is empty
            if not self.queue:
                return None
            
            # Return the oldest message
            message = self.queue.pop(0)
            
            # If this was a deduped command, clear its counter when removed
            command = message.get('key_name')
            if command in DEDUP_COMMANDS:
                # Only clear if this was the last instance of this command
                if all(msg.get('key_name') != command for msg in self.queue):
                    self.command_counts[command] = 0
                    self.last_message_time.pop(command, None)
            
            return message
    
    def size(self):
        """Return the current size of the queue."""
        with self.lock:
            return len(self.queue)


def shouldSendWebhook(data):
    return True

def shouldSendWebsocket(data):
    return True

class PC2Device:
    # B&O PC2 device identifiers
    VENDOR_ID = 0x0cd4
    PRODUCT_ID = 0x0101

    # USB endpoints
    EP_OUT = 0x01  # For sending data to device
    EP_IN = 0x81   # For receiving data from device (LIBUSB_ENDPOINT_IN | 1)

    # Address mask types
    ADDRESS_MASK_AUDIO_MASTER = 1
    ADDRESS_MASK_BEOPORT = 2
    ADDRESS_MASK_PROMISC = 3

    def __init__(self):
        self.dev = None
        self.running = False
        self.message_queue = MessageQueue()
        self.sniffer_thread = None
        self.sender_thread = None
        self.ws = None

    def open(self):
        """Find and open the PC2 device"""
        # Find the PC2 device
        self.dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)

        if self.dev is None:
            raise Exception("PC2 not found")

        # Detach kernel driver if active
        if self.dev.is_kernel_driver_active(0):
            self.dev.detach_kernel_driver(0)

        # Set configuration
        self.dev.set_configuration()

        # Claim interface
        usb.util.claim_interface(self.dev, 0)

        print("Opened PC2 device")

    def init(self):
        """Initialize the device with required commands"""
        # Send initial commands same as in C++ code
        self.send_message([0xf1])
        time.sleep(0.1)  # Small delay between commands
        self.send_message([0x80, 0x01, 0x00])

    def send_message(self, message):
        """Send a message to the device"""
        # Format the message as in the C++ code
        # Start of transmission + length + message + end of transmission
        telegram = [0x60, len(message)] + list(message) + [0x61]

        # Debug output
        debug_str = "Sending: " + " ".join([f"{x:02X}" for x in telegram])
        print(debug_str)

        # Send the message
        self.dev.write(self.EP_OUT, telegram, 0)

    def set_address_filter(self, address_mask):
        """Set the address filter based on the mask type"""
        if address_mask == self.ADDRESS_MASK_AUDIO_MASTER:
            print("Setting address filter to audio master mode")
            self.send_message([0xf6, 0x10, 0xc1, 0x80, 0x83, 0x05, 0x00, 0x00])
        elif address_mask == self.ADDRESS_MASK_BEOPORT:
            print("Setting address filter to Beoport PC2 mode")
            self.send_message([0xf6, 0x00, 0x82, 0x80, 0x83])
        elif address_mask == self.ADDRESS_MASK_PROMISC:
            print("Setting address filter to promiscuous mode")
            self.send_message([0xf6, 0xc0, 0xc1, 0x80, 0x83, 0x05, 0x00, 0x00])
        else:
            print("Error: Invalid address mask")

    def start_sniffing(self):
        """Start sniffing USB messages and sending them via webhook/websocket"""
        self.running = True
        
        # Start the sniffer thread (reads USB and adds to queue)
        self.sniffer_thread = threading.Thread(target=self._sniff_loop)
        self.sniffer_thread.daemon = True
        self.sniffer_thread.start()
        
        # Start the sender thread (processes queue and sends messages)
        self.sender_thread = threading.Thread(target=self._sender_loop)
        self.sender_thread.daemon = True
        self.sender_thread.start()
        
        print("USB message sniffer and sender threads started")

    def _sniff_loop(self):
        """Background thread to continuously read USB messages and add to queue"""
        timeout_count = 0
        last_timeout_message = time.time()

        while self.running:
            try:
                # Try to read data from the device with a timeout
                # Buffer size of 1024 should be enough for most messages
                data = self.dev.read(self.EP_IN, 1024, timeout=500)  # Increased timeout to 500ms

                if data and len(data) > 0:
                    # Reset timeout counter when we get data
                    timeout_count = 0

                    # Convert data to a list of bytes
                    message = list(data)
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

                    # Process the message (only for Beo4 keycodes)
                    if len(message) > 2 and message[2] == 0x02:
                        msg_data = self.process_beo4_keycode(timestamp, message)
                        if msg_data:
                            # Add to queue for processing by sender thread
                            self.message_queue.add(msg_data)

            except usb.core.USBTimeoutError:
                # This specifically catches timeout errors
                timeout_count += 1

                # Only print a timeout message occasionally to reduce spam
                if time.time() - last_timeout_message > 10:  # Show timeout message at most once per 10 seconds
                    # print(f"No data received for a while ({timeout_count} timeouts)")
                    last_timeout_message = time.time()

                time.sleep(0.1)  # Short delay to prevent tight loop

            except usb.core.USBError as e:
                # Handle other USB errors (not timeouts)
                print(f"USB Error: {e}")
                time.sleep(0.5)  # Longer delay on actual errors

            except Exception as e:
                print(f"Error in sniffing thread: {e}")
                time.sleep(1)  # Even longer delay on unexpected errors
    
    def _sender_loop(self):
        """Background thread to process messages from the queue and send them"""
        # Connect to the WebSocket server
        self._connect_websocket()
        
        while self.running:
            try:
                # Get a message from the queue
                message = self.message_queue.get()
                
                # If we got a message, process it
                if message:
                    # Check if we should send via webhook
                    if shouldSendWebhook(message):
                        self._send_webhook(message)
                    
                    # Check if we should send via WebSocket
                    if shouldSendWebsocket(message):
                        self._send_websocket(message)
                
                # Short sleep to prevent tight loop
                time.sleep(0.01)
                
            except Exception as e:
                print(f"Error in sender thread: {e}")
                time.sleep(0.5)
    
    def _connect_websocket(self):
        """Connect to the WebSocket server"""
        try:
            # Close existing connection if any
            if self.ws:
                self.ws.close()
            
            # Connect to the WebSocket server
            self.ws = websocket.WebSocket()
            self.ws.connect(WEBSOCKET_URL)
            print(f"Connected to WebSocket server at {WEBSOCKET_URL}")
            
        except Exception as e:
            print(f"Error connecting to WebSocket server: {e}")
            self.ws = None
    
    def _send_websocket(self, message):
        """Send a message via WebSocket"""
        try:
            if not self.ws:
                self._connect_websocket()
                if not self.ws:
                    return  # Connection failed
            
            # Convert key_name to the expected format for WebSocket
            key_name = message.get('key_name', '')
            device_type = message.get('device_type', '')
            count = message.get('count', 1)
            
            # Map to websocket format
            ws_data = {}
            
            # Handle special commands with count
            if key_name == 'volup':
                ws_type = 'volume'
                ws_data = {'direction': 'clock', 'speed': min(count * 10, 80)}
            elif key_name == 'voldown':
                ws_type = 'volume'
                ws_data = {'direction': 'counter', 'speed': min(count * 10, 80)}
            elif key_name == 'left':
                ws_type = 'button'
                ws_data = {'button': 'left'}
            elif key_name == 'right': 
                ws_type = 'button'
                ws_data = {'button': 'right'}
            elif key_name == 'go':
                ws_type = 'button'
                ws_data = {'button': 'go'}
            else:
                # Default button handling
                ws_type = 'button'
                ws_data = {'button': key_name}
            
            # Prepare the WebSocket message
            ws_message = {
                'type': ws_type,
                'data': ws_data
            }
            
            # Send the message
            self.ws.send(json.dumps(ws_message))
            print(f"Sent WebSocket message: {ws_message}")
            
        except Exception as e:
            print(f"Error sending WebSocket message: {e}")
            self.ws = None  # Reset connection on error
    
    def _send_webhook(self, message):
        """Send a message via webhook"""
        try:
            # Prepare webhook payload for Home Assistant
            webhook_data = {
                'device': 'beosound5c',
                'action': message.get('key_name', ''),
                'device_type': message.get('device_type', ''),
                'count': message.get('count', 1),
                'timestamp': datetime.now().isoformat()
            }
            
            # Send the webhook
            response = requests.post(WEBHOOK_URL, json=webhook_data, timeout=2.0)
            
            # Check if successful
            if response.status_code >= 200 and response.status_code < 300:
                print(f"Webhook sent successfully: {webhook_data}")
            else:
                print(f"Error sending webhook: {response.status_code} - {response.text}")
                
        except Exception as e:
            print(f"Error sending webhook: {e}")

    def process_beo4_keycode(self, timestamp, data):
        """Process and display a received Beo4 keycode USB message"""
        hex_data = " ".join([f"{x:02X}" for x in data])

        # Device type mapping
        device_type_map = {
            0x00: "Video",
            0x01: "Audio",
            0x05: "Vmem",
            0x1B: "Light"
        }

        # Key mapping based on your log and notes
        key_map = {
            0x00: "0", 0x01: "1", 0x02: "2", 0x03: "3", 0x04: "4",
            0x05: "5", 0x06: "6", 0x07: "7", 0x08: "8", 0x09: "9",
            0x0C: "off",
            0x0D: "mute",
            0x0F: "alloff",
            0x5C: "menu",
            0x1E: "up", 0x1F: "down",
            0x32: "left", 0x34: "right",
            0x35: "go", 0x36: "stop", 0x7F: "back",
            0x58: "list",
            0x60: "volup", 0x64: "voldown",
            0x80: "tv",
            0x81: "radio",  # Based on log: Unknown(0x81) is amem
            0x85: "vmem",
            0x86: "dvd",
            0x8A: "dtv",
            0x91: "amem",
            0x92: "cd",
            0xD4: "yellow", 0xD5: "green", 0xD8: "blue", 0xD9: "red"
        }

        # Parse mode and keycode
        mode = data[4]
        keycode = data[6]

        device_type = device_type_map.get(mode, f"Unknown(0x{mode:02x})")
        key_name = key_map.get(keycode, f"Unknown(0x{keycode:02x})")

        print(f"[{timestamp}] {device_type} → {key_name}")
        print(f"Raw data: {hex_data} | Device: {device_type} | Keycode: 0x{keycode:02X}")

        # If the key is unknown, log the data for future mapping
        if key_name.startswith("Unknown("):
            print(f"[MISSING BUTTON] Raw data: {hex_data} | Device: {device_type} | Keycode: 0x{keycode:02X}")
        
        # Create and return the processed message data
        return {
            'timestamp_str': timestamp,
            'device_type': device_type,
            'key_name': key_name,
            'keycode': f"0x{keycode:02X}",
            'raw_data': hex_data
        }

    def _process_message(self, timestamp, data):
        """Process and display a received USB message"""
        hex_data = " ".join([f"{x:02X}" for x in data])

        # Try to identify message type
        message_type = "Unknown"
        if len(data) > 2:
            if data[2] == 0x00:
                message_type = "Incoming Masterlink Telegram"
            elif data[2] == 0xE0:
                message_type = "Outgoing Masterlink Telegram"
            elif data[2] == 0x02:
                message_type = "Beo4 Keycode"
            elif data[2] == 0x03 or data[2] == 0x1D:
                message_type = "Mixer State"
            elif data[2] == 0x06:
                message_type = "Headphone State"

        # Log the message
        if(data[2] == 0x02):
            self.process_beo4_keycode(timestamp, data)
        else:
            print(f"[{timestamp}] RECEIVED {message_type}: {hex_data}")

        # Save to log file
        with open("pc2_usb_log.txt", "a") as f:
            f.write(f"[{timestamp}] {message_type}: {hex_data}\n")

    def stop_sniffing(self):
        """Stop the USB sniffer"""
        self.running = False
        if self.sniffer_thread:
            self.sniffer_thread.join(timeout=1.0)
        if self.sender_thread:
            self.sender_thread.join(timeout=1.0)
        if self.ws:
            self.ws.close()

    def close(self):
        """Close the device"""
        # Stop sniffing before closing
        if self.running:
            self.stop_sniffing()

        if self.dev:
            try:
                # Send close command as in the C++ code
                self.send_message([0xa7])

                # Release the interface
                usb.util.release_interface(self.dev, 0)

                # Reattach kernel driver if needed
                # self.dev.attach_kernel_driver(0)

                print("Device closed")
            except Exception as e:
                print(f"Error closing device: {e}")


# Example usage
if __name__ == "__main__":
    try:
        # Create and initialize the device
        pc2 = PC2Device()
        pc2.open()

        # Start the USB sniffer before initialization
        pc2.start_sniffing()

        # Initialize the device - we'll capture the responses
        print("\n=== Starting device initialization ===")
        pc2.init()

        # Set address filter to desired mode
        print("\n=== Setting address filter ===")
        pc2.set_address_filter(PC2Device.ADDRESS_MASK_PROMISC)  # Use promiscuous mode to capture all messages

        # Keep the program running to allow for communication
        print("\n=== Device initialized. Sniffing USB messages... ===")
        print("Press Ctrl+C to exit.")

        # Add timer to periodically print status
        start_time = time.time()
        try:
            while True:
                elapsed = time.time() - start_time
                time.sleep(1)
                # Print a status message every 30 seconds
                if int(elapsed) % 30 == 0 and int(elapsed) > 0:
                    print(f"\rSniffing... Elapsed time: {int(elapsed)} seconds | Queue size: {pc2.message_queue.size()}")
        except KeyboardInterrupt:
            raise

    except KeyboardInterrupt:
        print("\n\nExiting...")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        # Make sure to close the device
        if 'pc2' in locals():
            pc2.close()

        print("\nLog file saved as pc2_usb_log.txt")
