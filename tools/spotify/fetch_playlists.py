#!/usr/bin/env python3
"""
Fetch all Spotify playlists for the authenticated user.
Auto-detects digit playlists by name pattern (e.g., "5: Dinner" → digit 5).
Uses OAuth refresh token flow - requires running setup_spotify.py first.
Run via cron to keep playlists updated.
"""

import json
import os
import re
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

# Spotify refresh token (obtained via setup_spotify.py OAuth flow)
REFRESH_TOKEN = os.getenv('SPOTIFY_REFRESH_TOKEN', '')

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
DIGIT_PLAYLISTS_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'digit_playlists.json')
OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'playlists_with_tracks.json')
LOG_FILE = os.path.join(SCRIPT_DIR, 'fetch.log')

# Spotify API credentials (OAuth flow - run setup_spotify.py first)
# Get your credentials at https://developer.spotify.com/dashboard
CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID', '')
CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', '')

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
    """Get Spotify access token using refresh token flow."""
    if not REFRESH_TOKEN:
        raise ValueError("SPOTIFY_REFRESH_TOKEN not set. Run setup_spotify.py first.")

    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()

    data = urllib.parse.urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': REFRESH_TOKEN
    }).encode()

    req = urllib.request.Request(
        'https://accounts.spotify.com/api/token',
        data=data,
        headers={
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
        return data['access_token']

def fetch_playlist_tracks(token, playlist_id, max_tracks=100):
    """Fetch tracks for a playlist."""
    headers = {'Authorization': f'Bearer {token}'}
    tracks = []

    try:
        req = urllib.request.Request(
            f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=50',
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        for item in data.get('items', [])[:max_tracks]:
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
    except Exception as e:
        log(f"  Error fetching tracks: {e}")

    return tracks

def fetch_user_playlists(token):
    """Fetch all playlists for the authenticated user."""
    headers = {'Authorization': f'Bearer {token}'}
    playlists = []
    url = 'https://api.spotify.com/v1/me/playlists?limit=50'

    while url:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            for pl in data.get('items', []):
                if not pl:
                    continue
                playlists.append({
                    'id': pl['id'],
                    'name': pl['name'],
                    'url': pl.get('external_urls', {}).get('spotify', ''),
                    'image': pl['images'][0]['url'] if pl.get('images') else None,
                    'owner': pl.get('owner', {}).get('id', ''),
                    'public': pl.get('public', False)
                })

            url = data.get('next')  # Pagination
        except Exception as e:
            log(f"Error fetching playlists: {e}")
            break

    return playlists

def detect_digit_playlist(name):
    """Check if playlist name starts with a digit pattern like '5:' or '5 -'.
    Returns the digit (0-9) or None."""
    match = re.match(r'^(\d)[\s]*[:\-]', name)
    if match:
        return match.group(1)
    return None

def main():
    log("=== Spotify Playlist Fetch Starting ===")

    # Get access token
    try:
        token = get_access_token()
        log("Got Spotify access token")
    except Exception as e:
        log(f"ERROR: Failed to get access token: {e}")
        return 1

    # Fetch all user's playlists
    log("Fetching playlists for authenticated user")
    all_playlists = fetch_user_playlists(token)
    log(f"Found {len(all_playlists)} playlists")

    # Fetch tracks for each playlist and detect digit playlists
    playlists_with_tracks = []
    digit_mapping = {}

    for pl in all_playlists:
        log(f"Fetching tracks: {pl['name']}")
        tracks = fetch_playlist_tracks(token, pl['id'])
        pl['tracks'] = tracks
        playlists_with_tracks.append(pl)
        log(f"  Got {len(tracks)} tracks")

        # Check if this is a digit playlist
        digit = detect_digit_playlist(pl['name'])
        if digit:
            digit_mapping[digit] = {
                'id': pl['id'],
                'name': pl['name'],
                'image': pl.get('image')
            }
            log(f"  -> Digit {digit} playlist")

    # Sort by name
    playlists_with_tracks.sort(key=lambda p: p['name'].lower())

    # Save all playlists
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(playlists_with_tracks, f, indent=2)
    log(f"Saved {len(playlists_with_tracks)} playlists to {OUTPUT_FILE}")

    # Save digit mapping
    with open(DIGIT_PLAYLISTS_FILE, 'w') as f:
        json.dump(digit_mapping, f, indent=2)
    log(f"Saved {len(digit_mapping)} digit playlists to {DIGIT_PLAYLISTS_FILE}")

    log("=== Done ===")
    return 0

if __name__ == '__main__':
    exit(main())
