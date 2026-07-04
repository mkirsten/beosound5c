#!/usr/bin/env python3
"""Spotify diagnostic — refreshes the stored token, then probes /me,
/me/playlists, and /v1/playlists/<id>/items for the first 3 playlists,
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

# Prefer the live token from the running service — PKCE refresh tokens
# rotate on each use, so a standalone refresh here races the service and
# can hit "invalid_grant: Refresh token revoked" on the now-stale disk copy.
access = None
try:
    with urllib.request.urlopen('http://localhost:8771/token', timeout=5) as r:
        access = json.loads(r.read())['access_token']
    print("Using live access token from running beo-source-spotify (port 8771)")
except Exception as e:
    print(f"Service token unavailable ({e}) — falling back to standalone refresh")

if not access:
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
        print("(If the service is running, stop it first or it will keep "
              "rotating the refresh token out from under this script.)")
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
my_id = me.get('id')
print(f"\n/me  HTTP {code}  user_id={my_id!r}  "
      f"product={me.get('product')!r}  country={me.get('country')!r}")

# Page through ALL playlists so we can categorize them.
all_pls = []
url = 'https://api.spotify.com/v1/me/playlists?limit=50'
while url:
    code, body = call(url)
    if code != 200:
        print(f"\n/me/playlists  HTTP {code}\n  body={body[:400]}")
        raise SystemExit(1)
    page = json.loads(body)
    all_pls.extend(page.get('items', []))
    url = page.get('next')
print(f"\n/me/playlists  HTTP 200  ({len(all_pls)} playlists)")

# Categorize by access class. Spotify (post Mar 2026 dev-mode rules) only
# lets dev-mode apps read tracks for playlists the user owns or collaborates
# on; everything else is third-party (metadata-only).
owned = [p for p in all_pls if p['owner']['id'] == my_id]
collab = [p for p in all_pls if p['owner']['id'] != my_id and p.get('collaborative')]
third = [p for p in all_pls if p['owner']['id'] != my_id and not p.get('collaborative')]
print(f"  owned by you: {len(owned)}   collaborative: {len(collab)}   "
      f"third-party: {len(third)}")


def probe(p):
    """Test track access for one playlist via the new /items endpoint."""
    print(f"\n--- {p['name']!r}  id={p['id']}  owner={p['owner']['id']}  "
          f"collab={p.get('collaborative')}  public={p.get('public')} ---")
    code, body = call(f"https://api.spotify.com/v1/playlists/{p['id']}/items?limit=1")
    if code == 200:
        n = len(json.loads(body).get('items', []))
        print(f"  items OK ({n} item)")
        return True
    print(f"  items FAIL: HTTP {code}")
    print(f"  body: {body.decode(errors='replace')[:300]}")
    return False


# Probe up to 3 from each category so we can tell whether the failure is
# strictly third-party (expected dev-mode behavior) or also hits the user's
# OWN playlists (a different problem).
owned_tested = owned_fail = 0
third_tested = third_fail = 0
for p in owned[:3]:
    owned_tested += 1
    if not probe(p):
        owned_fail += 1
for p in collab[:3]:
    probe(p)
for p in third[:3]:
    third_tested += 1
    if not probe(p):
        third_fail += 1

# Verdict — based on what actually failed, not category counts.
print("\n=== VERDICT ===")
if owned_fail:
    print(f"WARNING: {owned_fail}/{owned_tested} of your OWN playlists failed — "
          "this is NOT just the third-party restriction.")
    print("A 403 on a self-owned playlist usually means a token/scope problem or "
          "a folder/ownership quirk. Re-authenticate via /setup and re-run.")
elif third_fail and not owned_fail:
    print(f"Your OWN playlists work; {third_fail}/{third_tested} tested third-party "
          "playlists are blocked.")
    print("This is Spotify's dev-mode restriction. Fixes:")
    print("  - Duplicate the third-party playlists into your own account, OR")
    print("  - Apply for Extended Quota Mode at developer.spotify.com/dashboard")
elif owned_tested and not owned_fail and not third_fail:
    print("All tested playlists (owned and third-party) work — this app has full "
          "access (Extended Quota Mode). No playlist restriction in effect.")
elif not owned_tested:
    print("You have no self-owned playlists in this account to test against.")
