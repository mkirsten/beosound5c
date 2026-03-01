#!/usr/bin/env python3
"""
Fetch all Apple Music library playlists for the authenticated user.
Auto-detects digit playlists by name pattern (e.g., "5: Dinner" -> digit 5).
Run via beo-source-apple-music service to keep playlists updated.

Token source: --developer-token and --user-token from the service process.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'services'))

from lib.digit_playlists import detect_digit_playlist, build_digit_mapping

DIGIT_PLAYLISTS_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'apple_music_digit_playlists.json')
DEFAULT_OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'apple_music_playlists.json')

API_BASE = 'https://api.music.apple.com/v1'
RATE_LIMIT_DELAY = 2  # seconds between requests (Apple enforces this)


def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")


def _api_request(url, developer_token, user_token, retries=3):
    """Make an authenticated Apple Music API request with retry on 429."""
    headers = {
        'Authorization': f'Bearer {developer_token}',
        'Music-User-Token': user_token,
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get('Retry-After', RATE_LIMIT_DELAY * 2))
                log(f"  Rate limited, waiting {retry_after}s...")
                time.sleep(retry_after)
                continue
            if e.code == 401:
                log(f"  ERROR: Unauthorized (401) — user token may be expired")
                raise
            if e.code == 403:
                log(f"  ERROR: Forbidden (403) — Apple Music subscription may be required")
                raise
            log(f"  HTTP error {e.code}: {e.reason}")
            if attempt < retries - 1:
                time.sleep(RATE_LIMIT_DELAY)
                continue
            raise
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(RATE_LIMIT_DELAY)
                continue
            raise
    return None


def fetch_storefront(developer_token, user_token):
    """Detect the user's storefront (country code)."""
    data = _api_request(f'{API_BASE}/me/storefront', developer_token, user_token)
    if data and data.get('data'):
        sf = data['data'][0]['id']
        log(f"Detected storefront: {sf}")
        return sf
    return 'us'


def fetch_playlist_tracks(developer_token, user_token, playlist_id, storefront):
    """Fetch all tracks for a library playlist (handles pagination)."""
    tracks = []
    url = f'{API_BASE}/me/library/playlists/{playlist_id}/tracks?limit=100'

    while url:
        time.sleep(RATE_LIMIT_DELAY)
        try:
            data = _api_request(url, developer_token, user_token)
            if not data:
                break

            for item in data.get('data', []):
                attrs = item.get('attributes', {})
                name = attrs.get('name', 'Unknown')
                artist = attrs.get('artistName', 'Unknown')
                # Catalog ID for building Apple Music URLs
                catalog_id = None
                play_params = attrs.get('playParams', {})
                if play_params:
                    catalog_id = play_params.get('catalogId') or play_params.get('id')

                # Artwork URL (replace {w}x{h} placeholder)
                artwork = attrs.get('artwork', {})
                image = None
                if artwork and artwork.get('url'):
                    image = artwork['url'].replace('{w}', '300').replace('{h}', '300')

                track_url = None
                if catalog_id:
                    slug = name.lower().replace(' ', '-').replace('/', '-')[:50]
                    track_url = f'https://music.apple.com/{storefront}/song/{slug}/{catalog_id}'

                tracks.append({
                    'name': name,
                    'artist': artist,
                    'id': catalog_id or item.get('id', ''),
                    'url': track_url,
                    'image': image,
                })

            # Pagination
            url = data.get('next')
            if url and not url.startswith('http'):
                url = f'{API_BASE}{url}'
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise
            log(f"  Error fetching tracks: {e}")
            break
        except Exception as e:
            log(f"  Error fetching tracks: {e}")
            break

    return tracks


def fetch_user_playlists(developer_token, user_token):
    """Fetch all library playlists (handles pagination)."""
    playlists = []
    page_limit = 100
    url = f'{API_BASE}/me/library/playlists?limit={page_limit}'
    page = 0

    while url:
        time.sleep(RATE_LIMIT_DELAY)
        page += 1
        try:
            data = _api_request(url, developer_token, user_token)
            if not data:
                log(f"  Page {page}: no data returned")
                break

            page_items = data.get('data', [])
            for pl in page_items:
                attrs = pl.get('attributes', {})
                artwork = attrs.get('artwork', {})
                image = None
                if artwork and artwork.get('url'):
                    image = artwork['url'].replace('{w}', '300').replace('{h}', '300')

                # catalogId for building shareable URLs
                play_params = attrs.get('playParams', {})
                catalog_id = play_params.get('catalogId')

                playlists.append({
                    'id': pl['id'],
                    'name': attrs.get('name', 'Untitled'),
                    'url': None,  # filled in later with catalog_id + storefront
                    'image': image,
                    'catalog_id': catalog_id,
                    'lastModifiedDate': attrs.get('lastModifiedDate', ''),
                })

            next_url = data.get('next')
            if next_url:
                if not next_url.startswith('http'):
                    next_url = f'{API_BASE}{next_url}'
                # Ensure limit is preserved across pages
                if 'limit=' not in next_url:
                    next_url += f'&limit={page_limit}' if '?' in next_url else f'?limit={page_limit}'
                log(f"  Page {page}: {len(page_items)} playlists (continuing...)")
                url = next_url
            else:
                log(f"  Page {page}: {len(page_items)} playlists (last page)")
                url = None
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise
            log(f"  Page {page}: HTTP error {e.code} — stopping pagination ({len(playlists)} so far)")
            break
        except Exception as e:
            log(f"  Page {page}: error {e} — stopping pagination ({len(playlists)} so far)")
            break

    return playlists




def main():
    force = '--force' in sys.argv

    output_file = DEFAULT_OUTPUT_FILE
    if '--output' in sys.argv:
        idx = sys.argv.index('--output')
        if idx + 1 < len(sys.argv):
            output_file = sys.argv[idx + 1]

    developer_token = None
    if '--developer-token' in sys.argv:
        idx = sys.argv.index('--developer-token')
        if idx + 1 < len(sys.argv):
            developer_token = sys.argv[idx + 1]

    user_token = None
    if '--user-token' in sys.argv:
        idx = sys.argv.index('--user-token')
        if idx + 1 < len(sys.argv):
            user_token = sys.argv[idx + 1]

    if not developer_token or not user_token:
        log("ERROR: --developer-token and --user-token are required")
        return 1

    log("=== Apple Music Playlist Fetch Starting ===")
    if force:
        log("Force mode: fetching all tracks regardless of cache")

    # Detect storefront
    try:
        storefront = fetch_storefront(developer_token, user_token)
    except Exception as e:
        log(f"ERROR: Could not detect storefront: {e}")
        return 1

    # Load cached data for incremental sync
    cache = {}
    if not force and os.path.exists(output_file):
        try:
            with open(output_file, 'r') as f:
                cached_playlists = json.load(f)
            for cp in cached_playlists:
                cache[cp['id']] = {
                    'lastModifiedDate': cp.get('lastModifiedDate', ''),
                    'tracks': cp.get('tracks', []),
                }
            log(f"Loaded cache with {len(cache)} playlists")
        except Exception as e:
            log(f"Could not load cache: {e}")

    # Fetch all user's library playlists
    log("Fetching playlists for authenticated user")
    all_playlists = fetch_user_playlists(developer_token, user_token)
    log(f"Found {len(all_playlists)} playlists")

    # Build shareable URLs
    for pl in all_playlists:
        if pl.get('catalog_id'):
            slug = pl['name'].lower().replace(' ', '-').replace('/', '-')[:50]
            pl['url'] = f"https://music.apple.com/{storefront}/playlist/{slug}/{pl['catalog_id']}"

    # Split into cached vs needs-fetch
    playlists_with_tracks = []
    to_fetch = []
    skipped = 0

    for pl in all_playlists:
        cached = cache.get(pl['id'])
        if (cached and cached['lastModifiedDate']
                and cached['lastModifiedDate'] == pl.get('lastModifiedDate', '')):
            pl['tracks'] = cached['tracks']
            playlists_with_tracks.append(pl)
            log(f"  {pl['name']} (unchanged)")
            skipped += 1
        else:
            to_fetch.append(pl)

    # Fetch tracks (sequential with rate limiting — Apple enforces 1 req/2s)
    fetched = 0
    if to_fetch:
        log(f"Fetching tracks for {len(to_fetch)} playlists...")
        for pl in to_fetch:
            try:
                tracks = fetch_playlist_tracks(developer_token, user_token, pl['id'], storefront)
                pl['tracks'] = tracks
                playlists_with_tracks.append(pl)
                log(f"  {pl['name']}: {len(tracks)} tracks")
                fetched += 1
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    log("ERROR: User token expired (401)")
                    return 1
                log(f"  {pl['name']}: ERROR {e}")
                pl['tracks'] = []
                playlists_with_tracks.append(pl)
                fetched += 1
            except Exception as e:
                log(f"  {pl['name']}: ERROR {e}")
                pl['tracks'] = []
                playlists_with_tracks.append(pl)
                fetched += 1

    log(f"Fetched {fetched}, skipped {skipped} unchanged")

    # Filter out empty playlists
    before = len(playlists_with_tracks)
    playlists_with_tracks = [p for p in playlists_with_tracks if p.get('tracks')]
    if before != len(playlists_with_tracks):
        log(f"Filtered out {before - len(playlists_with_tracks)} empty playlists")

    # Remove internal fields before saving
    for pl in playlists_with_tracks:
        pl.pop('catalog_id', None)

    # Sort by name
    playlists_with_tracks.sort(key=lambda p: p['name'].lower())

    # Skip write if nothing changed
    if fetched == 0 and len(playlists_with_tracks) == len(cache):
        log("No changes — skipping disk write")
        return 0

    # Save all playlists
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(playlists_with_tracks, f, indent=2)
    log(f"Saved {len(playlists_with_tracks)} playlists to {output_file}")

    # Build digit mapping
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
