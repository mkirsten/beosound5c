"""
Sonos direct control implementation for B&O remote.
This module provides direct control of Sonos speakers using SoCo.
"""

import asyncio
import time
from hw.webhook_sender import WebhookSender

try:
    import soco
    SOCO_AVAILABLE = True
except ImportError:
    SOCO_AVAILABLE = False
    print("SoCo library not available. Install with: pip install soco")

class SonosSender(WebhookSender):
    """Webhook sender that directly controls Sonos speakers"""
    
    def __init__(self, sonos_ip="192.168.0.116"):
        self.sonos_ip = sonos_ip
        self.speaker = None
        self.volume_step = 2  # Default volume step
        self.initialized = False
        self.last_command_time = {}  # To prevent too rapid commands
        self.command_interval = 0.1  # Minimum interval between same commands
        
    async def initialize(self):
        """Initialize the Sonos connection"""
        if not SOCO_AVAILABLE:
            print("Cannot initialize Sonos control - SoCo library not available")
            return False
            
        try:
            # This is a blocking operation, so we run it in a thread
            loop = asyncio.get_event_loop()
            self.speaker = await loop.run_in_executor(None, lambda: soco.SoCo(self.sonos_ip))
            
            # Test the connection by getting the volume
            volume = await loop.run_in_executor(None, lambda: self.speaker.volume)
            print(f"Connected to Sonos at {self.sonos_ip} (current volume: {volume})")
            self.initialized = True
            return True
        except Exception as e:
            print(f"Failed to connect to Sonos at {self.sonos_ip}: {e}")
            return False
        
    async def send_webhook(self, message):
        """Process a webhook message and control Sonos accordingly"""
        if not SOCO_AVAILABLE:
            print("Cannot control Sonos - SoCo library not available")
            return False
            
        if not self.initialized:
            success = await self.initialize()
            if not success:
                return False
                
        action = message.get('key_name', '')
        
        # Rate limiting for rapid commands
        now = time.time()
        if action in self.last_command_time:
            time_since_last = now - self.last_command_time[action]
            if time_since_last < self.command_interval:
                # Too soon, skip this command
                return True
        self.last_command_time[action] = now
        
        try:
            loop = asyncio.get_event_loop()
            
            # Handle different commands
            if action == 'volup':
                # Get current volume
                current_vol = await loop.run_in_executor(None, lambda: self.speaker.volume)
                # Set new volume (capped at 100)
                new_vol = min(100, current_vol + self.volume_step)
                await loop.run_in_executor(None, lambda: setattr(self.speaker, 'volume', new_vol))
                print(f"Sonos volume up: {current_vol} → {new_vol}")
                
            elif action == 'voldown':
                # Get current volume
                current_vol = await loop.run_in_executor(None, lambda: self.speaker.volume)
                # Set new volume (minimum 0)
                new_vol = max(0, current_vol - self.volume_step)
                await loop.run_in_executor(None, lambda: setattr(self.speaker, 'volume', new_vol))
                print(f"Sonos volume down: {current_vol} → {new_vol}")
                
            elif action == 'right' or action == 'next':
                # Next track
                await loop.run_in_executor(None, lambda: self.speaker.next())
                print("Sonos next track")
                
            elif action == 'left' or action == 'prev':
                # Previous track
                await loop.run_in_executor(None, lambda: self.speaker.previous())
                print("Sonos previous track")
                
            elif action == 'go' or action == 'play' or action == 'pause':
                # Toggle play/pause
                transport_info = await loop.run_in_executor(None, lambda: self.speaker.get_current_transport_info())
                current_state = transport_info.get('current_transport_state', '')
                
                if current_state == 'PLAYING':
                    await loop.run_in_executor(None, lambda: self.speaker.pause())
                    print("Sonos paused")
                else:
                    await loop.run_in_executor(None, lambda: self.speaker.play())
                    print("Sonos playing")
            
            elif action == 'mute':
                # Toggle mute
                current_mute = await loop.run_in_executor(None, lambda: self.speaker.mute)
                await loop.run_in_executor(None, lambda: setattr(self.speaker, 'mute', not current_mute))
                print(f"Sonos mute: {not current_mute}")
                
            else:
                # Unhandled command
                print(f"Unhandled Sonos command: {action}")
                return False
                
            return True
            
        except Exception as e:
            print(f"Error controlling Sonos: {e}")
            self.initialized = False  # Force re-initialization on next command
            return False
            
    async def close(self):
        """Clean up resources"""
        # Nothing to clean up for SoCo
        self.speaker = None
        self.initialized = False 