#!/usr/bin/env python3
"""
Sonos Queue Viewer
Displays the current Sonos queue, handling grouped players correctly.
"""

import soco
import sys

def get_coordinator(sonos_device):
    """Get the group coordinator for this player."""
    try:
        # If this player is part of a group, get the coordinator
        # If it's standalone, it will be its own coordinator
        coordinator = sonos_device.group.coordinator
        
        # Verify coordinator is reachable
        if coordinator and coordinator.ip_address:
            return coordinator
        else:
            print(f"⚠️  Coordinator not reachable, using original player")
            return sonos_device
            
    except Exception as e:
        print(f"⚠️  Error getting coordinator, using original player: {e}")
        return sonos_device

def format_duration(duration_str):
    """Format duration string for better display."""
    if not duration_str or duration_str == "0:00:00":
        return "Unknown"
    return duration_str

def main():
    # Parse command line arguments
    debug_mode = "--debug" in sys.argv
    show_artwork = "--artwork" in sys.argv or "-a" in sys.argv
    
    if len(sys.argv) > 1 and (sys.argv[1] in ['-h', '--help']):
        print("Sonos Queue Viewer")
        print("Usage: python3 sonosq.py [options]")
        print("Options:")
        print("  --debug       Show debug information about queue items")
        print("  --artwork|-a  Show artwork URLs for each track")
        print("  --help|-h     Show this help message")
        return
    
    # Connect to specified speaker
    speaker_ip = "192.168.1.111"  # Match the IP from media.py
    
    try:
        device = soco.SoCo(speaker_ip)
        print(f"🔍 Connecting to: {device.player_name} ({speaker_ip})")
        
        # Get the coordinator (handles grouped players)
        coordinator = get_coordinator(device)
        
        if coordinator != device:
            print(f"📡 Using coordinator: {coordinator.player_name} ({coordinator.ip_address})")
        else:
            print(f"🎵 Player is standalone or group coordinator")
            
    except Exception as e:
        print(f"❌ Error connecting to Sonos player at {speaker_ip}: {e}")
        sys.exit(1)
    
    try:
        # Get current track info from coordinator
        current_track = coordinator.get_current_track_info()
        print(f"\n🎧 Currently Playing:")
        print(f"   {current_track.get('title', 'Unknown')} — {current_track.get('artist', 'Unknown Artist')}")
        print(f"   Album: {current_track.get('album', 'Unknown Album')}")
        print(f"   Position: {current_track.get('position', '0:00')} / {format_duration(current_track.get('duration', ''))}")
        
        # Get queue from coordinator
        print(f"\n📋 Getting queue from coordinator...")
        queue = coordinator.get_queue()
        
        if not queue:
            print("   Queue is empty")
            return
            
        print(f"   Found {len(queue)} tracks in queue\n")
        
        # Get current queue position (more robust approach)
        current_queue_index = int(current_track.get("queue_position", 1)) - 1  # Convert to 0-based
        
        # Alternative: find current track by title/artist if queue_position is unreliable
        current_title = current_track.get('title', '').lower()
        current_artist = current_track.get('artist', '').lower()
        
        if current_title and current_artist:
            for i, item in enumerate(queue):
                item_title = getattr(item, 'title', '').lower()
                item_artist = getattr(item, 'creator', '').lower()
                if current_title in item_title and current_artist in item_artist:
                    current_queue_index = i
                    print(f"   📍 Found current track at queue position {i+1} by matching title/artist")
                    break
        
        print("🎵 Queue (🔹 = currently playing):")
        if show_artwork:
            print("🖼️  Artwork URLs will be shown below each track")
        print("=" * 80)
        
        # Debug: Show attributes of first item if requested
        if debug_mode and queue:
            print(f"\n🔧 Debug - First queue item attributes:")
            first_item = queue[0]
            for attr in dir(first_item):
                if not attr.startswith('_'):
                    try:
                        value = getattr(first_item, attr)
                        if not callable(value):
                            print(f"   {attr}: {value}")
                    except:
                        pass
            print()
        
        # Print all tracks with current track highlighted
        for i, item in enumerate(queue):
            # Mark current track
            marker = "🔹" if i == current_queue_index else "  "
            
            # Format track info - handle different attribute names
            title = getattr(item, 'title', None) or "Unknown Title"
            
            # Try different possible artist attribute names
            artist = (getattr(item, 'creator', None) or 
                     getattr(item, 'artist', None) or 
                     "Unknown Artist")
            
            # Try different possible album attribute names  
            album = (getattr(item, 'album', None) or 
                    getattr(item, 'album_title', None) or
                    "Unknown Album")
            
            # Duration might be in the resources
            duration = "Unknown"
            try:
                resources = getattr(item, 'resources', [])
                if resources and len(resources) > 0:
                    resource = resources[0]
                    duration_raw = getattr(resource, 'duration', None)
                    if duration_raw:
                        duration = format_duration(duration_raw)
            except:
                pass
            
            # Get artwork URL if available
            artwork_url = None
            try:
                album_art_uri = getattr(item, 'album_art_uri', None)
                if album_art_uri:
                    # Convert relative URI to full URL using coordinator's IP
                    if album_art_uri.startswith('/'):
                        artwork_url = f"http://{coordinator.ip_address}:1400{album_art_uri}"
                    else:
                        artwork_url = album_art_uri
            except:
                pass
            
            # Truncate long titles/artists for better formatting
            title = (title[:40] + "...") if len(title) > 43 else title
            artist = (artist[:25] + "...") if len(artist) > 28 else artist
            
            print(f"{marker} {i+1:3d}. {title:<45} — {artist:<30} ({duration})")
            
            # Show artwork URL if requested
            if show_artwork and artwork_url:
                print(f"       🖼️  {artwork_url}")
            
            # Show album if different from previous track
            if i == 0 or (i > 0 and getattr(queue[i-1], 'album', '') != album):
                print(f"       📀 {album}")
        
        print("=" * 80)
        print(f"Total: {len(queue)} tracks")
        
        # Show upcoming tracks summary
        remaining = len(queue) - current_queue_index - 1
        if remaining > 0:
            print(f"🔜 {remaining} tracks remaining after current song")
        else:
            print("🔚 Current track is the last in queue")
            
        # Show artwork note if enabled
        if show_artwork:
            print(f"🖼️  Artwork URLs shown for all tracks (use in media apps or browsers)")
        
        # Show usage tip for first-time users
        if not debug_mode and not show_artwork:
            print(f"\n💡 Tip: Use --artwork to see album artwork URLs, --debug for technical details")
            
    except Exception as e:
        print(f"❌ Error getting queue: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()