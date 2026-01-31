#!/usr/bin/env python3
"""
Fetch public Spotify playlists and their tracks.
Uses client credentials flow - no user auth required.
Run via cron to keep playlists updated.

Fetches:
1. All public playlists from the configured user
2. Updates digit_playlists.json with track data for quick-access buttons
"""

import json
import os
import base64
import urllib.request
import urllib.error
from datetime import datetime

# Spotify user ID to fetch playlists from
SPOTIFY_USER_ID = "mkirsten"

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

def fetch_user_playlists(token, user_id):
    """Fetch all public playlists for a user."""
    headers = {'Authorization': f'Bearer {token}'}
    playlists = []
    url = f'https://api.spotify.com/v1/users/{user_id}/playlists?limit=50'

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

def fetch_playlist_info(token, playlist_id):
    """Fetch a single playlist's info."""
    headers = {'Authorization': f'Bearer {token}'}
    try:
        req = urllib.request.Request(
            f'https://api.spotify.com/v1/playlists/{playlist_id}',
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pl = json.loads(resp.read().decode())
            return {
                'id': pl['id'],
                'name': pl['name'],
                'url': pl.get('external_urls', {}).get('spotify', ''),
                'image': pl['images'][0]['url'] if pl.get('images') else None,
                'owner': pl.get('owner', {}).get('id', ''),
                'public': pl.get('public', False)
            }
    except Exception as e:
        log(f"Error fetching playlist {playlist_id}: {e}")
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

    # Collect all playlist IDs to fetch
    playlist_ids = set()

    # 1. Get digit playlists (curated, may not be owned by user)
    if os.path.exists(DIGIT_PLAYLISTS_FILE):
        with open(DIGIT_PLAYLISTS_FILE, 'r') as f:
            digit_mapping = json.load(f)
        for info in digit_mapping.values():
            if info.get('id'):
                playlist_ids.add(info['id'])
        log(f"Added {len(playlist_ids)} digit playlists")

    # 2. Get user's own playlists
    log(f"Fetching playlists for user: {SPOTIFY_USER_ID}")
    user_playlists = fetch_user_playlists(token, SPOTIFY_USER_ID)
    for pl in user_playlists:
        playlist_ids.add(pl['id'])
    log(f"Added {len(user_playlists)} user playlists (total: {len(playlist_ids)})")

    # Fetch full info and tracks for all playlists
    playlists_with_tracks = []
    for pl_id in playlist_ids:
        # Check if we already have info from user playlists
        pl = next((p for p in user_playlists if p['id'] == pl_id), None)
        if not pl:
            pl = fetch_playlist_info(token, pl_id)
        if not pl:
            continue

        log(f"Fetching tracks: {pl['name']}")
        tracks = fetch_playlist_tracks(token, pl['id'])
        pl['tracks'] = tracks
        playlists_with_tracks.append(pl)
        log(f"  Got {len(tracks)} tracks")

    # Sort by name
    playlists_with_tracks.sort(key=lambda p: p['name'].lower())

    # Save all playlists
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(playlists_with_tracks, f, indent=2)
    log(f"Saved {len(playlists_with_tracks)} playlists to {OUTPUT_FILE}")

    # Update digit_playlists.json with latest info from fetched data
    if os.path.exists(DIGIT_PLAYLISTS_FILE):
        with open(DIGIT_PLAYLISTS_FILE, 'r') as f:
            digit_mapping = json.load(f)

        # Update names/images from fetched data
        playlist_lookup = {p['id']: p for p in playlists_with_tracks}
        for digit, info in digit_mapping.items():
            pl_id = info.get('id')
            if pl_id and pl_id in playlist_lookup:
                fetched = playlist_lookup[pl_id]
                digit_mapping[digit]['name'] = fetched['name']
                digit_mapping[digit]['image'] = fetched.get('image')

        with open(DIGIT_PLAYLISTS_FILE, 'w') as f:
            json.dump(digit_mapping, f, indent=2)
        log(f"Updated {DIGIT_PLAYLISTS_FILE}")

    log("=== Done ===")
    return 0

if __name__ == '__main__':
    exit(main())
