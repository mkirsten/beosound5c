#!/usr/bin/env python3
"""
Shared playlist lookup utility for BeoSound 5c services.

Used by:
  - masterlink.py (imported as module)
  - bluetooth.py (imported as module)

Usage as CLI:
  python3 playlist_lookup.py <digit>
  # Returns spotify:playlist:ID or empty string
"""

import json
import os
import sys

BS5C_BASE_PATH = os.getenv('BS5C_BASE_PATH', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIGIT_PLAYLISTS_FILE = os.path.join(BS5C_BASE_PATH, "web/json/digit_playlists.json")


def get_playlist_uri(digit):
    """Get Spotify playlist URI by digit from digit_playlists.json mapping.

    Args:
        digit: The digit (0-9) as string or int

    Returns:
        Spotify URI string (e.g., "spotify:playlist:abc123") or None if not found
    """
    try:
        with open(DIGIT_PLAYLISTS_FILE, 'r') as f:
            mapping = json.load(f)
        if str(digit) in mapping:
            playlist_id = mapping[str(digit)].get('id')
            if playlist_id:
                return f"spotify:playlist:{playlist_id}"
    except Exception as e:
        print(f"Error reading digit playlists: {e}", file=sys.stderr)
    return None


if __name__ == "__main__":
    # CLI mode for bash scripts
    if len(sys.argv) != 2:
        print("Usage: playlist_lookup.py <digit>", file=sys.stderr)
        sys.exit(1)

    digit = sys.argv[1]
    uri = get_playlist_uri(digit)
    if uri:
        print(uri)
    # Exit 0 regardless - empty output means no playlist found
