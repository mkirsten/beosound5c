import usb.core
import usb.util
import time
import threading
import queue
import sys
import json
import websocket
import aiohttp
import asyncio
from datetime import datetime
from collections import defaultdict

# Configuration variables
# Home Assistant webhook and WebSocket URLs
WEBHOOK_URL = "http://homeassistant.local:8123/api/webhook/beosound5c"
DIRECT_WEBHOOK_URL = "http://localhost:8765/webhook"  # Set to None to disable
WEBSOCKET_URL = "ws://localhost:8765"

# Message processing settings
MESSAGE_TIMEOUT = 2.0  # Discard messages older than 2 seconds
DEDUP_COMMANDS = []  # Removed deduplication for time-sensitive commands
WEBHOOK_INTERVAL = 0.2  # Send webhook at least every 0.2 seconds for deduped commands
MAX_WEBHOOK_RETRIES = 0  # No retries - just send once and log failures
WEBHOOK_TIMEOUT = 0.2  # Shorter timeout for webhooks
MAX_QUEUE_SIZE = 10  # Maximum number of messages to keep in queue

# Time-sensitive commands that should use direct webhook if available
FAST_COMMANDS = ["volup", "voldown", "left", "right"]

# Add debug flag
DEBUG_NETWORK = True  # Set to True to enable detailed network debugging

sys.stdout.reconfigure(line_buffering=True)

class MessageQueue:
    """Thread-safe queue with lossy behavior."""
    def __init__(self, timeout=MESSAGE_TIMEOUT):
        self.lock = threading.Lock()
        self.queue = []
        self.timeout = timeout
    
    def add(self, message):
        """Add a message to the queue with timestamp."""
        with self.lock:
            # Add timestamp to the message
            now = time.time()
            message['timestamp'] = now
            
            # Ensure count is set to 1 for all messages
            message['count'] = 1
            
            # Add message to queue
            self.queue.append(message)
            
            # Limit queue size to prevent memory issues
            if len(self.queue) > MAX_QUEUE_SIZE:
                # Keep priority messages and remove oldest non-priority ones
                priority_msgs = [msg for msg in self.queue if msg.get('priority', False)]
                non_priority_msgs = [msg for msg in self.queue if not msg.get('priority', False)]
                
                # Sort non-priority by timestamp and keep only newest ones
                non_priority_msgs.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                keep_count = max(0, MAX_QUEUE_SIZE - len(priority_msgs))
                
                # Rebuild queue with all priority messages and newest non-priority ones
                self.queue = priority_msgs + non_priority_msgs[:keep_count]
    
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
            return message
    
    def size(self):
        """Return the current size of the queue."""
        with self.lock:
            return len(self.queue)


def shouldSendWebhook(data):
    """Always send webhooks for all messages - no filtering."""
    return True

def shouldSendWebsocket(data):
    return False

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
        self.session = None
        self.loop = None
        self.start_time = time.time()

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
        
        # Create an event loop for the sender thread
        self.loop = asyncio.new_event_loop()
        
        # Start the sniffer thread (reads USB and adds to queue)
        self.sniffer_thread = threading.Thread(target=self._sniff_loop)
        self.sniffer_thread.daemon = True
        self.sniffer_thread.start()
        
        # Start the sender thread (processes queue and sends messages)
        self.sender_thread = threading.Thread(target=self._sender_loop_wrapper)
        self.sender_thread.daemon = True
        self.sender_thread.start()
        
        print("USB message sniffer and sender threads started")

    def _sniff_loop(self):
        """Background thread to continuously read USB messages and add to queue"""
        timeout_count = 0
        last_timeout_message = time.time()
        usb_errors = 0
        last_usb_error = None
        last_successful_read = time.time()

        while self.running:
            try:
                # Try to read data from the device with a timeout
                # Buffer size of 1024 should be enough for most messages
                data = self.dev.read(self.EP_IN, 1024, timeout=500)  # Increased timeout to 500ms

                if data and len(data) > 0:
                    # Reset timeout counter when we get data
                    timeout_count = 0
                    usb_errors = 0
                    last_successful_read = time.time()

                    # Convert data to a list of bytes
                    message = list(data)
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

                    # Process the message
                    self._process_message(timestamp, message)
                else:
                    # Increment timeout counter
                    timeout_count += 1
                    
                    # Only log timeouts occasionally to avoid flooding the console
                    now = time.time()
                    if now - last_timeout_message > 10:  # Log every 10 seconds at most
                        print(f"No data received for {int(now - last_successful_read)} seconds (timeout count: {timeout_count})")
                        last_timeout_message = now

                    # Short delay to prevent tight loop
                    time.sleep(0.1)  # Short delay to prevent tight loop

            except usb.core.USBError as e:
                # Handle USB errors
                usb_errors += 1
                last_usb_error = str(e)
                
                # Log USB errors with increasing detail based on frequency
                if usb_errors == 1:
                    print(f"USB Error: {e}")
                elif usb_errors % 10 == 0:  # Log every 10 errors
                    print(f"USB Error count: {usb_errors}, last error: {last_usb_error}")
                    
                    # Check if we need to reconnect
                    if usb_errors > 50:
                        print("Too many USB errors, attempting to reconnect...")
                        try:
                            # Try to re-initialize the device
                            self.dev = None
                            self.open()
                            self.init()
                            print("USB device reconnected successfully")
                            usb_errors = 0
                        except Exception as re:
                            print(f"Failed to reconnect USB device: {re}")
                
                time.sleep(0.5)  # Longer delay on actual errors

            except Exception as e:
                print(f"Error in sniffing thread: {e}")
                time.sleep(1)  # Even longer delay on unexpected errors
    
    def _sender_loop_wrapper(self):
        """Wrapper to run the async sender loop in its own thread"""
        print("🔍 DEBUG: _sender_loop_wrapper started")
        try:
            asyncio.set_event_loop(self.loop)
            print("🔍 DEBUG: Set event loop")
            self.loop.run_until_complete(self._init_session())
            print("🔍 DEBUG: Session initialized")
            self.loop.run_until_complete(self._async_sender_loop())
            print("🔍 DEBUG: Sender loop completed")
        except Exception as e:
            print(f"🔍 DEBUG: Error in _sender_loop_wrapper: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
    
    async def _init_session(self):
<<<<<<< HEAD
        """Initialize the aiohttp session with optimized settings"""
        # Create a TCP connector with optimized settings
        connector = aiohttp.TCPConnector(
            limit=10,  # Limit total number of connections
            limit_per_host=5,  # Limit connections per host
            force_close=True,  # Don't keep connections alive
            enable_cleanup_closed=True,  # Clean up closed connections
            ttl_dns_cache=300  # Cache DNS results for 5 minutes
        )
        
        # Create the session with the connector
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT),
            raise_for_status=False  # We'll handle status codes manually
        )
        
=======
        """Initialize aiohttp session with optimized settings"""
        print("🔍 DEBUG: Initializing aiohttp session")
        try:
            # Configure TCP connector with keepalive and limits
            connector = aiohttp.TCPConnector(
                limit=10,  # Limit number of simultaneous connections
                ttl_dns_cache=300,  # Cache DNS results for 5 minutes
                keepalive_timeout=60,  # Keep connections alive for 60 seconds
                force_close=False,  # Don't force close connections
            )
            
            # Create session with the connector
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=1.0),  # Default timeout
                headers={"User-Agent": "BeosoundSniffer/1.0"}
            )
            print("🔍 DEBUG: aiohttp session created successfully")
        except Exception as e:
            print(f"🔍 DEBUG: Error creating aiohttp session: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
>>>>>>> 4d58683cbee97f0cf77eae617749ce9db5a545be
        print("Initialized aiohttp session with optimized settings")
    
    async def _async_sender_loop(self):
        """Asynchronous background thread to process messages from the queue and send them"""
        print("🔍 DEBUG: _async_sender_loop started")
        
        # Connect to the WebSocket server
        self._connect_websocket()
        
<<<<<<< HEAD
        # Track processing statistics
        processed_count = 0
        last_stats_time = time.time()
=======
        message_count = 0
        last_debug_time = time.time()
>>>>>>> 4d58683cbee97f0cf77eae617749ce9db5a545be
        
        while self.running:
            try:
                # Get a message from the queue
                message = self.message_queue.get()
                
                # Debug output every 10 seconds
                now = time.time()
                if now - last_debug_time > 10:
                    print(f"🔍 DEBUG: Sender loop alive, processed {message_count} messages since last debug")
                    print(f"🔍 DEBUG: Queue size: {self.message_queue.size()}, Session exists: {self.session is not None}")
                    message_count = 0
                    last_debug_time = now
                
                # If we got a message, process it
                if message:
<<<<<<< HEAD
                    processed_count += 1
=======
                    message_count += 1
                    print(f"🔍 DEBUG: Processing message: {message.get('key_name', 'unknown')}")
                    tasks = []
>>>>>>> 4d58683cbee97f0cf77eae617749ce9db5a545be
                    
                    # Check if we should send via webhook (prioritize this)
                    if shouldSendWebhook(message) or message.get('force_webhook', False):
<<<<<<< HEAD
                        await self._send_webhook_async(message)
=======
                        print(f"🔍 DEBUG: Should send webhook for {message.get('key_name', 'unknown')}")
                        tasks.append(self._send_webhook_async(message))
>>>>>>> 4d58683cbee97f0cf77eae617749ce9db5a545be
                    
                    # Check if we should send via WebSocket (lower priority)
                    if shouldSendWebsocket(message):
                        self._send_websocket(message)
                    
<<<<<<< HEAD
                    # No sleep when processing messages to minimize latency
                else:
                    # Short sleep only when queue is empty
                    await asyncio.sleep(0.001)
=======
                    # Run webhook tasks concurrently
                    if tasks:
                        print(f"🔍 DEBUG: Running {len(tasks)} webhook tasks")
                        await asyncio.gather(*tasks, return_exceptions=True)
>>>>>>> 4d58683cbee97f0cf77eae617749ce9db5a545be
                
                # Print stats every 60 seconds
                now = time.time()
                if now - last_stats_time > 60:
                    queue_size = self.message_queue.size()
                    print(f"Sniffing... Elapsed time: {int(now - self.start_time)} seconds | Queue size: {queue_size} | Processed: {processed_count} messages")
                    last_stats_time = now
                    processed_count = 0
                
            except Exception as e:
                print(f"🔍 DEBUG: Error in _async_sender_loop: {type(e).__name__}: {str(e)}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(0.1)
    
    async def _send_webhook_async(self, message):
        """Send a message via webhook asynchronously with no retries"""
        # Prepare webhook payload for Home Assistant
        webhook_data = {
            'device': 'beosound5c',
            'action': message.get('key_name', ''),
            'device_type': message.get('device_type', ''),
            'count': message.get('count', 1),
            'timestamp': datetime.now().isoformat()
        }
        
<<<<<<< HEAD
        # Determine if this is a time-sensitive command
        action = webhook_data['action']
        use_direct = DIRECT_WEBHOOK_URL and action in FAST_COMMANDS
        
        # Simple send with no retries
        try:
            # Send the webhook asynchronously
            if self.session:
                start_time = time.time()
                async with self.session.post(
                    WEBHOOK_URL, 
                    json=webhook_data, 
                    timeout=aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT),
                    raise_for_status=True  # Raise exception for non-2xx responses
                ) as response:
                    # This will only execute if status is 2xx due to raise_for_status
                    if DEBUG_NETWORK:
                        print(f"Webhook sent successfully: {webhook_data}")
                    else:
                        print(f"Webhook sent: {webhook_data['action']}")
                    return True
=======
        # DIAGNOSTIC: Print webhook attempt
        print(f"🔍 DEBUG: Attempting to send webhook: {webhook_data['action']} to {WEBHOOK_URL}")
        
        # Implement retry logic
        retries = 0
        while retries <= MAX_WEBHOOK_RETRIES:
            try:
                # Send the webhook asynchronously
                if self.session:
                    print(f"🔍 DEBUG: Session exists, sending POST request")
                    async with self.session.post(
                        WEBHOOK_URL, 
                        json=webhook_data, 
                        timeout=aiohttp.ClientTimeout(total=0.3),  # Even shorter timeout for faster failure
                        raise_for_status=True  # Raise exception for non-2xx responses
                    ) as response:
                        # This will only execute if status is 2xx due to raise_for_status
                        print(f"Webhook sent successfully: {webhook_data}")
                        return True
                else:
                    print("🔍 DEBUG: No aiohttp session available - this is the problem!")
                    # Try to recreate the session
                    try:
                        await self._init_session()
                        print("🔍 DEBUG: Created new session")
                    except Exception as se:
                        print(f"🔍 DEBUG: Failed to create session: {se}")
                    return False
                
            except asyncio.TimeoutError:
                print(f"Webhook timeout (attempt {retries+1}/{MAX_WEBHOOK_RETRIES+1})")
            except aiohttp.ClientResponseError as e:
                print(f"Webhook response error (attempt {retries+1}/{MAX_WEBHOOK_RETRIES+1}): {e.status}")
            except aiohttp.ClientError as e:
                print(f"Webhook client error (attempt {retries+1}/{MAX_WEBHOOK_RETRIES+1}): {str(e)}")
            except Exception as e:
                print(f"🔍 DEBUG: Unexpected webhook error: {type(e).__name__}: {str(e)}")
                import traceback
                traceback.print_exc()
            
            # If we get here, the request failed - increment retries and wait before trying again
            retries += 1
            if retries <= MAX_WEBHOOK_RETRIES:
                await asyncio.sleep(WEBHOOK_RETRY_DELAY)  # Fixed delay, no exponential backoff
>>>>>>> 4d58683cbee97f0cf77eae617749ce9db5a545be
            else:
                print("No aiohttp session available")
                return False
            
        except asyncio.TimeoutError:
            print(f"⚠️ Webhook timeout for {webhook_data['action']} - server might be busy")
            # Add network diagnostics
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                host = url.split('://')[1].split('/')[0].split(':')[0]
                port = 8123 if "homeassistant" in host else 8765
                result = s.connect_ex((host, port))
                if result == 0:
                    print(f"✓ Connection to {host}:{port} succeeded, but request timed out")
                else:
                    print(f"✗ Connection to {host}:{port} failed with error {result}")
                s.close()
            except Exception as e:
                print(f"Network diagnostic error: {str(e)}")
        except aiohttp.ClientResponseError as e:
            print(f"⚠️ Webhook server error: {e.status} - {e.message}")
        except aiohttp.ClientError as e:
            print(f"⚠️ Webhook client error: {str(e)}")
        except Exception as e:
            print(f"⚠️ Unexpected webhook error: {str(e)}")
        
        return False
    
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
        
        # Create the processed message data
        msg_data = {
            'timestamp_str': timestamp,
            'device_type': device_type,
            'key_name': key_name,
            'keycode': f"0x{keycode:02X}",
            'raw_data': hex_data
        }
        
        print(f"🔍 DEBUG: Created message for queue: {key_name}")
        
        return msg_data

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
            print(f"🔍 DEBUG: Processing Beo4 keycode")
            msg_data = self.process_beo4_keycode(timestamp, data)
            if msg_data:
                print(f"🔍 DEBUG: Adding message to queue: {msg_data.get('key_name')}")
                self.message_queue.add(msg_data)
                print(f"🔍 DEBUG: Queue size after add: {self.message_queue.size()}")
        else:
            print(f"[{timestamp}] RECEIVED {message_type}: {hex_data}")

    def stop_sniffing(self):
        """Stop the USB sniffer"""
        self.running = False
        
        # Close the aiohttp session
        if self.session and self.loop:
            asyncio.run_coroutine_threadsafe(self.session.close(), self.loop)
        
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

        # Remove reference to log file
        print("\nExiting sniffer")
