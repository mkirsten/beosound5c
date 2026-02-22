#!/usr/bin/env python3
"""
Fetch all Spotify playlists for the authenticated user.
Auto-detects digit playlists by name pattern (e.g., "5: Dinner" -> digit 5).
Run via cron or beo-source-spotify service to keep playlists updated.

Token source: auth.get_access_token() (PKCE token store or env vars).
Can also receive --access-token from the beo-source-spotify service.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, SCRIPT_DIR)

from auth import get_access_token

DIGIT_PLAYLISTS_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'digit_playlists.json')
DEFAULT_OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'spotify_playlists.json')


def log(msg):
    """Log with timestamp to stdout (captured by systemd journal or parent process)."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")


def fetch_playlist_tracks(token, playlist_id):
    """Fetch all tracks for a playlist (handles pagination + 429 retry)."""
    headers = {'Authorization': f'Bearer {token}'}
    tracks = []
    url = f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100'

    while url:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            for item in data.get('items', []):
                track = item.get('track')
                if not track:
                    continue
                ext_url = track.get('external_urls', {}).get('spotify')
                if not ext_url:
                    continue
                tracks.append({
                    'name': track['name'],
                    'artist': ', '.join([a['name'] for a in track.get('artists', []) if a.get('name')]),
                    'id': track['id'],
                    'uri': track.get('uri', ''),
                    'url': ext_url,
                    'image': track['album']['images'][0]['url'] if track.get('album', {}).get('images') else None
                })

            url = data.get('next')
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get('Retry-After', 2))
                time.sleep(retry_after)
                continue  # retry same URL
            log(f"  Error fetching tracks: {e}")
            break
        except Exception as e:
            log(f"  Error fetching tracks: {e}")
            break

    return tracks


def fetch_user_playlists(token):
    """Fetch all playlists for the authenticated user (handles pagination + 429 retry)."""
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
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get('Retry-After', 2))
                time.sleep(retry_after)
                continue
            log(f"Error fetching playlists: {e}")
            break
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


def build_digit_mapping(playlists):
    """Build digit 0-9 mapping. Explicitly named playlists (e.g. '5: Jazz')
    get pinned to their digit; remaining slots filled alphabetically."""
    pinned = {}
    pinned_ids = set()
    for pl in playlists:
        digit = detect_digit_playlist(pl['name'])
        if digit is not None and digit not in pinned:
            pinned[digit] = pl
            pinned_ids.add(pl['id'])

    remaining = iter(pl for pl in playlists if pl['id'] not in pinned_ids)

    mapping = {}
    for slot in "0123456789":
        if slot in pinned:
            pl = pinned[slot]
        else:
            pl = next(remaining, None)
            if not pl:
                continue
        mapping[slot] = {
            'id': pl['id'],
            'name': pl['name'],
            'image': pl.get('image'),
        }

    return mapping


def main():
    force = '--force' in sys.argv

    # Parse --output <path> argument
    output_file = DEFAULT_OUTPUT_FILE
    if '--output' in sys.argv:
        idx = sys.argv.index('--output')
        if idx + 1 < len(sys.argv):
            output_file = sys.argv[idx + 1]

    # Parse --access-token <token> argument (passed by beo-source-spotify service)
    access_token = None
    if '--access-token' in sys.argv:
        idx = sys.argv.index('--access-token')
        if idx + 1 < len(sys.argv):
            access_token = sys.argv[idx + 1]

    log("=== Spotify Playlist Fetch Starting ===")
    if force:
        log("Force mode: fetching all tracks regardless of snapshot")

    # Get access token
    try:
        if access_token:
            token = access_token
            log("Using provided access token")
        else:
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

    # Split into cached (unchanged) and needs-fetch
    playlists_with_tracks = []
    to_fetch = []
    skipped = 0

    for pl in all_playlists:
        cached = cache.get(pl['id'])
        if cached and cached['snapshot_id'] and cached['snapshot_id'] == pl.get('snapshot_id', ''):
            pl['tracks'] = cached['tracks']
            playlists_with_tracks.append(pl)
            log(f"  {pl['name']} (unchanged)")
            skipped += 1
        else:
            to_fetch.append(pl)

    # Fetch tracks in parallel (4 workers, respects Spotify rate limits)
    fetched = 0
    if to_fetch:
        log(f"Fetching tracks for {len(to_fetch)} playlists in parallel...")
        with ThreadPoolExecutor(max_workers=4) as pool:
            future_to_pl = {
                pool.submit(fetch_playlist_tracks, token, pl['id']): pl
                for pl in to_fetch
            }
            for future in as_completed(future_to_pl):
                pl = future_to_pl[future]
                try:
                    tracks = future.result()
                    pl['tracks'] = tracks
                    playlists_with_tracks.append(pl)
                    log(f"  {pl['name']}: {len(tracks)} tracks")
                    fetched += 1
                except Exception as e:
                    log(f"  {pl['name']}: ERROR {e}")
                    pl['tracks'] = []
                    playlists_with_tracks.append(pl)
                    fetched += 1

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

    # Build digit mapping: pinned names first, then fill alphabetically
    digit_mapping = build_digit_mapping(playlists_with_tracks)
    with open(DIGIT_PLAYLISTS_FILE, 'w') as f:
        json.dump(digit_mapping, f, indent=2)
    pinned = sum(1 for d in "0123456789"
                 if d in digit_mapping and detect_digit_playlist(digit_mapping[d]['name']) is not None)
    log(f"Saved digit playlists ({pinned} pinned, {len(digit_mapping) - pinned} auto-filled)")

    log("=== Done ===")
    return 0

if __name__ == '__main__':
    exit(main())
