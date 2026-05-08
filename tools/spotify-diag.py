#!/usr/bin/env python3
"""Spotify diagnostic — refreshes the stored token, then probes /me,
/me/playlists, and /v1/playlists/<id>/tracks for the first 3 playlists,
printing HTTP status + response body for each.

Run on the device:  sudo python3 ~/beosound5c/tools/spotify-diag.py
(needs read access to /etc/beosound5c/spotify_tokens.json)
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

CANDIDATES = [
    '/etc/beosound5c/spotify_tokens.json',
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 '..', 'services', 'sources', 'spotify', 'spotify_tokens.json'),
]
token_path = next((p for p in CANDIDATES if os.path.exists(p)), None)
if not token_path:
    print("ERROR: no spotify_tokens.json found.  Looked in:")
    for p in CANDIDATES:
        print(f"  {p}")
    print("\nThis script must run ON the device (church/kitchen/office/etc.),")
    print("where Spotify has been authorised.  ssh in first, then run:")
    print("  sudo python3 ~/beosound5c/tools/spotify-diag.py")
    sys.exit(1)
print(f"Using token file: {token_path}")

with open(token_path) as f:
    t = json.load(f)
print(f"Stored scope: {t.get('scope')!r}")

# Refresh access token
req = urllib.request.Request(
    'https://accounts.spotify.com/api/token',
    data=urllib.parse.urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': t['refresh_token'],
        'client_id': t['client_id'],
    }).encode(),
    headers={'Content-Type': 'application/x-www-form-urlencoded'})
try:
    tok = json.loads(urllib.request.urlopen(req).read())
except urllib.error.HTTPError as e:
    print(f"REFRESH FAILED: HTTP {e.code} body={e.read().decode()[:400]}")
    raise SystemExit(1)
print(f"Fresh scope:  {tok.get('scope')!r}")
access = tok['access_token']


def call(url):
    try:
        return 200, urllib.request.urlopen(
            urllib.request.Request(url, headers={'Authorization': f'Bearer {access}'})
        ).read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# Who am I?
code, body = call('https://api.spotify.com/v1/me')
me = json.loads(body) if code == 200 else {}
print(f"\n/me  HTTP {code}  user_id={me.get('id')!r}  "
      f"product={me.get('product')!r}  country={me.get('country')!r}")

# List playlists
code, body = call('https://api.spotify.com/v1/me/playlists?limit=3')
print(f"\n/me/playlists  HTTP {code}")
if code != 200:
    print(f"  body={body[:400]}")
    raise SystemExit(1)
pls = json.loads(body).get('items', [])

# Try fetching tracks for each
for p in pls:
    print(f"\n--- {p['name']!r}  id={p['id']}  owner={p['owner']['id']}  "
          f"collab={p.get('collaborative')}  public={p.get('public')} ---")
    code, body = call(f"https://api.spotify.com/v1/playlists/{p['id']}/tracks?limit=1")
    if code == 200:
        n = len(json.loads(body).get('items', []))
        print(f"  tracks OK ({n} item)")
    else:
        print(f"  tracks FAIL: HTTP {code}")
        print(f"  body: {body.decode(errors='replace')[:500]}")
