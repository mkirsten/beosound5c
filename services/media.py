#!/usr/bin/env python3
"""
Media Server for Sonos Integration
Monitors Sonos player for changes and sends updates via WebSocket.
Handles both automatic change detection and on-demand requests.

This runs as a separate service to avoid interfering with the latency-sensitive
USB event processing in other services
"""

import asyncio
import websockets
import json
import time
import logging
import signal
import sys
import os
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import base64
from io import BytesIO
from urllib.parse import urlparse
import aiohttp

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
SONOS_IP = os.getenv('SONOS_IP', '192.168.0.190')
WEBSOCKET_PORT = 8766
POLL_INTERVAL = 0.5  # seconds between change checks (fast for responsive track changes)
MAX_ARTWORK_SIZE = 500 * 1024  # 500KB limit for artwork
ARTWORK_CACHE_SIZE = 100  # number of artworks to cache (~3-5MB RAM)
PREFETCH_COUNT = 5  # number of upcoming tracks to prefetch

# Global variables for caching and state
clients = set()
current_track_id = None
current_position = None
current_playback_state = None  # Track playback state for wake detection
cached_media_data = None
last_update_time = 0
sonos_viewer = None

# Thread pool for CPU-bound image processing
executor = ThreadPoolExecutor(max_workers=2)


class ArtworkCache:
    """Simple LRU cache for artwork data (URL -> base64)."""

    def __init__(self, max_size=20):
        self.max_size = max_size
        self._cache = OrderedDict()

    def get(self, url):
        """Get cached artwork, moving to end (most recently used)."""
        if url in self._cache:
            self._cache.move_to_end(url)
            return self._cache[url]
        return None

    def put(self, url, data):
        """Cache artwork data, evicting oldest if full."""
        if url in self._cache:
            self._cache.move_to_end(url)
        else:
            if len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)  # Remove oldest
            self._cache[url] = data

    def __contains__(self, url):
        return url in self._cache

    def __len__(self):
        return len(self._cache)


# Global artwork cache
artwork_cache = ArtworkCache(max_size=ARTWORK_CACHE_SIZE)

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
        self._cached_coordinator = None
        self._coordinator_check_time = 0
        
    def get_coordinator(self):
        """Get the group coordinator for this player with caching."""
        current_time = time.time()
        
        # Refresh coordinator info every 30 seconds or on first call
        if (self._cached_coordinator is None or 
            current_time - self._coordinator_check_time > 30):
            
            try:
                # If this player is part of a group, get the coordinator
                # If it's standalone, it will be its own coordinator
                coordinator = self.sonos.group.coordinator
                
                # Verify coordinator is reachable
                if coordinator and coordinator.ip_address:
                    self._cached_coordinator = coordinator
                    self._coordinator_check_time = current_time
                    
                    # Log if coordinator changed
                    if hasattr(self, '_last_coordinator_ip'):
                        if self._last_coordinator_ip != coordinator.ip_address:
                            logger.info(f"Coordinator changed from {self._last_coordinator_ip} to {coordinator.ip_address}")
                    self._last_coordinator_ip = coordinator.ip_address
                    
                    return coordinator
                else:
                    logger.debug("Coordinator not reachable, using original player")
                    self._cached_coordinator = self.sonos
                    self._coordinator_check_time = current_time
                    return self.sonos
                    
            except Exception as e:
                logger.debug(f"Error getting coordinator, using original player: {e}")
                self._cached_coordinator = self.sonos
                self._coordinator_check_time = current_time
                return self.sonos
        
        return self._cached_coordinator
            
    def get_current_track_info(self):
        """Get current track information from Sonos player or its coordinator."""
        try:
            # Always use the coordinator to get track info
            # This ensures we get the correct info regardless of which player we're querying
            coordinator = self.get_coordinator()
            track_info = coordinator.get_current_track_info()
            
            # Log coordinator info for debugging
            if coordinator != self.sonos:
                logger.debug(f"Using coordinator {coordinator.ip_address} instead of {self.sonos_ip}")
            
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
        # Use the coordinator's IP for artwork URLs to ensure consistency
        if artwork_url.startswith('/'):
            coordinator = self.get_coordinator()
            coordinator_ip = coordinator.ip_address
            artwork_url = f"http://{coordinator_ip}:1400{artwork_url}"
        
        return artwork_url
    
    async def fetch_artwork_async(self, url, session=None):
        """Fetch artwork from URL asynchronously and return as base64 string.

        Uses cache to avoid re-fetching the same artwork.
        Image processing runs in thread pool to avoid blocking.
        """
        global artwork_cache

        # Check cache first
        cached = artwork_cache.get(url)
        if cached is not None:
            logger.debug(f"Artwork cache hit for {url}")
            return cached

        logger.debug(f"Artwork cache miss, fetching: {url}")

        try:
            # Create session if not provided
            close_session = False
            if session is None:
                session = aiohttp.ClientSession()
                close_session = True

            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    response.raise_for_status()
                    image_bytes = await response.read()

                    if len(image_bytes) == 0:
                        logger.warning("Artwork URL returned 0 bytes")
                        return None

                    logger.debug(f"Downloaded {len(image_bytes)} bytes of artwork data")

                    # Process image in thread pool to avoid blocking event loop
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        executor, self._process_image, image_bytes
                    )

                    if result:
                        artwork_cache.put(url, result)
                        logger.info(f"Cached artwork for {url} ({len(artwork_cache)} items in cache)")

                    return result

            finally:
                if close_session:
                    await session.close()

        except aiohttp.ClientError as e:
            logger.warning(f"Error fetching artwork: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error processing artwork: {e}")
            return None

    def _process_image(self, image_bytes):
        """Process image bytes into base64 string (CPU-bound, runs in thread pool)."""
        try:
            image = Image.open(BytesIO(image_bytes))

            # Convert to RGB if necessary
            if image.mode in ('RGBA', 'LA', 'P'):
                image = image.convert('RGB')

            # Encode to JPEG
            img_io = BytesIO()
            image.save(img_io, 'JPEG', quality=85)

            # Reduce quality if too large
            if img_io.tell() > MAX_ARTWORK_SIZE:
                img_io = BytesIO()
                image.save(img_io, 'JPEG', quality=60)

            img_io.seek(0)
            base64_data = base64.b64encode(img_io.getvalue()).decode('utf-8')

            return {
                'base64': base64_data,
                'size': image.size
            }

        except Exception as e:
            logger.warning(f"Error processing image: {e}")
            return None

    def get_queue_artwork_urls(self, count=3):
        """Get artwork URLs for upcoming tracks in the queue.

        Returns list of (position, artwork_url) tuples for the next `count` tracks.
        """
        try:
            coordinator = self.get_coordinator()
            if not coordinator:
                return []

            # Get current queue position
            track_info = coordinator.get_current_track_info()
            if not track_info:
                return []

            current_pos_str = track_info.get('playlist_position', '0')
            try:
                current_pos = int(current_pos_str)
            except (ValueError, TypeError):
                return []

            # Get queue starting from next track
            # soco's get_queue returns items starting at start_index (0-based)
            start_index = current_pos  # current_pos is 1-based, so this gets next track
            queue = coordinator.get_queue(start=start_index, max_items=count)

            artwork_urls = []
            for i, item in enumerate(queue):
                album_art = getattr(item, 'album_art_uri', None)
                if album_art:
                    # Make URL absolute if needed
                    if album_art.startswith('/'):
                        album_art = f"http://{coordinator.ip_address}:1400{album_art}"
                    artwork_urls.append((start_index + i + 1, album_art))

            return artwork_urls

        except Exception as e:
            logger.debug(f"Error getting queue artwork URLs: {e}")
            return []

    async def prefetch_upcoming_artwork(self, count=3):
        """Prefetch artwork for upcoming tracks in background.

        This runs after a track change to warm the cache for smoother transitions.
        """
        urls = self.get_queue_artwork_urls(count=count)
        if not urls:
            logger.debug("No upcoming tracks to prefetch")
            return

        logger.info(f"Prefetching artwork for {len(urls)} upcoming tracks")

        # Create a shared session for all prefetch requests
        async with aiohttp.ClientSession() as session:
            tasks = []
            for position, url in urls:
                # Skip if already cached
                if url in artwork_cache:
                    logger.debug(f"Track {position} artwork already cached")
                    continue
                tasks.append(self._prefetch_single(session, position, url))

            if tasks:
                # Run prefetch tasks concurrently but don't wait too long
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=15.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("Prefetch timed out, some tracks may not be cached")

    async def _prefetch_single(self, session, position, url):
        """Prefetch a single artwork URL."""
        try:
            result = await self.fetch_artwork_async(url, session=session)
            if result:
                logger.debug(f"Prefetched artwork for track {position}")
            else:
                logger.debug(f"No artwork for track {position}")
        except Exception as e:
            logger.debug(f"Failed to prefetch track {position}: {e}")


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
        global current_track_id, current_position, current_playback_state, last_update_time, cached_media_data

        logger.info(f"Starting Sonos monitoring for {SONOS_IP}")
        
        # Log initial coordinator info
        try:
            coordinator = self.sonos_viewer.get_coordinator()
            if coordinator.ip_address != SONOS_IP:
                logger.info(f"Player {SONOS_IP} is grouped, using coordinator {coordinator.ip_address}")
            else:
                logger.info(f"Player {SONOS_IP} is standalone or group coordinator")
        except Exception as e:
            logger.warning(f"Could not determine coordinator status: {e}")
        
        while self.running:
            try:
                # Get current track info (automatically uses coordinator)
                track_info = self.sonos_viewer.get_current_track_info()

                # Check playback state for wake trigger
                coordinator = self.sonos_viewer.get_coordinator()
                try:
                    transport_info = coordinator.get_current_transport_info() if coordinator else {}
                    playback_state = transport_info.get('current_transport_state', 'STOPPED').lower()
                    if playback_state == 'playing':
                        state = 'playing'
                    elif playback_state == 'paused_playback':
                        state = 'paused'
                    else:
                        state = 'stopped'

                    # Trigger wake if state changed to playing
                    if state == 'playing' and current_playback_state in ('paused', 'stopped', None):
                        logger.info(f"Playback started (was: {current_playback_state}), triggering wake")
                        await self.trigger_wake()

                    current_playback_state = state
                except Exception as e:
                    logger.debug(f"Could not get transport state: {e}")

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

                        # Prefetch upcoming tracks in background (don't await)
                        if track_changed:
                            asyncio.create_task(self.sonos_viewer.prefetch_upcoming_artwork(count=PREFETCH_COUNT))
                    else:
                        # Still update cached data silently for future requests
                        if track_changed:
                            current_track_id = track_id
                            # Update cached data without broadcasting
                            await self.fetch_media_data()
                            # Prefetch upcoming tracks in background
                            asyncio.create_task(self.sonos_viewer.prefetch_upcoming_artwork(count=PREFETCH_COUNT))

                    current_position = position

                await asyncio.sleep(POLL_INTERVAL)

            except Exception as e:
                logger.error(f"Error in Sonos monitoring: {e}")
                await asyncio.sleep(POLL_INTERVAL)
                
    async def fetch_media_data(self):
        """Fetch current media data including artwork."""
        global cached_media_data, last_update_time

        try:
            # Get track info (automatically uses coordinator)
            track_info = self.sonos_viewer.get_current_track_info()
            if not track_info:
                logger.debug("No track info available")
                return None

            # Get artwork asynchronously (uses cache and non-blocking fetch)
            artwork_url = self.sonos_viewer.get_artwork_url()
            artwork_base64 = None
            artwork_size = None

            if artwork_url:
                try:
                    artwork_result = await self.sonos_viewer.fetch_artwork_async(artwork_url)
                    if artwork_result:
                        artwork_base64 = artwork_result['base64']
                        artwork_size = artwork_result['size']
                        logger.info(f"Artwork ready: {artwork_size}, {len(artwork_base64)} chars")
                except Exception as e:
                    logger.warning(f"Failed to fetch artwork: {e}")
            
            # Get speaker info - show configured speaker, note if grouped
            coordinator = self.sonos_viewer.get_coordinator()
            actual_speaker = self.sonos_viewer.sonos
            speaker_name = actual_speaker.player_name if actual_speaker else 'Unknown'
            speaker_ip = SONOS_IP

            # Check if grouped with a different coordinator
            is_grouped = False
            coordinator_name = None
            if coordinator and coordinator.ip_address != SONOS_IP:
                is_grouped = True
                coordinator_name = coordinator.player_name

            # Get playback state
            try:
                transport_info = coordinator.get_current_transport_info() if coordinator else {}
                playback_state = transport_info.get('current_transport_state', 'STOPPED').lower()
                if playback_state == 'playing':
                    state = 'playing'
                elif playback_state == 'paused_playback':
                    state = 'paused'
                else:
                    state = 'stopped'
            except:
                state = 'unknown'

            # Get volume
            try:
                volume = coordinator.volume if coordinator else 0
            except:
                volume = 0

            # Build media data
            media_data = {
                'title': track_info.get('title', '—'),
                'artist': track_info.get('artist', '—'),
                'album': track_info.get('album', '—'),
                'artwork': f'data:image/jpeg;base64,{artwork_base64}' if artwork_base64 else None,
                'artwork_size': artwork_size,
                'position': track_info.get('position', '0:00'),
                'duration': track_info.get('duration', '0:00'),
                'state': state,
                'volume': volume,
                'speaker_name': speaker_name,
                'speaker_ip': speaker_ip,
                'is_grouped': is_grouped,
                'coordinator_name': coordinator_name,
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

    async def trigger_wake(self):
        """Trigger screen wake via input service webhook."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'http://localhost:8767/webhook',
                    json={'command': 'wake', 'params': {'page': 'now_playing'}},
                    timeout=aiohttp.ClientTimeout(total=2)
                ) as response:
                    if response.status == 200:
                        logger.info("Triggered screen wake")
                    else:
                        logger.warning(f"Wake trigger returned status {response.status}")
        except Exception as e:
            logger.warning(f"Could not trigger wake: {e}")

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