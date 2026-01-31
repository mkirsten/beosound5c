#!/usr/bin/env python3
"""
Spotify Playlist Refresh Script
Fetches all playlists for the authenticated user and saves to JSON.
Designed to run as a cron job.

First run requires interactive auth - run manually once to cache token:
  cd ~/beosound5c/tools/spotify && python3 refresh_playlists.py
"""

import json
import os
import sys
from datetime import datetime

# Get script directory for relative paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'playlists_with_tracks.json')
TOKEN_CACHE = os.path.join(SCRIPT_DIR, '.spotify_token_cache')
LOG_FILE = os.path.join(SCRIPT_DIR, 'refresh.log')

def log(msg):
    """Log message with timestamp."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except:
        pass

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    log("ERROR: spotipy not installed. Run: pip install spotipy")
    sys.exit(1)

# Spotify app credentials
CLIENT_ID = '6420bddd82d046adb24b3009960c5d81'
CLIENT_SECRET = '0cbb9390b1c045878ec3a57d8bb32b76'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = 'playlist-read-private playlist-read-collaborative'

def get_playlist_tracks(sp, playlist_id, max_tracks=100):
    """Fetch tracks for a playlist (limited to avoid huge files)."""
    tracks = []
    try:
        results = sp.playlist_tracks(playlist_id, limit=50)
        while results and len(tracks) < max_tracks:
            for item in results['items']:
                if len(tracks) >= max_tracks:
                    break
                track = item.get('track')
                if not track:
                    continue
                url = track.get('external_urls', {}).get('spotify')
                if not url:
                    continue
                tracks.append({
                    'name': track['name'],
                    'artist': ', '.join([a['name'] for a in track.get('artists', []) if a and a.get('name')]),
                    'id': track['id'],
                    'url': url,
                    'image': track['album']['images'][0]['url'] if track.get('album', {}).get('images') else None
                })
            if len(tracks) < max_tracks:
                results = sp.next(results)
            else:
                break
    except Exception as e:
        log(f"  Warning: Failed to fetch tracks: {e}")
    return tracks

def main():
    log("=== Spotify Playlist Refresh Starting ===")

    # Check if token cache exists
    if not os.path.exists(TOKEN_CACHE):
        log(f"ERROR: No token cache found at {TOKEN_CACHE}")
        log("Run this script interactively first to authenticate:")
        log(f"  cd {SCRIPT_DIR} && python3 refresh_playlists.py")
        sys.exit(1)

    try:
        # Initialize Spotify client
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope=SCOPE,
            cache_path=TOKEN_CACHE,
            open_browser=False  # Don't try to open browser in cron
        ))

        # Test authentication
        user = sp.current_user()
        log(f"Authenticated as: {user['display_name']} ({user['id']})")

        # Fetch playlists
        playlists = []
        results = sp.current_user_playlists(limit=50)

        while results:
            for pl in results['items']:
                if not pl:
                    continue
                log(f"Fetching: {pl['name']}")
                playlists.append({
                    'name': pl['name'],
                    'id': pl['id'],
                    'url': pl.get('external_urls', {}).get('spotify', ''),
                    'image': pl['images'][0]['url'] if pl.get('images') else None,
                    'tracks': get_playlist_tracks(sp, pl['id'])
                })
            results = sp.next(results)

        # Sort playlists by name for consistent ordering
        playlists.sort(key=lambda p: p['name'].lower())

        # Write output
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(playlists, f, indent=2)

        log(f"Success: {len(playlists)} playlists saved to {OUTPUT_FILE}")

    except spotipy.SpotifyException as e:
        log(f"ERROR: Spotify API error: {e}")
        sys.exit(1)
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
