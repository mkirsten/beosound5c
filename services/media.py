#!/usr/bin/env python3
"""
Media Server for Sonos Integration
Monitors Sonos player for changes and sends updates via WebSocket.
Handles both automatic change detection and on-demand requests.

This runs as a separate service to avoid interfering with the latency-sensitive
USB event processing in server.py.
"""

import asyncio
import websockets
import json
import time
import logging
import signal
import sys
import os
from threading import Thread
import base64
from io import BytesIO
import requests
from urllib.parse import urlparse

# Import Sonos libraries
try:
    import soco
    from soco import SoCo
except ImportError:
    print("ERROR: soco library not installed. Install with: pip install soco")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow library not installed. Install with: pip install pillow")
    sys.exit(1)

# Configuration
SONOS_IP = '192.168.1.111'
WEBSOCKET_PORT = 8766
POLL_INTERVAL = 2.0  # seconds between change checks
MAX_ARTWORK_SIZE = 500 * 1024  # 500KB limit for artwork

# Global variables for caching and state
clients = set()
current_track_id = None
current_position = None
cached_media_data = None
last_update_time = 0
cached_artwork_url = None  # Track artwork URL to avoid re-fetching same artwork
cached_artwork_data = None  # Cache the actual artwork data
sonos_viewer = None

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('media_server')

class SonosArtworkViewer:
    """Integrated Sonos artwork viewer for direct communication with Sonos devices."""
    
    def __init__(self, sonos_ip):
        self.sonos_ip = sonos_ip
        self.sonos = SoCo(sonos_ip)
        
    def get_current_track_info(self):
        """Get current track information from Sonos player."""
        try:
            track_info = self.sonos.get_current_track_info()
            return track_info
        except Exception as e:
            logger.error(f"Error getting track info: {e}")
            return None
    
    def get_artwork_url(self):
        """Get the artwork URL for the currently playing track."""
        track_info = self.get_current_track_info()
        if not track_info:
            return None
            
        artwork_url = track_info.get('album_art', '')
        if not artwork_url:
            logger.debug("No artwork URL found for current track")
            return None
        
        # Handle relative URLs by making them absolute
        if artwork_url.startswith('/'):
            artwork_url = f"http://{self.sonos_ip}:1400{artwork_url}"
        
        return artwork_url
    
    def fetch_artwork(self, url):
        """Fetch artwork from URL and return as PIL Image."""
        try:
            logger.debug(f"Fetching artwork from: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            if len(response.content) == 0:
                logger.warning("Artwork URL returned 0 bytes")
                return None
            
            logger.debug(f"Downloaded {len(response.content)} bytes of artwork data")
            
            # Create PIL Image from response content
            image = Image.open(BytesIO(response.content))
            return image
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error fetching artwork: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error processing artwork: {e}")
            return None

class MediaServer:
    def __init__(self):
        self.running = False
        self.sonos_viewer = SonosArtworkViewer(SONOS_IP)
        
    async def start(self):
        """Start the media server."""
        self.running = True
        logger.info(f"Starting media server for Sonos at {SONOS_IP}")
        
        # Start WebSocket server
        ws_server = await websockets.serve(self.handle_client, '0.0.0.0', WEBSOCKET_PORT)
        logger.info(f"WebSocket server listening on port {WEBSOCKET_PORT}")
        
        # Start background monitoring
        monitor_task = asyncio.create_task(self.monitor_sonos())
        
        # Wait for shutdown signal
        try:
            await ws_server.wait_closed()
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        finally:
            self.running = False
            monitor_task.cancel()
            
    async def handle_client(self, websocket):
        """Handle new WebSocket client connections."""
        global clients, cached_media_data
        
        clients.add(websocket)
        client_info = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        logger.info(f"Client connected: {client_info}")
        
        try:
            # Immediately send current media info to new client
            if cached_media_data:
                await self.send_media_update(websocket, cached_media_data, 'client_connect')
            else:
                # Fetch fresh data for first-time connection
                media_data = await self.fetch_media_data()
                if media_data:
                    await self.send_media_update(websocket, media_data, 'client_connect')
            
            # Keep connection alive (no message handling - push only)
            await websocket.wait_closed()
                    
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"Error with client {client_info}: {e}")
        finally:
            clients.discard(websocket)
            logger.info(f"Client disconnected: {client_info}")
            
    async def monitor_sonos(self):
        """Background task to monitor Sonos for changes."""
        global current_track_id, current_position, last_update_time, cached_media_data
        
        logger.info("Starting Sonos monitoring")
        
        while self.running:
            try:
                # Get current track info
                track_info = self.sonos_viewer.get_current_track_info()
                
                if track_info:
                    track_id = track_info.get('uri', '')
                    position = track_info.get('position', '0:00')
                    
                    # Check if track changed
                    track_changed = track_id != current_track_id
                    
                    # Check if position jumped (indicating external control)
                    position_jumped = False
                    if current_position and position:
                        try:
                            # Simple position jump detection
                            current_seconds = self.time_to_seconds(current_position)
                            new_seconds = self.time_to_seconds(position)
                            expected_seconds = current_seconds + POLL_INTERVAL
                            
                            # If position jumped more than expected + tolerance
                            if abs(new_seconds - expected_seconds) > 5:
                                position_jumped = True
                        except:
                            pass
                    
                    # Only broadcast if there are actual changes AND we have connected clients
                    if (track_changed or position_jumped) and clients:
                        reason = 'track_change' if track_changed else 'external_control'
                        logger.info(f"Detected change: {reason}")
                        
                        media_data = await self.fetch_media_data()
                        if media_data:
                            await self.broadcast_media_update(media_data, reason)
                            
                        current_track_id = track_id
                    else:
                        # Still update cached data silently for future requests
                        if track_changed:
                            current_track_id = track_id
                            # Update cached data without broadcasting
                            await self.fetch_media_data()
                        
                    current_position = position
                    
                await asyncio.sleep(POLL_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in Sonos monitoring: {e}")
                await asyncio.sleep(POLL_INTERVAL)
                
    async def fetch_media_data(self):
        """Fetch current media data including artwork."""
        global cached_media_data, last_update_time
        
        try:
            # Get track info
            track_info = self.sonos_viewer.get_current_track_info()
            if not track_info:
                return None
                
            # Get artwork
            artwork_url = self.sonos_viewer.get_artwork_url()
            artwork_base64 = None
            artwork_size = None
            
            if artwork_url:
                try:
                    image = self.sonos_viewer.fetch_artwork(artwork_url)
                    if image:
                        # Convert to base64
                        img_io = BytesIO()
                        
                        # Convert to RGB if necessary
                        if image.mode in ('RGBA', 'LA', 'P'):
                            rgb_image = image.convert('RGB')
                            image = rgb_image
                            
                        # Resize if too large
                        if img_io.tell() == 0:  # Haven't saved yet
                            image.save(img_io, 'JPEG', quality=85)
                            
                        # Check size and reduce quality if needed
                        if img_io.tell() > MAX_ARTWORK_SIZE:
                            img_io = BytesIO()
                            image.save(img_io, 'JPEG', quality=60)
                            
                        img_io.seek(0)
                        artwork_base64 = base64.b64encode(img_io.getvalue()).decode('utf-8')
                        artwork_size = image.size
                        
                        logger.info(f"Fetched artwork: {artwork_size}, {len(artwork_base64)} chars")
                        
                except Exception as e:
                    logger.warning(f"Failed to fetch artwork: {e}")
            
            # Build media data
            media_data = {
                'title': track_info.get('title', '—'),
                'artist': track_info.get('artist', '—'),
                'album': track_info.get('album', '—'),
                'artwork': f'data:image/jpeg;base64,{artwork_base64}' if artwork_base64 else None,
                'artwork_size': artwork_size,
                'position': track_info.get('position', '0:00'),
                'duration': track_info.get('duration', '0:00'),
                'state': 'playing' if track_info.get('position') else 'paused',
                'uri': track_info.get('uri', ''),
                'timestamp': int(time.time())
            }
            
            cached_media_data = media_data
            last_update_time = time.time()
            
            return media_data
            
        except Exception as e:
            logger.error(f"Error fetching media data: {e}")
            return None
            
    async def broadcast_media_update(self, media_data, reason='update'):
        """Broadcast media update to all connected clients."""
        global clients
        
        if not clients:
            return
            
        message = {
            'type': 'media_update',
            'reason': reason,
            'data': media_data
        }
        
        message_json = json.dumps(message)
        
        # Send to all clients
        disconnected = set()
        for client in clients:
            try:
                await client.send(message_json)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
            except Exception as e:
                logger.error(f"Error sending to client: {e}")
                disconnected.add(client)
                
        # Remove disconnected clients
        clients -= disconnected
        
        if clients:
            logger.info(f"Broadcast media update to {len(clients)} clients: {reason}")
            
    def time_to_seconds(self, time_str):
        """Convert time string (MM:SS or HH:MM:SS) to seconds."""
        try:
            parts = time_str.split(':')
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except:
            pass
        return 0

    async def send_media_update(self, websocket, media_data, reason):
        """Send fresh media data to a specific client."""
        message = {
            'type': 'media_update',
            'reason': reason,
            'data': media_data
        }
        
        try:
            await websocket.send(json.dumps(message))
            logger.info(f"Sent media update to client: {reason}")
        except Exception as e:
            logger.error(f"Error sending media update: {e}")

def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)

async def main():
    """Main entry point."""
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and start media server
    server = MediaServer()
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Shutting down media server")
    except Exception as e:
        logger.error(f"Media server error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main()) 