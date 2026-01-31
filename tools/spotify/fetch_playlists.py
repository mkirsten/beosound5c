#!/usr/bin/env python3
"""
Fetch public Spotify playlists and their tracks.
Uses client credentials flow - no user auth required.
Run via cron to keep playlists updated.
"""

import json
import os
import base64
import urllib.request
import urllib.error
from datetime import datetime

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
DIGIT_PLAYLISTS_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'digit_playlists.json')
OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'playlists_with_tracks.json')
LOG_FILE = os.path.join(SCRIPT_DIR, 'fetch.log')

# Spotify API credentials (client credentials flow - no user auth needed)
CLIENT_ID = '6420bddd82d046adb24b3009960c5d81'
CLIENT_SECRET = '0cbb9390b1c045878ec3a57d8bb32b76'

def log(msg):
    """Log with timestamp."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except:
        pass

def get_access_token():
    """Get Spotify access token using client credentials flow."""
    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()

    req = urllib.request.Request(
        'https://accounts.spotify.com/api/token',
        data=b'grant_type=client_credentials',
        headers={
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
        return data['access_token']

def fetch_playlist(token, playlist_id, max_tracks=100):
    """Fetch a playlist and its tracks."""
    headers = {'Authorization': f'Bearer {token}'}

    # Get playlist info
    req = urllib.request.Request(
        f'https://api.spotify.com/v1/playlists/{playlist_id}',
        headers=headers
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            playlist = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        log(f"  Error fetching playlist {playlist_id}: {e.code}")
        return None

    # Extract tracks
    tracks = []
    for item in playlist.get('tracks', {}).get('items', [])[:max_tracks]:
        track = item.get('track')
        if not track:
            continue

        url = track.get('external_urls', {}).get('spotify')
        if not url:
            continue

        tracks.append({
            'name': track['name'],
            'artist': ', '.join([a['name'] for a in track.get('artists', []) if a.get('name')]),
            'id': track['id'],
            'url': url,
            'image': track['album']['images'][0]['url'] if track.get('album', {}).get('images') else None
        })

    return {
        'name': playlist['name'],
        'id': playlist['id'],
        'url': playlist.get('external_urls', {}).get('spotify', ''),
        'image': playlist['images'][0]['url'] if playlist.get('images') else None,
        'tracks': tracks
    }

def main():
    log("=== Spotify Playlist Fetch Starting ===")

    # Load digit playlist mapping
    if not os.path.exists(DIGIT_PLAYLISTS_FILE):
        log(f"ERROR: Digit playlists file not found: {DIGIT_PLAYLISTS_FILE}")
        return 1

    with open(DIGIT_PLAYLISTS_FILE, 'r') as f:
        digit_mapping = json.load(f)

    log(f"Loaded {len(digit_mapping)} digit mappings")

    # Get access token
    try:
        token = get_access_token()
        log("Got Spotify access token")
    except Exception as e:
        log(f"ERROR: Failed to get access token: {e}")
        return 1

    # Fetch each playlist
    playlists = []
    for digit, info in sorted(digit_mapping.items()):
        playlist_id = info['id']
        log(f"Fetching digit {digit}: {info.get('name', playlist_id)}")

        playlist_data = fetch_playlist(token, playlist_id)
        if playlist_data:
            playlist_data['digit'] = digit  # Add digit reference
            playlists.append(playlist_data)
            log(f"  Got {len(playlist_data['tracks'])} tracks")
        else:
            log(f"  FAILED")

    # Update digit_playlists.json with fetched names
    for playlist in playlists:
        digit = playlist.get('digit')
        if digit and digit in digit_mapping:
            digit_mapping[digit]['name'] = playlist['name']
            digit_mapping[digit]['image'] = playlist.get('image')

    with open(DIGIT_PLAYLISTS_FILE, 'w') as f:
        json.dump(digit_mapping, f, indent=2)
    log(f"Updated {DIGIT_PLAYLISTS_FILE}")

    # Write playlists with tracks
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(playlists, f, indent=2)

    log(f"Success: {len(playlists)} playlists saved to {OUTPUT_FILE}")
    return 0

if __name__ == '__main__':
    exit(main())
