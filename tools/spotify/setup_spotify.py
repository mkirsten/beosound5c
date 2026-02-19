#!/usr/bin/env python3
"""
Spotify OAuth Setup Wizard for BeoSound 5c (PKCE flow).

Starts a plain HTTP server that guides users through Spotify OAuth.
No SSL certificates, no client_secret — uses PKCE for security.
The app's client_id is shipped in config/default.json.

Usage:
    python3 setup_spotify.py
"""

import json
import os
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

# Add project paths for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
sys.path.insert(0, SCRIPT_DIR)

from pkce import (
    generate_code_verifier,
    generate_code_challenge,
    build_auth_url,
    exchange_code,
    refresh_access_token,
)
from token_store import save_tokens, load_tokens

# Server config
PORT = 8888
SCOPES = 'playlist-read-private playlist-read-collaborative user-read-playback-state user-modify-playback-state user-read-currently-playing streaming'

# Config file paths
CONFIG_PATHS = [
    '/etc/beosound5c/config.json',
    os.path.join(PROJECT_ROOT, 'config', 'default.json'),
]

# Temporary storage during OAuth flow
_pkce_state = {}


def load_client_id():
    """Load client_id from config.json."""
    for path in CONFIG_PATHS:
        try:
            with open(path) as f:
                config = json.load(f)
            cid = config.get('spotify', {}).get('client_id', '')
            if cid:
                return cid
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            continue

    # Check token store (from a previous setup)
    tokens = load_tokens()
    if tokens and tokens.get('client_id'):
        return tokens['client_id']

    return ''


def get_local_ip():
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def get_hostname():
    """Get the hostname with .local suffix."""
    hostname = socket.gethostname()
    if not hostname.endswith('.local'):
        hostname += '.local'
    return hostname


def print_qr_code(url):
    """Print a QR code to terminal if qrencode is available."""
    try:
        result = subprocess.run(
            ['qrencode', '-t', 'ANSIUTF8', url],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(result.stdout)
            return True
    except FileNotFoundError:
        pass
    return False


def run_playlist_fetch(client_id, refresh_token):
    """Run the playlist fetch using the new tokens. Returns count."""
    try:
        token_data = refresh_access_token(client_id, refresh_token)
        access_token = token_data['access_token']

        # Persist rotated refresh_token if provided
        new_rt = token_data.get('refresh_token')
        if new_rt and new_rt != refresh_token:
            save_tokens(client_id, new_rt)

        headers = {'Authorization': f'Bearer {access_token}'}
        playlists = []
        url = 'https://api.spotify.com/v1/me/playlists?limit=50'

        while url:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            playlists.extend(data.get('items', []))
            url = data.get('next')

        return len(playlists)
    except Exception as e:
        print(f"Playlist fetch failed: {e}")
        return 0


# ── HTML templates ──

def get_setup_page_html(client_id, local_ip, hostname):
    redirect_uri = f'http://{local_ip}:{PORT}/callback'

    # If client_id is pre-configured, skip to "Connect" directly
    if client_id:
        cred_section = f'''
        <div class="step">
            <div class="step-title"><span class="step-number">1</span>Connect your Spotify account</div>
            <div class="step-content">
                <p>Click the button below to authorize BeoSound 5c to access your Spotify playlists and control playback.</p>
                <form action="/start-auth" method="GET">
                    <input type="hidden" name="client_id" value="{client_id}">
                    <button type="submit" class="submit-btn">Connect to Spotify &rarr;</button>
                </form>
            </div>
        </div>'''
    else:
        cred_section = f'''
        <div class="step">
            <div class="step-title"><span class="step-number">1</span>Create a Spotify App</div>
            <div class="step-content">
                <p>Go to the <a href="https://developer.spotify.com/dashboard" target="_blank">Spotify Developer Dashboard</a> and create a new app.</p>
                <p>Set the Redirect URI to:</p>
                <div class="uri-box" id="redirect-uri">{redirect_uri}</div>
                <button class="copy-btn" onclick="copyUri()">Copy to clipboard</button>
                <p style="margin-top: 12px;">Under "Which API/SDKs are you planning to use?", select <strong>Web API</strong>.</p>
            </div>
        </div>

        <div class="step">
            <div class="step-title"><span class="step-number">2</span>Enter Client ID</div>
            <div class="step-content">
                <p>Copy the Client ID from your Spotify app (no secret needed):</p>
                <form action="/start-auth" method="GET">
                    <label for="client_id">Client ID</label>
                    <input type="text" id="client_id" name="client_id" required placeholder="e.g., a1b2c3d4e5f6...">
                    <button type="submit" class="submit-btn">Connect to Spotify &rarr;</button>
                </form>
            </div>
        </div>'''

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BeoSound 5c - Spotify Setup</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Helvetica Neue', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #000;
            color: #fff;
            padding: 20px;
            line-height: 1.7;
        }}
        .container {{ max-width: 500px; margin: 0 auto; }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid #333;
        }}
        h1 {{ font-size: 24px; font-weight: 300; letter-spacing: 2px; margin-bottom: 8px; }}
        .subtitle {{ color: #666; font-size: 14px; }}
        .step {{
            background: #111;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 16px;
            border: 1px solid #222;
        }}
        .step-number {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            border: 2px solid #1ED760;
            color: #1ED760;
            border-radius: 50%;
            font-weight: 600;
            font-size: 14px;
            margin-right: 12px;
        }}
        .step-title {{
            font-size: 16px;
            font-weight: 500;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
        }}
        .step-content {{ color: #999; font-size: 14px; margin-left: 40px; }}
        .step-content p {{ margin-bottom: 8px; }}
        a {{ color: #999; text-decoration: underline; text-decoration-color: #666; text-underline-offset: 2px; }}
        a:hover {{ color: #fff; text-decoration-color: #fff; }}
        .uri-box {{
            background: #000;
            border: 1px solid #333;
            border-radius: 4px;
            padding: 12px;
            margin: 12px 0;
            font-family: 'SF Mono', Monaco, Consolas, monospace;
            font-size: 12px;
            word-break: break-all;
        }}
        .copy-btn {{
            background: #222;
            border: 1px solid #333;
            color: #999;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            transition: all 0.2s;
        }}
        .copy-btn:hover {{ background: #333; color: #fff; }}
        .copy-btn.copied {{ background: #1ED760; border-color: #1ED760; color: #000; }}
        input[type="text"] {{
            width: 100%;
            padding: 12px;
            margin: 8px 0;
            background: #000;
            border: 1px solid #333;
            border-radius: 4px;
            color: #fff;
            font-size: 14px;
            font-family: inherit;
        }}
        input:focus {{ outline: none; border-color: #1ED760; }}
        input::placeholder {{ color: #444; }}
        label {{
            display: block;
            margin-top: 12px;
            color: #666;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .submit-btn {{
            width: 100%;
            padding: 14px;
            margin-top: 20px;
            background: #1ED760;
            border: none;
            border-radius: 4px;
            color: #000;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }}
        .submit-btn:hover {{ background: #1db954; }}
        .note {{
            background: #0a0a0a;
            border: 1px solid #222;
            border-radius: 4px;
            padding: 12px;
            margin: 12px 0;
            font-size: 13px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>SPOTIFY SETUP</h1>
            <div class="subtitle">BeoSound 5c</div>
        </div>

        <div class="note">
            No secret keys needed. This uses PKCE authentication &mdash; your Spotify
            credentials never touch this device.
        </div>

        {cred_section}
    </div>

    <script>
        function copyUri() {{
            const uri = document.getElementById('redirect-uri')?.textContent;
            if (!uri) return;
            navigator.clipboard.writeText(uri).then(() => {{
                const btn = document.querySelector('.copy-btn');
                btn.textContent = 'Copied!';
                btn.classList.add('copied');
                setTimeout(() => {{
                    btn.textContent = 'Copy to clipboard';
                    btn.classList.remove('copied');
                }}, 2000);
            }});
        }}
    </script>
</body>
</html>'''


def get_success_page_html(playlist_count):
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BeoSound 5c - Setup Complete</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Helvetica Neue', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #000;
            color: #fff;
            padding: 20px;
            line-height: 1.7;
            text-align: center;
        }}
        .container {{ max-width: 500px; margin: 50px auto; }}
        .checkmark {{
            width: 80px; height: 80px;
            border: 3px solid #1ED760;
            border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            margin: 0 auto 30px;
            font-size: 36px; color: #1ED760;
        }}
        h1 {{ font-size: 24px; font-weight: 300; margin-bottom: 20px; letter-spacing: 1px; }}
        .status {{
            background: #111; border: 1px solid #222;
            border-radius: 8px; padding: 20px; margin: 20px 0; text-align: left;
        }}
        .status-item {{
            display: flex; align-items: center;
            padding: 12px 0; border-bottom: 1px solid #222; color: #999;
        }}
        .status-item:last-child {{ border-bottom: none; }}
        .status-icon {{ color: #1ED760; margin-right: 12px; font-size: 18px; }}
        .note {{ color: #666; font-size: 14px; margin-top: 30px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="checkmark">&#10003;</div>
        <h1>Connected to Spotify</h1>
        <div class="status">
            <div class="status-item">
                <span class="status-icon">&#10003;</span>
                <span>PKCE authorization successful</span>
            </div>
            <div class="status-item">
                <span class="status-icon">&#10003;</span>
                <span>Tokens saved securely</span>
            </div>
            <div class="status-item">
                <span class="status-icon">&#10003;</span>
                <span>Found {playlist_count} playlists</span>
            </div>
        </div>
        <p class="note">Setup complete. You can close this page.<br>
        Playlists will refresh automatically.</p>
    </div>
</body>
</html>'''


def get_error_page_html(error_message):
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BeoSound 5c - Setup Error</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Helvetica Neue', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #000; color: #fff; padding: 20px;
            line-height: 1.7; text-align: center;
        }}
        .container {{ max-width: 500px; margin: 50px auto; }}
        .error-icon {{
            width: 80px; height: 80px;
            border: 3px solid #c33; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            margin: 0 auto 30px; font-size: 36px; color: #c33;
        }}
        h1 {{ font-size: 24px; font-weight: 300; margin-bottom: 20px; letter-spacing: 1px; }}
        .error-box {{
            background: #110000; border-left: 3px solid #c33;
            border-radius: 4px; padding: 20px; margin: 20px 0;
            text-align: left; font-family: 'SF Mono', Monaco, Consolas, monospace;
            font-size: 13px; color: #c33;
        }}
        a {{ color: #999; text-decoration: underline; text-decoration-color: #666; }}
        a:hover {{ color: #fff; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="error-icon">!</div>
        <h1>Setup Error</h1>
        <div class="error-box">{error_message}</div>
        <p><a href="/">Try again</a></p>
    </div>
</body>
</html>'''


# ── HTTP Handler ──

class SetupHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.command}] {self.path}")

    def send_html(self, html, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(html.encode()))
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/':
            client_id = load_client_id()
            local_ip = get_local_ip()
            hostname = get_hostname()
            html = get_setup_page_html(client_id, local_ip, hostname)
            self.send_html(html)

        elif parsed.path == '/start-auth':
            self.handle_start_auth(parsed.query)

        elif parsed.path == '/callback':
            self.handle_callback(parsed.query)

        else:
            self.send_error(404)

    def handle_start_auth(self, query_string):
        """Start PKCE auth flow — generate verifier, redirect to Spotify."""
        global _pkce_state
        params = urllib.parse.parse_qs(query_string)
        client_id = params.get('client_id', [''])[0].strip()

        if not client_id:
            self.send_html(get_error_page_html('Client ID is required'), 400)
            return

        # Generate PKCE pair
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)

        local_ip = get_local_ip()
        redirect_uri = f'http://{local_ip}:{PORT}/callback'

        # Store for callback
        _pkce_state = {
            'client_id': client_id,
            'code_verifier': verifier,
            'redirect_uri': redirect_uri,
        }

        auth_url = build_auth_url(client_id, redirect_uri, challenge, SCOPES)

        # Redirect to Spotify
        self.send_response(302)
        self.send_header('Location', auth_url)
        self.end_headers()
        print(f"Redirecting to Spotify authorization...")

    def handle_callback(self, query_string):
        """Handle OAuth callback from Spotify."""
        global _pkce_state
        params = urllib.parse.parse_qs(query_string)

        if 'error' in params:
            error = params['error'][0]
            self.send_html(get_error_page_html(f'Spotify authorization failed: {error}'))
            return

        code = params.get('code', [''])[0]
        if not code:
            self.send_html(get_error_page_html('No authorization code received'))
            return

        if not _pkce_state:
            self.send_html(get_error_page_html('Session expired. Please start over.'))
            return

        client_id = _pkce_state['client_id']
        code_verifier = _pkce_state['code_verifier']
        redirect_uri = _pkce_state['redirect_uri']

        try:
            print("Exchanging authorization code for tokens (PKCE)...")
            token_data = exchange_code(code, client_id, code_verifier, redirect_uri)
            rt = token_data.get('refresh_token')

            if not rt:
                self.send_html(get_error_page_html('No refresh token received from Spotify'))
                return

            # Save tokens
            print("Saving tokens...")
            path = save_tokens(client_id, rt)
            print(f"Tokens saved to {path}")

            # Fetch playlists
            print("Fetching playlists...")
            playlist_count = run_playlist_fetch(client_id, rt)

            self.send_html(get_success_page_html(playlist_count))

            print("\nSetup complete! Server will stop.")
            self.server.should_stop = True

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else str(e)
            self.send_html(get_error_page_html(f'Token exchange failed: {error_body}'))
        except Exception as e:
            self.send_html(get_error_page_html(f'Error: {str(e)}'))


def main():
    hostname = get_hostname()
    local_ip = get_local_ip()
    client_id = load_client_id()

    url = f"http://{local_ip}:{PORT}"

    print()
    print("=" * 60)
    print("BeoSound 5c - Spotify Setup (PKCE)")
    print("=" * 60)
    print()
    if client_id:
        print(f"  Client ID: {client_id[:8]}...{client_id[-4:]}")
    else:
        print("  No client_id configured — user will enter manually")
    print()
    print("Open this URL in your browser:")
    print()
    print_qr_code(url)
    print(f"  {url}")
    print()
    print(f"  (or http://{hostname}:{PORT})")
    print()
    print("Waiting for connection...")
    print()

    server = HTTPServer(('', PORT), SetupHandler)
    server.should_stop = False

    try:
        while not server.should_stop:
            server.handle_request()
    except KeyboardInterrupt:
        print("\nServer stopped.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
