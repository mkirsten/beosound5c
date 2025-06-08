import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# CONFIG: Replace with your app's values
CLIENT_ID = '6420bddd82d046adb24b3009960c5d81'
CLIENT_SECRET = '0cbb9390b1c045878ec3a57d8bb32b76'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'

# Authenticate (only once — will cache credentials)
scope = 'playlist-read-private playlist-read-collaborative'
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=scope,
    cache_path='.spotify_token_cache'  # default is .cache
))

# Fetch playlists
playlists = []
results = sp.current_user_playlists()

while results:
    for pl in results['items']:
        playlists.append({
            'name': pl['name'],
            'id': pl['id'],
            'url': pl['external_urls']['spotify'],
            'image': pl['images'][0]['url'] if pl['images'] else None
        })
    results = sp.next(results)

# Save to file
with open('playlists.json', 'w') as f:
    json.dump(playlists, f, indent=2)

print("✅ playlists.json written.")