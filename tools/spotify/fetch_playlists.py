#!/usr/bin/env python3
"""
Fetch all Spotify playlists for the authenticated user.
Auto-detects digit playlists by name pattern (e.g., "5: Dinner" -> digit 5).
Uses PKCE refresh token flow — no client_secret needed.
Run via cron to keep playlists updated.

Token sources (in priority order):
  1. spotify_tokens.json (PKCE flow, preferred)
  2. Environment variables (legacy compat: SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET + SPOTIFY_REFRESH_TOKEN)
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
sys.path.insert(0, SCRIPT_DIR)

from token_store import load_tokens, save_tokens

DIGIT_PLAYLISTS_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'digit_playlists.json')
DEFAULT_OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'playlists_with_tracks.json')
LOG_FILE = os.path.join(SCRIPT_DIR, 'fetch.log')


def log(msg):
    """Log with timestamp."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def get_access_token():
    """Get a Spotify access token. Tries PKCE first, then legacy env vars."""
    # 1. Try PKCE token store
    tokens = load_tokens()
    if tokens and tokens.get('client_id') and tokens.get('refresh_token'):
        client_id = tokens['client_id']
        refresh_token = tokens['refresh_token']
        log(f"Using PKCE tokens (client_id: {client_id[:8]}...)")

        from pkce import refresh_access_token
        result = refresh_access_token(client_id, refresh_token)

        # Persist rotated refresh token if provided
        new_rt = result.get('refresh_token')
        if new_rt and new_rt != refresh_token:
            save_tokens(client_id, new_rt)
            log("Refresh token rotated and saved")

        return result['access_token']

    # 2. Legacy fallback: env vars with client_secret
    client_id = os.getenv('SPOTIFY_CLIENT_ID', '')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET', '')
    refresh_token = os.getenv('SPOTIFY_REFRESH_TOKEN', '')

    if not refresh_token:
        raise ValueError(
            "No Spotify credentials found. Run setup_spotify.py first, "
            "or set SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET + SPOTIFY_REFRESH_TOKEN."
        )

    if client_secret:
        # Legacy: client_secret flow
        import base64
        log("Using legacy env var credentials (client_secret)")
        auth_str = f"{client_id}:{client_secret}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        data = urllib.parse.urlencode({
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
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
            result = json.loads(resp.read().decode())
            return result['access_token']
    else:
        # Env vars without secret: try PKCE-style refresh
        log("Using env var credentials (PKCE, no secret)")
        from pkce import refresh_access_token
        result = refresh_access_token(client_id, refresh_token)
        return result['access_token']


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
                'uri': track.get('uri', ''),
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
                    'uri': pl.get('uri', ''),
                    'url': pl.get('external_urls', {}).get('spotify', ''),
                    'image': pl['images'][0]['url'] if pl.get('images') else None,
                    'owner': pl.get('owner', {}).get('id', ''),
                    'public': pl.get('public', False),
                    'snapshot_id': pl.get('snapshot_id', '')
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
    force = '--force' in sys.argv

    # Parse --output <path> argument
    output_file = DEFAULT_OUTPUT_FILE
    if '--output' in sys.argv:
        idx = sys.argv.index('--output')
        if idx + 1 < len(sys.argv):
            output_file = sys.argv[idx + 1]

    log("=== Spotify Playlist Fetch Starting ===")
    if force:
        log("Force mode: fetching all tracks regardless of snapshot")

    # Get access token
    try:
        token = get_access_token()
        log("Got Spotify access token")
    except Exception as e:
        log(f"ERROR: Failed to get access token: {e}")
        return 1

    # Load cached data for incremental sync
    cache = {}
    if not force and os.path.exists(output_file):
        try:
            with open(output_file, 'r') as f:
                cached_playlists = json.load(f)
            for cp in cached_playlists:
                cache[cp['id']] = {
                    'snapshot_id': cp.get('snapshot_id', ''),
                    'tracks': cp.get('tracks', [])
                }
            log(f"Loaded cache with {len(cache)} playlists")
        except Exception as e:
            log(f"Could not load cache: {e}")

    # Fetch all user's playlists
    log("Fetching playlists for authenticated user")
    all_playlists = fetch_user_playlists(token)
    log(f"Found {len(all_playlists)} playlists")

    # Fetch tracks for each playlist and detect digit playlists
    playlists_with_tracks = []
    digit_mapping = {}
    fetched = 0
    skipped = 0

    for pl in all_playlists:
        cached = cache.get(pl['id'])
        if cached and cached['snapshot_id'] and cached['snapshot_id'] == pl.get('snapshot_id', ''):
            pl['tracks'] = cached['tracks']
            playlists_with_tracks.append(pl)
            log(f"  {pl['name']} (unchanged)")
            skipped += 1
        else:
            log(f"Fetching tracks: {pl['name']}")
            tracks = fetch_playlist_tracks(token, pl['id'])
            pl['tracks'] = tracks
            playlists_with_tracks.append(pl)
            log(f"  Got {len(tracks)} tracks")
            fetched += 1

        # Check if this is a digit playlist
        digit = detect_digit_playlist(pl['name'])
        if digit:
            digit_mapping[digit] = {
                'id': pl['id'],
                'name': pl['name'],
                'image': pl.get('image')
            }
            log(f"  -> Digit {digit} playlist")

    log(f"Fetched {fetched}, skipped {skipped} unchanged")

    # Filter out empty playlists (no tracks)
    before = len(playlists_with_tracks)
    playlists_with_tracks = [p for p in playlists_with_tracks if p.get('tracks')]
    if before != len(playlists_with_tracks):
        log(f"Filtered out {before - len(playlists_with_tracks)} empty playlists")

    # Sort by name
    playlists_with_tracks.sort(key=lambda p: p['name'].lower())

    # Skip write if nothing changed (no tracks fetched, same playlist count)
    if fetched == 0 and len(playlists_with_tracks) == len(cache):
        log(f"No changes — skipping disk write")
        return 0

    # Save all playlists
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(playlists_with_tracks, f, indent=2)
    log(f"Saved {len(playlists_with_tracks)} playlists to {output_file}")

    # Save digit mapping
    with open(DIGIT_PLAYLISTS_FILE, 'w') as f:
        json.dump(digit_mapping, f, indent=2)
    log(f"Saved {len(digit_mapping)} digit playlists to {DIGIT_PLAYLISTS_FILE}")

    log("=== Done ===")
    return 0

if __name__ == '__main__':
    exit(main())
