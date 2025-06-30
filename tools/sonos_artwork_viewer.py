#!/usr/bin/env python3
"""
Sonos Artwork Viewer
A standalone tool to fetch and display artwork from a Sonos player directly.
Bypasses Home Assistant proxy issues by connecting directly to the Sonos device.

Usage: python3 sonos_artwork_viewer.py [--ip SONOS_IP] [--save] [--info]

Requirements:
    pip install soco pillow requests

Author: Assistant
"""

import argparse
import sys
import os
import requests
from io import BytesIO
import time
from urllib.parse import urlparse

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

class SonosArtworkViewer:
    def __init__(self, sonos_ip):
        self.sonos_ip = sonos_ip
        self.sonos = SoCo(sonos_ip)
        
    def get_current_track_info(self):
        """Get current track information from Sonos player."""
        try:
            track_info = self.sonos.get_current_track_info()
            return track_info
        except Exception as e:
            print(f"Error getting track info: {e}")
            return None
    
    def get_artwork_url(self):
        """Get the artwork URL for the currently playing track."""
        track_info = self.get_current_track_info()
        if not track_info:
            return None
            
        artwork_url = track_info.get('album_art', '')
        if not artwork_url:
            print("No artwork URL found for current track")
            return None
        
        # Handle relative URLs by making them absolute
        if artwork_url.startswith('/'):
            artwork_url = f"http://{self.sonos_ip}:1400{artwork_url}"
        
        return artwork_url
    
    def fetch_artwork(self, url):
        """Fetch artwork from URL and return as PIL Image."""
        try:
            print(f"Fetching artwork from: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            if len(response.content) == 0:
                print("ERROR: Artwork URL returned 0 bytes")
                return None
            
            print(f"Downloaded {len(response.content)} bytes of artwork data")
            
            # Create PIL Image from response content
            image = Image.open(BytesIO(response.content))
            return image
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching artwork: {e}")
            return None
        except Exception as e:
            print(f"Error processing artwork: {e}")
            return None
    
    def display_artwork(self, image):
        """Display artwork using PIL's built-in viewer."""
        try:
            print("Displaying artwork...")
            image.show()
        except Exception as e:
            print(f"Error displaying artwork: {e}")
    
    def save_artwork(self, image, filename=None):
        """Save artwork to file."""
        if not filename:
            timestamp = int(time.time())
            filename = f"sonos_artwork_{timestamp}.jpg"
        
        try:
            # Convert to RGB if necessary (for JPEG saving)
            if image.mode in ('RGBA', 'LA', 'P'):
                rgb_image = Image.new('RGB', image.size, (255, 255, 255))
                if image.mode == 'P':
                    image = image.convert('RGBA')
                rgb_image.paste(image, mask=image.split()[-1] if image.mode == 'RGBA' else None)
                image = rgb_image
            
            image.save(filename, 'JPEG', quality=95)
            print(f"Artwork saved to: {filename}")
            return filename
        except Exception as e:
            print(f"Error saving artwork: {e}")
            return None
    
    def print_track_info(self):
        """Print detailed track information."""
        track_info = self.get_current_track_info()
        if not track_info:
            print("No track information available")
            return
        
        print("\n" + "="*50)
        print("CURRENT TRACK INFORMATION")
        print("="*50)
        
        for key, value in track_info.items():
            if value:  # Only show non-empty values
                print(f"{key.replace('_', ' ').title()}: {value}")
        
        print("="*50)
    
    def get_player_info(self):
        """Get Sonos player information."""
        try:
            player_name = self.sonos.player_name
            volume = self.sonos.volume
            state = self.sonos.get_current_transport_info()['current_transport_state']
            
            print(f"\nPlayer: {player_name}")
            print(f"IP: {self.sonos_ip}")
            print(f"Volume: {volume}")
            print(f"State: {state}")
            
        except Exception as e:
            print(f"Error getting player info: {e}")

def main():
    parser = argparse.ArgumentParser(
        description="Fetch and display artwork from Sonos player",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 sonos_artwork_viewer.py --ip 192.168.1.111
  python3 sonos_artwork_viewer.py --ip 192.168.1.111 --save
  python3 sonos_artwork_viewer.py --ip 192.168.1.111 --info --save
        """
    )
    
    parser.add_argument(
        '--ip', 
        default='192.168.1.111',
        help='Sonos player IP address (default: 192.168.1.111)'
    )
    
    parser.add_argument(
        '--save', 
        action='store_true',
        help='Save artwork to file'
    )
    
    parser.add_argument(
        '--info', 
        action='store_true',
        help='Display detailed track information'
    )
    
    parser.add_argument(
        '--no-display', 
        action='store_true',
        help='Don\'t display artwork (useful with --save)'
    )
    
    parser.add_argument(
        '--output', 
        help='Output filename for saved artwork'
    )
    
    args = parser.parse_args()
    
    print(f"Connecting to Sonos player at {args.ip}...")
    
    try:
        viewer = SonosArtworkViewer(args.ip)
        
        # Get player info
        viewer.get_player_info()
        
        # Print track info if requested
        if args.info:
            viewer.print_track_info()
        
        # Get artwork URL
        artwork_url = viewer.get_artwork_url()
        if not artwork_url:
            print("No artwork available for current track")
            return
        
        # Fetch artwork
        image = viewer.fetch_artwork(artwork_url)
        if not image:
            print("Failed to fetch artwork")
            return
        
        print(f"Artwork size: {image.size[0]}x{image.size[1]} pixels")
        print(f"Artwork mode: {image.mode}")
        
        # Save artwork if requested
        if args.save:
            saved_file = viewer.save_artwork(image, args.output)
            if saved_file:
                print(f"Artwork saved successfully to {saved_file}")
        
        # Display artwork unless disabled
        if not args.no_display:
            viewer.display_artwork(image)
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 