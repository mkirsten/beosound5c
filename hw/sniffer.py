import usb.core
import usb.util
import time
import threading
import queue
import sys
from datetime import datetime

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
        self.message_queue = queue.Queue()
        self.sniffer_thread = None
        
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
        """Start sniffing USB messages"""
        self.running = True
        self.sniffer_thread = threading.Thread(target=self._sniff_loop)
        self.sniffer_thread.daemon = True
        self.sniffer_thread.start()
        print("USB message sniffer started")
        
    def _sniff_loop(self):
        """Background thread to continuously read USB messages"""
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
                    
                    # Put message in queue
                    self.message_queue.put((timestamp, message))
                    
                    # Process and display the message
                    self._process_message(timestamp, message)
                
            except usb.core.USBTimeoutError:
                # This specifically catches timeout errors
                timeout_count += 1
                
                # Only print a timeout message occasionally to reduce spam
                if time.time() - last_timeout_message > 10:  # Show timeout message at most once per 10 seconds
                    print(f"No data received for a while ({timeout_count} timeouts)")
                    last_timeout_message = time.time()
                    
                time.sleep(0.1)  # Short delay to prevent tight loop
                
            except usb.core.USBError as e:
                # Handle other USB errors (not timeouts)
                print(f"USB Error: {e}")
                time.sleep(0.5)  # Longer delay on actual errors
                
            except Exception as e:
                print(f"Error in sniffing thread: {e}")
                time.sleep(1)  # Even longer delay on unexpected errors
    
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
        print(f"[{timestamp}] RECEIVED {message_type}: {hex_data}")
        
        # Save to log file
        with open("pc2_usb_log.txt", "a") as f:
            f.write(f"[{timestamp}] {message_type}: {hex_data}\n")
    
    def stop_sniffing(self):
        """Stop the USB sniffer"""
        self.running = False
        if self.sniffer_thread:
            self.sniffer_thread.join(timeout=1.0)
            
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
                    print(f"\rSniffing... Elapsed time: {int(elapsed)} seconds")
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
