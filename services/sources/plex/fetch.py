#!/usr/bin/env python3
"""
Fetch all Plex playlists and recent albums for the authenticated user.
Auto-detects digit playlists by name pattern (e.g., "5: Dinner" -> digit 5).
Run via beo-source-plex service to keep playlists updated.

Token source: --token-file <path> pointing to plex_tokens.json.
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

DIGIT_PLAYLISTS_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'plex_digit_playlists.json')
DEFAULT_OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'plex_playlists.json')


def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")


def fetch_playlists(server):
    """Fetch audio playlists from the Plex server."""
    playlists = []
    try:
        all_playlists = server.playlists()
        for pl in all_playlists:
            if pl.playlistType == 'audio':
                playlists.append(pl)
    except Exception as e:
        log(f"Error fetching playlists: {e}")
    return playlists


def fetch_recent_albums(server, max_results=50):
    """Fetch recently added albums as pseudo-playlists."""
    albums = []
    try:
        for section in server.library.sections():
            if section.type == 'artist':
                recent = section.searchAlbums(sort='addedAt:desc', maxresults=max_results)
                albums.extend(recent)
                break  # use first music library
    except Exception as e:
        log(f"Error fetching recent albums: {e}")
    return albums


def fetch_playlist_tracks(playlist, server):
    """Fetch all tracks for a playlist."""
    tracks = []
    try:
        items = playlist.items()
        for track in items:
            name = track.title or 'Unknown'
            artist = track.grandparentTitle or 'Unknown'

            image = None
            if track.thumbUrl:
                image = track.thumbUrl

            try:
                stream_url = track.getStreamURL()
            except Exception:
                stream_url = None

            tracks.append({
                'name': name,
                'artist': artist,
                'id': str(track.ratingKey),
                'url': stream_url,
                'image': image,
            })
    except Exception as e:
        log(f"  Error fetching tracks: {e}")

    return tracks


def fetch_album_tracks(album, server):
    """Fetch all tracks for an album."""
    tracks = []
    try:
        items = album.tracks()
        for track in items:
            name = track.title or 'Unknown'
            artist = track.grandparentTitle or album.parentTitle or 'Unknown'

            image = None
            if track.thumbUrl:
                image = track.thumbUrl
            elif album.thumbUrl:
                image = album.thumbUrl

            try:
                stream_url = track.getStreamURL()
            except Exception:
                stream_url = None

            tracks.append({
                'name': name,
                'artist': artist,
                'id': str(track.ratingKey),
                'url': stream_url,
                'image': image,
            })
    except Exception as e:
        log(f"  Error fetching album tracks: {e}")

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

    if not tokens.get('auth_token'):
        log("ERROR: No auth_token in token file")
        return 1

    log("=== Plex Playlist Fetch Starting ===")
    if force:
        log("Force mode: fetching all tracks regardless of cache")

    # Connect to Plex server
    try:
        from plexapi.server import PlexServer
        server = PlexServer(tokens['server_url'], tokens['auth_token'], timeout=30)
        log(f"Connected to Plex server: {server.friendlyName}")
    except Exception as e:
        log(f"ERROR: Could not connect to Plex server: {e}")
        return 1

    # Load cached data for incremental sync
    cache = {}
    if not force and os.path.exists(output_file):
        try:
            with open(output_file, 'r') as f:
                cached_playlists = json.load(f)
            for cp in cached_playlists:
                cache[cp['id']] = {
                    'updatedAt': cp.get('updatedAt', ''),
                    'tracks': cp.get('tracks', []),
                }
            log(f"Loaded cache with {len(cache)} playlists")
        except Exception as e:
            log(f"Could not load cache: {e}")

    # Fetch audio playlists
    log("Fetching playlists from Plex server")
    raw_playlists = fetch_playlists(server)
    log(f"Found {len(raw_playlists)} audio playlists")

    # Fetch recent albums as pseudo-playlists
    log("Fetching recent albums")
    raw_albums = fetch_recent_albums(server)
    log(f"Found {len(raw_albums)} recent albums")

    # Convert playlists to our format
    all_playlists = []
    for pl in raw_playlists:
        image = None
        if hasattr(pl, 'thumbUrl') and pl.thumbUrl:
            image = pl.thumbUrl

        updated_at = ''
        if hasattr(pl, 'updatedAt') and pl.updatedAt:
            try:
                updated_at = pl.updatedAt.isoformat()
            except Exception:
                updated_at = str(pl.updatedAt)

        all_playlists.append({
            'id': f'playlist:{pl.ratingKey}',
            'name': pl.title or 'Untitled',
            'image': image,
            'updatedAt': updated_at,
            '_raw': pl,
            '_type': 'playlist',
        })

    # Convert albums to our format
    for album in raw_albums:
        image = None
        if hasattr(album, 'thumbUrl') and album.thumbUrl:
            image = album.thumbUrl

        updated_at = ''
        if hasattr(album, 'addedAt') and album.addedAt:
            try:
                updated_at = album.addedAt.isoformat()
            except Exception:
                updated_at = str(album.addedAt)

        artist = album.parentTitle or 'Unknown Artist'
        all_playlists.append({
            'id': f'album:{album.ratingKey}',
            'name': f'{album.title}\n({artist})',
            'image': image,
            'updatedAt': updated_at,
            '_raw': album,
            '_type': 'album',
        })

    # Split into cached vs needs-fetch
    playlists_with_tracks = []
    to_fetch = []
    skipped = 0

    for pl in all_playlists:
        cached = cache.get(pl['id'])
        if (cached and cached['updatedAt']
                and cached['updatedAt'] == pl.get('updatedAt', '')):
            pl['tracks'] = cached['tracks']
            playlists_with_tracks.append(pl)
            log(f"  {pl['name'].split(chr(10))[0]} (unchanged)")
            skipped += 1
        else:
            to_fetch.append(pl)

    # Fetch tracks
    fetched = 0
    if to_fetch:
        log(f"Fetching tracks for {len(to_fetch)} playlists/albums...")
        for pl in to_fetch:
            try:
                raw = pl.pop('_raw', None)
                pl_type = pl.pop('_type', 'playlist')
                if raw:
                    if pl_type == 'album':
                        tracks = fetch_album_tracks(raw, server)
                    else:
                        tracks = fetch_playlist_tracks(raw, server)
                    pl['tracks'] = tracks
                    log(f"  {pl['name'].split(chr(10))[0]}: {len(tracks)} tracks")
                else:
                    pl['tracks'] = []
                playlists_with_tracks.append(pl)
                fetched += 1
            except Exception as e:
                log(f"  {pl['name'].split(chr(10))[0]}: ERROR {e}")
                pl['tracks'] = []
                playlists_with_tracks.append(pl)
                fetched += 1

    # Clean up internal references
    for pl in playlists_with_tracks:
        pl.pop('_raw', None)
        pl.pop('_type', None)

    log(f"Fetched {fetched}, skipped {skipped} unchanged")

    # Filter out empty playlists
    before = len(playlists_with_tracks)
    playlists_with_tracks = [p for p in playlists_with_tracks if p.get('tracks')]
    if before != len(playlists_with_tracks):
        log(f"Filtered out {before - len(playlists_with_tracks)} empty playlists")

    # Playlists first (sorted by name), then albums (sorted by name)
    playlists_only = sorted(
        [p for p in playlists_with_tracks if p['id'].startswith('playlist:')],
        key=lambda p: p['name'].lower())
    albums_only = sorted(
        [p for p in playlists_with_tracks if p['id'].startswith('album:')],
        key=lambda p: p['name'].lower())
    playlists_with_tracks = playlists_only + albums_only

    # Skip write if nothing changed
    if fetched == 0 and len(playlists_with_tracks) == len(cache):
        log("No changes - skipping disk write")
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
