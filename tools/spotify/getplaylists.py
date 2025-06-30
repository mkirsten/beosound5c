import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# CONFIG: Replace with your app's values
CLIENT_ID = '6420bddd82d046adb24b3009960c5d81'
CLIENT_SECRET = '0cbb9390b1c045878ec3a57d8bb32b76'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'

# Authenticate (only once — will cache credentials)
SCOPE = 'playlist-read-private playlist-read-collaborative'
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
    cache_path='.spotify_token_cache'
))

def get_playlist_tracks(playlist_id):
    tracks = []
    results = sp.playlist_tracks(playlist_id)
    while results:
        for item in results['items']:
            track = item.get('track')
            if not track:
                continue
            url = track.get('external_urls', {}).get('spotify')
            if not url:
                continue  # skip broken/removed tracks
            tracks.append({
                'name': track['name'],
                'artist': ', '.join([a['name'] for a in track.get('artists', []) if a and a.get('name')]),
                'id': track['id'],
                'url': url,
                'image': track['album']['images'][0]['url'] if track['album']['images'] else None
            })
        results = sp.next(results)
    return tracks

# Fetch playlists and tracks
playlists = []
results = sp.current_user_playlists()

while results:
    for pl in results['items']:
        print(f"🎧 Fetching: {pl['name']}")
        playlists.append({
            'name': pl['name'],
            'id': pl['id'],
            'url': pl['external_urls']['spotify'],
            'image': pl['images'][0]['url'] if pl['images'] else None,
            'tracks': get_playlist_tracks(pl['id'])
        })
    results = sp.next(results)

# Write JSON
with open('../../web/json/playlists_with_tracks.json', 'w') as f:
    json.dump(playlists, f, indent=2)

print("✅ web/json/playlists_with_tracks.json written.")