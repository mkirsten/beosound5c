#!/usr/bin/env python3
"""
Fetch all Spotify playlists for the authenticated user.
Auto-detects digit playlists by name pattern (e.g., "5: Dinner" -> digit 5).
Run via cron or beo-source-spotify service to keep playlists updated.

Token source: auth.get_access_token() (PKCE token store or env vars).
Can also receive --access-token from the beo-source-spotify service.
"""

import hashlib
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
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'services'))

from spotify_auth import get_access_token, missing_scopes
from spotify_tokens import load_tokens
from lib.digit_playlists import detect_digit_playlist, build_digit_mapping

# Scopes the app currently asks for.  Kept in sync with SPOTIFY_SCOPES in
# service.py — duplicating the literal here keeps fetch.py runnable
# standalone (cron, ad-hoc invocations) without importing the full service.
EXPECTED_SCOPES = ('playlist-read-private playlist-read-collaborative '
                   'user-library-read '
                   'user-read-playback-state user-modify-playback-state '
                   'user-read-currently-playing streaming')

# Maximum 429-retry attempts per HTTP request before we give up and
# surface the error to the caller.  Spotify's Retry-After is honoured
# on each round; total wait is bounded by the sum of those values.
MAX_429_RETRIES = 3


class SpotifyAPIError(Exception):
    """Surfaced for non-recoverable HTTP errors on Spotify endpoints.

    Distinguishes "API said no" (e.g. 401, 403, 404, 5xx) from
    "endpoint returned 0 rows".  The caller decides whether to fall
    back to cached data or write the smaller result.
    """

    def __init__(self, status, body, url):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status} from {url}: {body[:200]}")


def _spotify_get(token, url, *, timeout=10):
    """GET ``url`` with bearer auth, retrying 429s up to MAX_429_RETRIES.

    Returns the parsed JSON body on success.  Raises SpotifyAPIError on
    non-recoverable HTTP errors (after retries are exhausted for 429s).
    Raises the original urllib exception on network/timeout errors.
    """
    headers = {'Authorization': f'Bearer {token}'}
    retries_left = MAX_429_RETRIES
    while True:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and retries_left > 0:
                retry_after = int(e.headers.get('Retry-After', 2))
                log(f"  429 from {url} — sleeping {retry_after}s "
                    f"({retries_left} retries left)")
                time.sleep(retry_after)
                retries_left -= 1
                continue
            try:
                body = e.read().decode('utf-8', 'replace')
            except Exception:
                body = ''
            raise SpotifyAPIError(e.code, body, url)

DIGIT_PLAYLISTS_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'digit_playlists.json')
DEFAULT_OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'web', 'json', 'spotify_playlists.json')


def log(msg):
    """Log with timestamp to stdout (captured by systemd journal or parent process)."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")


def fetch_playlist_tracks(token, playlist_id):
    """Fetch all tracks for a playlist.  Returns ``(tracks, error)``.

    ``error`` is ``None`` on success (including the legitimate empty-
    playlist case) and a short error string on API/network failure —
    the caller uses this to decide whether to fall back to cached
    tracks instead of overwriting them with an empty list.
    """
    tracks = []
    # Spotify's Feb/Mar 2026 Web API migration replaced /playlists/{id}/tracks
    # with /playlists/{id}/items — the old endpoint returns 403 for apps in
    # Development Mode (which is what third-party installs run as).  Each page
    # entry carries the track under 'item'; during the transition Spotify also
    # includes the legacy 'track' key, so read whichever is present.
    url = f'https://api.spotify.com/v1/playlists/{playlist_id}/items?limit=100'
    while url:
        try:
            data = _spotify_get(token, url)
        except SpotifyAPIError as e:
            log(f"  Error fetching tracks for {playlist_id}: HTTP {e.status} "
                f"body={e.body[:120]!r}")
            return tracks, f"http_{e.status}"
        except Exception as e:
            log(f"  Error fetching tracks for {playlist_id}: {e}")
            return tracks, "network"

        raw_items = data.get('items', [])
        skipped_local = 0
        skipped_no_url = 0
        for item in raw_items:
            track = item.get('item') or item.get('track')
            if not track:
                continue
            if track.get('is_local'):
                skipped_local += 1
                continue
            ext_url = track.get('external_urls', {}).get('spotify')
            if not ext_url:
                skipped_no_url += 1
                continue
            tracks.append({
                'name': track['name'],
                'artist': ', '.join([a['name'] for a in track.get('artists', []) if a.get('name')]),
                'album': track.get('album', {}).get('name', ''),
                'id': track['id'],
                'uri': track.get('uri', ''),
                'url': ext_url,
                'image': track['album']['images'][0]['url'] if track.get('album', {}).get('images') else None
            })

        if skipped_local or skipped_no_url:
            log(f"  Skipped {skipped_local} local files, {skipped_no_url} without URL "
                f"(page had {len(raw_items)} items, kept {len(tracks)} tracks)")

        url = data.get('next')

    return tracks, None


def fetch_me(token):
    """Fetch the authenticated user's profile.  Returns dict or None on
    error.  Used purely for diagnostics — confirms the token works at all
    and surfaces the user_id so support can verify the right account."""
    try:
        return _spotify_get(token, 'https://api.spotify.com/v1/me')
    except SpotifyAPIError as e:
        log(f"  /me failed: HTTP {e.status} body={e.body[:200]!r}")
        return None
    except Exception as e:
        log(f"  /me failed: {e}")
        return None


def fetch_liked_songs(token):
    """Fetch all liked (saved) tracks and return a synthetic playlist dict.

    Returns None on API error or when the user has no liked tracks.  The
    two cases are distinguishable from the log; from the caller's point
    of view they're treated the same — we just don't surface a Liked
    Songs entry."""
    tracks = []
    url = 'https://api.spotify.com/v1/me/tracks?limit=50'

    while url:
        try:
            data = _spotify_get(token, url)
        except SpotifyAPIError as e:
            log(f"  Error fetching liked songs: HTTP {e.status} "
                f"body={e.body[:200]!r}")
            break
        except Exception as e:
            log(f"  Error fetching liked songs: {e}")
            break

        for item in data.get('items', []):
            track = item.get('track')
            if not track:
                continue
            if track.get('is_local'):
                continue
            ext_url = track.get('external_urls', {}).get('spotify')
            if not ext_url:
                continue
            tracks.append({
                'name': track['name'],
                'artist': ', '.join([a['name'] for a in track.get('artists', []) if a.get('name')]),
                'album': track.get('album', {}).get('name', ''),
                'id': track['id'],
                'uri': track.get('uri', ''),
                'url': ext_url,
                'image': track['album']['images'][0]['url'] if track.get('album', {}).get('images') else None
            })

        url = data.get('next')

    if not tracks:
        return None

    # Build a change-detection key from track count + first/last track IDs
    first_id = tracks[0]['id'] if tracks else ''
    last_id = tracks[-1]['id'] if tracks else ''
    hash_input = f"{len(tracks)}:{first_id}:{last_id}"
    snapshot_id = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    # Use first track's album image as playlist image
    image = tracks[0].get('image') if tracks else None

    log(f"  Liked Songs: {len(tracks)} tracks")
    return {
        'id': 'liked-songs',
        'name': 'Liked Songs',
        'uri': 'spotify:collection:tracks',
        'url': 'https://open.spotify.com/collection/tracks',
        'image': image,
        'owner': '',
        'public': False,
        'snapshot_id': snapshot_id,
        'tracks': tracks
    }


def fetch_user_playlists(token):
    """Fetch all playlists for the authenticated user.

    Returns ``(playlists, error)``.  ``error`` is None on success
    (including legitimately empty accounts) and a short error string on
    API failure — the caller uses this to decide whether the empty list
    is real or a symptom (auth/scope/network) and refuses to overwrite
    a known-good cache with a known-bad result.
    """
    playlists = []
    url = 'https://api.spotify.com/v1/me/playlists?limit=50'
    while url:
        try:
            data = _spotify_get(token, url)
        except SpotifyAPIError as e:
            log(f"Error fetching playlists: HTTP {e.status} "
                f"body={e.body[:200]!r}")
            return playlists, f"http_{e.status}"
        except Exception as e:
            log(f"Error fetching playlists: {e}")
            return playlists, "network"

        for pl in data.get('items', []):
            if not pl:
                continue
            api_track_count = pl.get('tracks', {}).get('total', '?')
            log(f"  {pl['name']} (owner: {pl.get('owner', {}).get('id', '?')}, "
                f"tracks: {api_track_count})")
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

    return playlists, None




# Fields every track dict must have. Any cached playlist whose first
# track is missing one of these is dropped from the incremental-sync
# cache so the playlist is re-fetched with the current schema —
# snapshot-id matching alone would otherwise preserve the old shape
# indefinitely.
REQUIRED_TRACK_FIELDS = ('album',)


def _load_cache(output_file):
    """Load the playlist cache from ``output_file`` for incremental sync.

    Returns ``(cache, stale_schema_count)`` where ``cache`` is a dict
    keyed by playlist id with ``{snapshot_id, tracks}`` values, and
    ``stale_schema_count`` is the number of cache entries dropped
    because their tracks were missing fields in REQUIRED_TRACK_FIELDS.
    """
    with open(output_file, 'r') as f:
        cached_playlists = json.load(f)
    cache = {}
    stale_schema = 0
    for cp in cached_playlists:
        tracks = cp.get('tracks', [])
        if tracks and any(f not in tracks[0] for f in REQUIRED_TRACK_FIELDS):
            stale_schema += 1
            continue
        cache[cp['id']] = {
            'snapshot_id': cp.get('snapshot_id', ''),
            'tracks': tracks,
        }
    return cache, stale_schema


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

    # Scope check.  Spotify returns granted scopes on token refresh and
    # we persist them in the token store; if a required scope is missing
    # we surface it loudly because the user *must* re-auth to fix it.
    stored = load_tokens() or {}
    granted_scope = stored.get('scope')
    if granted_scope:
        log(f"Granted scopes: {granted_scope}")
        missing = missing_scopes(granted_scope, EXPECTED_SCOPES)
        if missing:
            log(f"WARNING: token is missing scopes {missing} — these "
                f"features will not work until the user re-auths via "
                f"the /setup page")
    else:
        log("Granted scopes unknown (token predates scope tracking — "
            "next refresh will record them)")

    # /me sanity check — confirms the token works and gives us a user_id
    # for support diagnostics.  Not a blocker if it fails; subsequent
    # endpoint calls will produce their own error logs.
    me = fetch_me(token)
    if me:
        log(f"Authenticated as user_id={me.get('id')!r} "
            f"display_name={me.get('display_name')!r} "
            f"product={me.get('product')!r}")
        my_user_id = me.get('id', '')
    else:
        log("WARNING: /me failed — token may be invalid")
        my_user_id = ''

    # Load cached data for incremental sync. See _load_cache below.
    cache = {}
    if not force and os.path.exists(output_file):
        try:
            cache, stale_schema = _load_cache(output_file)
            log(f"Loaded cache with {len(cache)} playlists"
                + (f" ({stale_schema} dropped for stale schema)"
                   if stale_schema else ""))
        except Exception as e:
            log(f"Could not load cache: {e}")

    # Fetch liked songs.
    log("Fetching liked songs")
    liked_cached = cache.get('liked-songs')
    liked_playlist = fetch_liked_songs(token)
    liked_changed = True
    if liked_playlist and liked_cached:
        if liked_cached.get('snapshot_id') == liked_playlist.get('snapshot_id'):
            liked_playlist['tracks'] = liked_cached['tracks']
            liked_changed = False
            log("  Liked Songs (unchanged)")

    # Fetch all user's playlists.  Distinguish "API said no" from "user
    # has no playlists" — a list-level error means we shouldn't trust the
    # absence of playlists, and we definitely shouldn't overwrite the
    # cache with an empty result.
    log("Fetching playlists for authenticated user")
    all_playlists, list_error = fetch_user_playlists(token)
    owned = sum(1 for p in all_playlists
                if my_user_id and p.get('owner') == my_user_id)
    followed = len(all_playlists) - owned
    log(f"Found {len(all_playlists)} playlists from API "
        f"(owned-by-user={owned}, followed/other={followed})")
    if list_error:
        log(f"WARNING: playlist-list fetch errored ({list_error}) — "
            f"will preserve cache rather than overwrite with partial result")
        if cache:
            log(f"Aborting write to preserve {len(cache)} cached playlists")
            return 0

    # Split into cached (unchanged) and needs-fetch.
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

    # Fetch tracks in parallel.  2 workers (down from 4) — Spotify's per-
    # app rate budget is small enough that 4 concurrent fetchers tend to
    # synchronize on 429s and all back off at once; 2 stays well under
    # the threshold.  Per-playlist errors are *not* fatal; we keep the
    # cached tracks (or empty list) and let the cache-fallback below
    # decide whether to drop the playlist.
    fetched_ok = 0
    fetched_failed = 0
    kept_from_cache_on_error = 0
    third_party_403 = 0
    if to_fetch:
        log(f"Fetching tracks for {len(to_fetch)} playlists in parallel...")
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_to_pl = {
                pool.submit(fetch_playlist_tracks, token, pl['id']): pl
                for pl in to_fetch
            }
            for future in as_completed(future_to_pl):
                pl = future_to_pl[future]
                try:
                    tracks, error = future.result()
                except Exception as e:
                    tracks, error = [], f"unexpected: {e}"

                if error:
                    if (error == "http_403" and my_user_id
                            and pl.get('owner')
                            and pl['owner'] != my_user_id):
                        third_party_403 += 1
                    # Don't clobber cached tracks with an empty list when
                    # the fetch errored — the playlist is real, we just
                    # couldn't reach it this round.  Snapshot stays the
                    # *previous* one so a successful refresh later picks
                    # up where we left off.
                    cached = cache.get(pl['id'])
                    if cached and cached.get('tracks'):
                        pl['tracks'] = cached['tracks']
                        pl['snapshot_id'] = cached.get('snapshot_id', '')
                        playlists_with_tracks.append(pl)
                        log(f"  {pl['name']}: error ({error}) — kept "
                            f"{len(pl['tracks'])} tracks from cache")
                        kept_from_cache_on_error += 1
                    else:
                        log(f"  {pl['name']}: error ({error}) — no cache, "
                            f"playlist will be dropped this round")
                    fetched_failed += 1
                else:
                    pl['tracks'] = tracks
                    playlists_with_tracks.append(pl)
                    log(f"  {pl['name']}: {len(tracks)} tracks")
                    fetched_ok += 1

    # Drop legitimately-empty playlists (Spotify returned 0 tracks for a
    # successful fetch).  Errored fetches that fell back to cached tracks
    # stay; errored fetches with no cache were already excluded above.
    before = len(playlists_with_tracks)
    playlists_with_tracks = [p for p in playlists_with_tracks if p.get('tracks')]
    dropped_empty = before - len(playlists_with_tracks)
    if dropped_empty:
        log(f"Filtered out {dropped_empty} empty playlists")

    # Sort by name.
    playlists_with_tracks.sort(key=lambda p: p['name'].lower())

    # Insert Liked Songs as the first playlist.
    if liked_playlist and liked_playlist.get('tracks'):
        playlists_with_tracks.insert(0, liked_playlist)

    final_count = len(playlists_with_tracks)
    cached_count = len(cache)

    # Refuse to overwrite a healthy cache with a result that's drastically
    # smaller — a sudden drop almost always means an upstream auth/scope/
    # rate-limit issue rather than the user actually deleting half their
    # library.  The user can always force the write with --force.
    if (not force and cached_count >= 4
            and final_count < cached_count // 2):
        log(f"WARNING: result has {final_count} playlists but cache had "
            f"{cached_count} — refusing to overwrite (likely auth, scope, "
            f"or rate-limit issue).  Re-run with --force to override.")
        log(f"=== Summary: liked={1 if liked_playlist else 0}, "
            f"playlists_from_api={len(all_playlists)}, "
            f"fetched_ok={fetched_ok}, fetched_failed={fetched_failed}, "
            f"kept_from_cache={skipped + kept_from_cache_on_error}, "
            f"dropped_empty={dropped_empty}, written=0 (refused) ===")
        return 0

    # Skip write if nothing changed.  fetched_ok counts successful
    # network fetches; if none ran, none of `skipped` changed, and the
    # liked-songs snapshot matched, the on-disk file is already correct.
    if (fetched_ok == 0 and not liked_changed
            and final_count == cached_count):
        log("No changes — skipping disk write")
        log(f"=== Summary: liked={1 if liked_playlist else 0}, "
            f"playlists_from_api={len(all_playlists)}, "
            f"fetched_ok=0, fetched_failed={fetched_failed}, "
            f"kept_from_cache={skipped + kept_from_cache_on_error}, "
            f"dropped_empty={dropped_empty}, written=0 (no-op) ===")
        return 0

    # Save all playlists.
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    # Atomic write — a concurrent fetch (or a reader mid-write) must never
    # see a truncated file; corrupt JSON means zero playlists until the
    # next clean refresh.
    _tmp = output_file + '.tmp'
    with open(_tmp, 'w') as f:
        json.dump(playlists_with_tracks, f, indent=2)
    os.replace(_tmp, output_file)
    log(f"Saved {final_count} playlists to {output_file}")

    # Build digit mapping: pinned names first, then fill alphabetically.
    digit_mapping = build_digit_mapping(playlists_with_tracks)
    with open(DIGIT_PLAYLISTS_FILE, 'w') as f:
        json.dump(digit_mapping, f, indent=2)
    pinned = sum(1 for d in "0123456789"
                 if d in digit_mapping and detect_digit_playlist(digit_mapping[d]['name']) is not None)
    log(f"Saved digit playlists ({pinned} pinned, {len(digit_mapping) - pinned} auto-filled)")

    # Single-line summary so support / log greps don't have to reconstruct
    # the run from a dozen scattered lines.
    log(f"=== Summary: liked={1 if liked_playlist else 0}, "
        f"playlists_from_api={len(all_playlists)}, "
        f"fetched_ok={fetched_ok}, fetched_failed={fetched_failed}, "
        f"kept_from_cache={skipped + kept_from_cache_on_error}, "
        f"dropped_empty={dropped_empty}, written={final_count} ===")
    if third_party_403:
        log(f"NOTE: {third_party_403} playlist(s) owned by other users "
            "returned 403. Spotify's dev-mode rules (Mar 2026) only allow "
            "reading tracks from playlists you own or collaborate on. "
            "Fix: duplicate them into your own account, or apply for "
            "Extended Quota Mode at developer.spotify.com/dashboard.")
    if len(all_playlists) <= 1:
        log(f"WARNING: only {len(all_playlists)} playlist(s) returned by "
            "Spotify. If you expected more, the most likely cause is a "
            "stale OAuth grant missing 'playlist-read-private' / "
            "'playlist-read-collaborative'. Fix: revoke at "
            "https://www.spotify.com/account/apps, then re-auth via "
            "the BeoSound 5c /setup page.")
    log("=== Done ===")
    return 0

if __name__ == '__main__':
    exit(main())
