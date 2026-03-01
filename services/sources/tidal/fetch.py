#!/usr/bin/env python3
"""
Fetch all TIDAL playlists for the authenticated user.
Auto-detects digit playlists by name pattern (e.g., "5: Dinner" -> digit 5).
Run via beo-source-tidal service to keep playlists updated.

Token source: --token-file <path> pointing to tidal_tokens.json.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'services'))

from lib.digit_playlists import detect_digit_playlist, build_digit_mapping

DIGIT_PLAYLISTS_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'tidal_digit_playlists.json')
DEFAULT_OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'tidal_playlists.json')


def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")


def fetch_playlists(session):
    """Fetch user's own playlists and favorite playlists."""
    playlists = []
    seen_ids = set()

    # User-created playlists
    try:
        user_playlists = session.user.playlists() or []
        for pl in user_playlists:
            if pl.id in seen_ids:
                continue
            seen_ids.add(pl.id)
            playlists.append(pl)
    except Exception as e:
        log(f"Error fetching user playlists: {e}")

    # Favorite playlists
    try:
        fav_playlists = session.user.favorites.playlists() or []
        for pl in fav_playlists:
            if pl.id in seen_ids:
                continue
            seen_ids.add(pl.id)
            playlists.append(pl)
    except Exception as e:
        log(f"Error fetching favorite playlists: {e}")

    return playlists


def fetch_playlist_tracks(playlist):
    """Fetch all tracks for a playlist."""
    tracks = []
    try:
        items = playlist.tracks() or []
        for track in items:
            name = track.name or 'Unknown'
            artist = 'Unknown'
            if track.artists:
                artist = ', '.join(a.name for a in track.artists if a.name)

            image = None
            if track.album and track.album.image:
                try:
                    image = track.album.image(640)
                except Exception:
                    try:
                        image = track.album.image(320)
                    except Exception:
                        pass

            track_url = f'https://tidal.com/browse/track/{track.id}' if track.id else None

            # Resolve direct stream URL for players without ShareLink (e.g. BlueSound).
            # These are time-limited tokens; nightly + startup refresh keeps them fresh.
            stream_url = None
            try:
                stream_url = track.get_url()
            except Exception:
                pass

            tracks.append({
                'name': name,
                'artist': artist,
                'id': str(track.id) if track.id else '',
                'url': track_url,
                'stream_url': stream_url,
                'image': image,
            })
    except Exception as e:
        log(f"  Error fetching tracks: {e}")

    return tracks




def main():
    force = '--force' in sys.argv

    output_file = DEFAULT_OUTPUT_FILE
    if '--output' in sys.argv:
        idx = sys.argv.index('--output')
        if idx + 1 < len(sys.argv):
            output_file = sys.argv[idx + 1]

    token_file = None
    if '--token-file' in sys.argv:
        idx = sys.argv.index('--token-file')
        if idx + 1 < len(sys.argv):
            token_file = sys.argv[idx + 1]

    if not token_file:
        log("ERROR: --token-file is required")
        return 1

    # Load tokens
    try:
        with open(token_file) as f:
            tokens = json.load(f)
    except Exception as e:
        log(f"ERROR: Could not load token file: {e}")
        return 1

    if not tokens.get('access_token'):
        log("ERROR: No access_token in token file")
        return 1

    log("=== TIDAL Playlist Fetch Starting ===")
    if force:
        log("Force mode: fetching all tracks regardless of cache")

    # Create session
    try:
        import tidalapi
        session = tidalapi.Session()
        # Convert stored float timestamp back to datetime for tidalapi
        expiry_raw = tokens.get('expiry_time')
        expiry_dt = (datetime.fromtimestamp(expiry_raw, tz=timezone.utc)
                     if expiry_raw else None)
        session.load_oauth_session(
            token_type=tokens.get('token_type', 'Bearer'),
            access_token=tokens['access_token'],
            refresh_token=tokens.get('refresh_token'),
            expiry_time=expiry_dt,
        )
        if not session.check_login():
            log("ERROR: TIDAL session invalid — re-authentication required")
            return 1
    except Exception as e:
        log(f"ERROR: Could not create TIDAL session: {e}")
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

    # Fetch all playlists
    log("Fetching playlists for authenticated user")
    raw_playlists = fetch_playlists(session)
    log(f"Found {len(raw_playlists)} playlists")

    # Convert to our format
    all_playlists = []
    for pl in raw_playlists:
        image = None
        if hasattr(pl, 'image') and pl.image:
            try:
                image = pl.image(640)
            except Exception:
                try:
                    image = pl.image(320)
                except Exception:
                    pass

        last_modified = ''
        if hasattr(pl, 'last_updated') and pl.last_updated:
            try:
                last_modified = pl.last_updated.isoformat()
            except Exception:
                last_modified = str(pl.last_updated)

        all_playlists.append({
            'id': str(pl.id),
            'name': pl.name or 'Untitled',
            'url': f'https://tidal.com/browse/playlist/{pl.id}',
            'image': image,
            'lastModifiedDate': last_modified,
            '_raw': pl,  # keep for track fetching
        })

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

    # Fetch tracks
    fetched = 0
    if to_fetch:
        log(f"Fetching tracks for {len(to_fetch)} playlists...")
        for pl in to_fetch:
            try:
                raw = pl.pop('_raw', None)
                if raw:
                    tracks = fetch_playlist_tracks(raw)
                    pl['tracks'] = tracks
                    log(f"  {pl['name']}: {len(tracks)} tracks")
                else:
                    pl['tracks'] = []
                playlists_with_tracks.append(pl)
                fetched += 1
            except Exception as e:
                log(f"  {pl['name']}: ERROR {e}")
                pl['tracks'] = []
                playlists_with_tracks.append(pl)
                fetched += 1

    # Clean up _raw references from cached entries
    for pl in playlists_with_tracks:
        pl.pop('_raw', None)

    log(f"Fetched {fetched}, skipped {skipped} unchanged")

    # Filter out empty playlists
    before = len(playlists_with_tracks)
    playlists_with_tracks = [p for p in playlists_with_tracks if p.get('tracks')]
    if before != len(playlists_with_tracks):
        log(f"Filtered out {before - len(playlists_with_tracks)} empty playlists")

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
