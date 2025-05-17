from flask      import Flask, request, redirect, send_from_directory, jsonify
import requests, secrets, hashlib, base64, threading, webbrowser

# ── CONFIG ──────────────────────────────────────────────────────────
CLIENT_ID    = '6420bddd82d046adb24b3009960c5d81'
PORT         = 8888
REDIRECT_URI = f'http://127.0.0.1:{PORT}/callback'
SCOPE        = 'playlist-read-private playlist-read-collaborative'

# In-memory storage (demo only)
code_verifier = None
access_token  = None

# ── APP SETUP ──────────────────────────────────────────────────────
app = Flask(__name__, static_folder='public', static_url_path='')

#  Serve your static front-end
@app.route('/')
def home():
    return send_from_directory('public', 'index.html')

# ── 1) LOGIN (PKCE) ────────────────────────────────────────────────
@app.route('/login')
def login():
    global code_verifier
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')

    params = {
      'client_id':             CLIENT_ID,
      'response_type':         'code',
      'redirect_uri':          REDIRECT_URI,
      'code_challenge_method': 'S256',
      'code_challenge':        code_challenge,
      'scope':                 SCOPE
    }
    url = 'https://accounts.spotify.com/authorize?' + requests.compat.urlencode(params)
    return redirect(url)

# ── 2) CALLBACK ────────────────────────────────────────────────────
@app.route('/callback')
def callback():
    global code_verifier, access_token
    code = request.args.get('code')
    if not code or not code_verifier:
        return "Missing code.", 400

    data = {
      'client_id':     CLIENT_ID,
      'grant_type':    'authorization_code',
      'code':          code,
      'redirect_uri':  REDIRECT_URI,
      'code_verifier': code_verifier
    }
    r = requests.post('https://accounts.spotify.com/api/token',
                      data=data,
                      headers={'Content-Type':'application/x-www-form-urlencoded'})
    if not r.ok:
        return f"Token exchange failed: {r.text}", 500

    access_token = r.json()['access_token']
    code_verifier = None
    return redirect('/')    # back to index.html

# ── 3) TOKEN POLLING ───────────────────────────────────────────────
@app.route('/token')
def token():
    if not access_token:
        return ('', 204)
    return jsonify(access_token=access_token)

# ── 4) RUN ────────────────────────────────────────────────────────
if __name__ == '__main__':
    # Auto-open local browser tab on the Pi
    def _open():
        import time
        time.sleep(1)
        webbrowser.open(f'http://127.0.0.1:{PORT}/')
    threading.Thread(target=_open, daemon=True).start()

    app.run(host='0.0.0.0', port=PORT)