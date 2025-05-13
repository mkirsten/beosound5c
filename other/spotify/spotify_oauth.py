import os, time, requests
from flask     import Flask, redirect, request, session, jsonify

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ─── YOUR SPOTIFY APP CREDS ─────────────────────────────────────────────────────
CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID",    "6420bddd82d046adb24b3009960c5d81")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET","0cbb9390b1c045878ec3a57d8bb32b76")
REDIRECT_URI  = "http://127.0.0.1:8888/callback"
SCOPE         = "playlist-read-private user-read-playback-state user-modify-playback-state"
# ────────────────────────────────────────────────────────────────────────────────

AUTH_URL   = "https://accounts.spotify.com/authorize"
TOKEN_URL  = "https://accounts.spotify.com/api/token"

# 1) Kick off auth flow
@app.route("/login")
def login():
    params = {
        "response_type": "code",
        "client_id":     CLIENT_ID,
        "scope":         SCOPE,
        "redirect_uri":  REDIRECT_URI,
        "show_dialog":   "true"
    }
    url = requests.Request("GET", AUTH_URL, params=params).prepare().url
    return redirect(url)

# 2) Spotify will redirect here with ?code=...
@app.route("/callback")
def callback():
      # 1) Did Spotify return an “error=…”?
    err = request.args.get("error")
    if err:
        desc = request.args.get("error_description","")
        return (
            f"<h2>⚠️ Spotify authorization failed</h2>"
            f"<p><strong>{err}</strong>: {desc}</p>"
            f"<p>Check your Redirect URI in the Spotify Dashboard.</p>"
        ), 400

    # 2) Otherwise grab the code as before
    code = request.args.get("code")
    if not code:
        return "No code parameter in callback.", 400
    resp = requests.post(TOKEN_URL, data={
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }).json()

    # stash into session
    access_token  = resp["access_token"]
    refresh_token = resp["refresh_token"]
    expires_in    = resp["expires_in"]

    session["access_token"]  = access_token
    session["refresh_token"] = refresh_token
    session["expires_at"]    = time.time() + expires_in - 60

    # 🔥 Log the refresh token so you can copy it out and KEEP it forever 🔥
    print("\n--- COPY THIS REFRESH TOKEN AND STORE IT SOMEWHERE SAFE ---")
    print("REFRESH_TOKEN =", refresh_token)
    print("-----------------------------------------------------------\n")

    return "<h2>✅ Authorization complete!</h2><p>You can now close this window.</p>"

# 3) Always hand out a valid (unexpired) access token
@app.route("/token")
def token():
    # attempt to load refresh_token from session, else fall back to env
    refresh_token = session.get("refresh_token") or os.getenv("SPOTIFY_REFRESH_TOKEN")
    if not refresh_token:
        return "No refresh token. Please /login first.", 400

    expires_at = session.get("expires_at", 0)
    if time.time() > expires_at:
        # need a fresh access token
        resp = requests.post(TOKEN_URL, data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }).json()
        session["access_token"] = resp["access_token"]
        session["expires_at"]   = time.time() + resp["expires_in"] - 60
        print(f"[{time.strftime('%T')}] 🔄 Refreshed access_token")

    return jsonify(access_token=session["access_token"])

if __name__=="__main__":
    app.run(host="0.0.0.0", port=8888, debug=True)