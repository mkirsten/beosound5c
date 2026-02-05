#!/usr/bin/env python3
"""
Spotify OAuth Setup Wizard for BeoSound 5c.

Starts a web server that guides users through Spotify OAuth setup.
Run this script, then open the displayed URL on your phone to complete setup.

Usage:
    python3 setup_spotify.py
"""

import base64
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

# Server config
PORT = 8888
SCOPES = 'playlist-read-private playlist-read-collaborative'

# SSL certificate paths (generated on first run)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_FILE = os.path.join(SCRIPT_DIR, 'server.crt')
KEY_FILE = os.path.join(SCRIPT_DIR, 'server.key')

# Config file locations (in order of preference)
CONFIG_PATHS = [
    '/etc/beosound5c/config.env',  # Production on Pi
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'services', 'config.env'),  # Development
]

# Temporary storage for credentials during OAuth flow
pending_credentials = {}


def get_config_path():
    """Get the config file path, preferring existing files."""
    for path in CONFIG_PATHS:
        if os.path.exists(path):
            return path
    # Default to development path if none exist
    return CONFIG_PATHS[1]


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
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(result.stdout)
            return True
    except FileNotFoundError:
        pass
    return False


def generate_self_signed_cert():
    """Generate a self-signed certificate for HTTPS."""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        print("Using existing SSL certificate")
        return

    print("Generating self-signed SSL certificate...")
    local_ip = get_local_ip()
    hostname = get_hostname()

    # Create openssl config for SAN (Subject Alternative Names)
    config = f"""[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = {hostname}

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = {hostname}
DNS.2 = localhost
IP.1 = {local_ip}
IP.2 = 127.0.0.1
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.cnf', delete=False) as f:
        f.write(config)
        config_file = f.name

    try:
        subprocess.run([
            'openssl', 'req', '-x509', '-nodes',
            '-days', '365',
            '-newkey', 'rsa:2048',
            '-keyout', KEY_FILE,
            '-out', CERT_FILE,
            '-config', config_file
        ], check=True, capture_output=True)
        print("SSL certificate generated")
    finally:
        os.unlink(config_file)


def update_config(client_id, client_secret, refresh_token):
    """Update config file with Spotify credentials."""
    config_path = get_config_path()

    # Read existing config if it exists
    lines = []
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            lines = f.readlines()

    # Track which keys we've updated
    updated = set()
    new_lines = []

    for line in lines:
        if line.startswith('SPOTIFY_CLIENT_ID='):
            new_lines.append(f'SPOTIFY_CLIENT_ID="{client_id}"\n')
            updated.add('client_id')
        elif line.startswith('SPOTIFY_CLIENT_SECRET='):
            new_lines.append(f'SPOTIFY_CLIENT_SECRET="{client_secret}"\n')
            updated.add('client_secret')
        elif line.startswith('SPOTIFY_REFRESH_TOKEN='):
            new_lines.append(f'SPOTIFY_REFRESH_TOKEN="{refresh_token}"\n')
            updated.add('refresh_token')
        elif line.startswith('SPOTIFY_USER_ID='):
            # Skip old USER_ID line - no longer needed
            continue
        else:
            new_lines.append(line)

    # Append any keys that weren't in the file
    if 'client_id' not in updated:
        new_lines.append(f'SPOTIFY_CLIENT_ID="{client_id}"\n')
    if 'client_secret' not in updated:
        new_lines.append(f'SPOTIFY_CLIENT_SECRET="{client_secret}"\n')
    if 'refresh_token' not in updated:
        new_lines.append(f'SPOTIFY_REFRESH_TOKEN="{refresh_token}"\n')

    # Ensure directory exists
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    with open(config_path, 'w') as f:
        f.writelines(new_lines)

    return config_path


def exchange_code_for_token(code, redirect_uri, client_id, client_secret):
    """Exchange authorization code for access and refresh tokens."""
    auth_str = f"{client_id}:{client_secret}"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()

    data = urllib.parse.urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
    }).encode()

    req = urllib.request.Request(
        'https://accounts.spotify.com/api/token',
        data=data,
        headers={
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get_setup_page_html(hostname, local_ip):
    """Generate the setup wizard HTML page."""
    redirect_uri = f'https://{local_ip}:{PORT}/callback'

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
        .container {{
            max-width: 500px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid #333;
        }}
        h1 {{
            font-size: 24px;
            font-weight: 300;
            color: #fff;
            letter-spacing: 2px;
            margin-bottom: 8px;
        }}
        .subtitle {{
            color: #666;
            font-size: 14px;
        }}
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
            border: 2px solid #c33;
            color: #c33;
            border-radius: 50%;
            font-weight: 600;
            font-size: 14px;
            margin-right: 12px;
        }}
        .step-title {{
            font-size: 16px;
            font-weight: 500;
            margin-bottom: 12px;
            color: #fff;
            display: flex;
            align-items: center;
        }}
        .step-content {{
            color: #999;
            font-size: 14px;
            margin-left: 40px;
        }}
        .step-content p {{
            margin-bottom: 8px;
        }}
        a {{
            color: #999;
            text-decoration: underline;
            text-decoration-color: #666;
            text-underline-offset: 2px;
        }}
        a:hover {{
            color: #fff;
            text-decoration-color: #fff;
        }}
        .uri-box {{
            background: #000;
            border: 1px solid #333;
            border-radius: 4px;
            padding: 12px;
            margin: 12px 0;
            font-family: 'SF Mono', Monaco, Consolas, monospace;
            font-size: 12px;
            word-break: break-all;
            color: #fff;
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
        .copy-btn:hover {{
            background: #333;
            color: #fff;
        }}
        .copy-btn.copied {{
            background: #c33;
            border-color: #c33;
            color: #fff;
        }}
        input[type="text"], input[type="password"] {{
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
        input:focus {{
            outline: none;
            border-color: #c33;
        }}
        input::placeholder {{
            color: #444;
        }}
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
            background: #c33;
            border: none;
            border-radius: 4px;
            color: #fff;
            font-size: 16px;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s;
        }}
        .submit-btn:hover {{
            background: #e44;
        }}
        .warning {{
            background: #1a1a00;
            border-left: 3px solid #ca0;
            padding: 12px 16px;
            margin: 16px 0;
            font-size: 13px;
            color: #ca0;
        }}
        .warning strong {{
            display: block;
            margin-bottom: 4px;
        }}
        .note {{
            background: #0a0a0a;
            border: 1px solid #222;
            border-radius: 4px;
            padding: 12px;
            margin: 12px 0;
            font-size: 13px;
            color: #666;
        }}
        code {{
            background: #0d1117;
            border: 1px solid #30363d;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: 'SF Mono', Monaco, Consolas, monospace;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>SPOTIFY SETUP</h1>
            <div class="subtitle">BeoSound 5c</div>
        </div>

        <div class="warning">
            <strong>Certificate Warning</strong>
            Your browser will show a security warning because this uses a self-signed certificate.
            Tap "Advanced" then "Proceed" to continue. You may need to reload the page once after accepting.
        </div>

        <div class="step">
            <div class="step-title"><span class="step-number">1</span>Create a Spotify App</div>
            <div class="step-content">
                <p>Go to the <a href="https://developer.spotify.com/dashboard" target="_blank">Spotify Developer Dashboard</a> and create a new app.</p>
                <p>Use any name and description you like.</p>
            </div>
        </div>

        <div class="step">
            <div class="step-title"><span class="step-number">2</span>Add Redirect URI</div>
            <div class="step-content">
                <p>In your Spotify app settings, add this exact Redirect URI:</p>
                <div class="uri-box" id="redirect-uri">{redirect_uri}</div>
                <button class="copy-btn" onclick="copyUri()">Copy to clipboard</button>
                <div class="warning">
                    <strong>Important</strong>
                    After adding the Redirect URI, click "Save" in the Spotify dashboard before continuing here.
                </div>
                <div class="note">
                    Alternative URI: <code>https://{hostname}:{PORT}/callback</code>
                </div>
            </div>
        </div>

        <div class="step">
            <div class="step-title"><span class="step-number">3</span>Enter Credentials</div>
            <div class="step-content">
                <p>Copy the Client ID and Client Secret from your Spotify app:</p>
                <form action="/save-credentials" method="GET">
                    <label for="client_id">Client ID</label>
                    <input type="text" id="client_id" name="client_id" required placeholder="e.g., a1b2c3d4e5f6...">

                    <label for="client_secret">Client Secret</label>
                    <input type="password" id="client_secret" name="client_secret" required placeholder="e.g., x9y8z7w6v5u4...">

                    <button type="submit" class="submit-btn">Connect to Spotify &rarr;</button>
                </form>
            </div>
        </div>
    </div>

    <script>
        function copyUri() {{
            const uri = document.getElementById('redirect-uri').textContent;
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
    """Generate the success page HTML."""
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
        .container {{
            max-width: 500px;
            margin: 50px auto;
        }}
        .checkmark {{
            width: 80px;
            height: 80px;
            border: 3px solid #5a5;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 30px;
            font-size: 36px;
            color: #5a5;
        }}
        h1 {{
            font-size: 24px;
            font-weight: 300;
            margin-bottom: 20px;
            color: #fff;
            letter-spacing: 1px;
        }}
        .status {{
            background: #111;
            border: 1px solid #222;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
            text-align: left;
        }}
        .status-item {{
            display: flex;
            align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid #222;
            color: #999;
        }}
        .status-item:last-child {{
            border-bottom: none;
        }}
        .status-icon {{
            color: #5a5;
            margin-right: 12px;
            font-size: 18px;
        }}
        .note {{
            color: #666;
            font-size: 14px;
            margin-top: 30px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="checkmark">&#10003;</div>
        <h1>Connected to Spotify</h1>

        <div class="status">
            <div class="status-item">
                <span class="status-icon">&#10003;</span>
                <span>Credentials saved</span>
            </div>
            <div class="status-item">
                <span class="status-icon">&#10003;</span>
                <span>Authorization successful</span>
            </div>
            <div class="status-item">
                <span class="status-icon">&#10003;</span>
                <span>Found {playlist_count} playlists</span>
            </div>
        </div>

        <p class="note">Setup complete. You can close this page.</p>
    </div>
</body>
</html>'''


def get_error_page_html(error_message):
    """Generate an error page HTML."""
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
            background: #000;
            color: #fff;
            padding: 20px;
            line-height: 1.7;
            text-align: center;
        }}
        .container {{
            max-width: 500px;
            margin: 50px auto;
        }}
        .error-icon {{
            width: 80px;
            height: 80px;
            border: 3px solid #c33;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 30px;
            font-size: 36px;
            color: #c33;
        }}
        h1 {{
            font-size: 24px;
            font-weight: 300;
            margin-bottom: 20px;
            color: #fff;
            letter-spacing: 1px;
        }}
        .error-box {{
            background: #110000;
            border-left: 3px solid #c33;
            border-radius: 4px;
            padding: 20px;
            margin: 20px 0;
            text-align: left;
            font-family: 'SF Mono', Monaco, Consolas, monospace;
            font-size: 13px;
            color: #c33;
        }}
        a {{
            color: #999;
            text-decoration: underline;
            text-decoration-color: #666;
        }}
        a:hover {{
            color: #fff;
        }}
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


class SetupHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the setup wizard."""

    def log_message(self, format, *args):
        """Log all requests."""
        print(f"[{self.command}] {self.path}")

    def send_html(self, html, status=200):
        """Send HTML response."""
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(html.encode()))
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        """Handle GET requests."""
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/':
            # Serve setup wizard page
            hostname = get_hostname()
            local_ip = get_local_ip()
            html = get_setup_page_html(hostname, local_ip)
            self.send_html(html)

        elif parsed.path == '/callback':
            # Handle OAuth callback
            self.handle_callback(parsed.query)

        elif parsed.path == '/save-credentials':
            # Handle GET with query params (some browsers/phones do this)
            self.handle_save_credentials_get(parsed.query)

        else:
            self.send_error(404)

    def do_POST(self):
        """Handle POST requests."""
        print(f"POST request to: {self.path}")
        if self.path == '/save-credentials':
            self.handle_save_credentials()
        else:
            print(f"404 - path not matched: '{self.path}'")
            self.send_error(404)

    def handle_save_credentials_get(self, query_string):
        """Handle GET request with credentials in query string."""
        params = urllib.parse.parse_qs(query_string)
        self._process_credentials(params)

    def handle_save_credentials(self):
        """Handle form submission with client credentials."""
        # Read form data
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode()
        params = urllib.parse.parse_qs(body)
        self._process_credentials(params)

    def _process_credentials(self, params):
        """Process credentials from either GET or POST."""
        global pending_credentials

        client_id = params.get('client_id', [''])[0].strip()
        client_secret = params.get('client_secret', [''])[0].strip()

        if not client_id or not client_secret:
            self.send_html(get_error_page_html('Client ID and Secret are required'), 400)
            return

        # Store credentials for callback
        pending_credentials = {
            'client_id': client_id,
            'client_secret': client_secret
        }

        # Build Spotify authorization URL (use IP for redirect since it's more reliable)
        local_ip = get_local_ip()
        redirect_uri = f'https://{local_ip}:{PORT}/callback'

        auth_params = urllib.parse.urlencode({
            'client_id': client_id,
            'response_type': 'code',
            'redirect_uri': redirect_uri,
            'scope': SCOPES,
        })

        auth_url = f'https://accounts.spotify.com/authorize?{auth_params}'

        # Redirect to Spotify
        self.send_response(302)
        self.send_header('Location', auth_url)
        self.end_headers()
        print(f"Redirecting to Spotify authorization...")

    def handle_callback(self, query_string):
        """Handle OAuth callback from Spotify."""
        global pending_credentials

        params = urllib.parse.parse_qs(query_string)

        # Check for error
        if 'error' in params:
            error = params['error'][0]
            self.send_html(get_error_page_html(f'Spotify authorization failed: {error}'))
            return

        # Get authorization code
        code = params.get('code', [''])[0]
        if not code:
            self.send_html(get_error_page_html('No authorization code received'))
            return

        # Check we have pending credentials
        if not pending_credentials:
            self.send_html(get_error_page_html('Session expired. Please start over.'))
            return

        client_id = pending_credentials['client_id']
        client_secret = pending_credentials['client_secret']

        # Build redirect URI (must match exactly what was used for authorization)
        local_ip = get_local_ip()
        redirect_uri = f'https://{local_ip}:{PORT}/callback'

        try:
            # Exchange code for tokens
            print("Exchanging authorization code for tokens...")
            token_data = exchange_code_for_token(code, redirect_uri, client_id, client_secret)
            refresh_token = token_data.get('refresh_token')

            if not refresh_token:
                self.send_html(get_error_page_html('No refresh token received from Spotify'))
                return

            # Save credentials to config
            print("Saving credentials to config...")
            config_path = update_config(client_id, client_secret, refresh_token)
            print(f"Credentials saved to {config_path}")

            # Run initial playlist fetch
            print("Fetching playlists...")
            playlist_count = self.run_playlist_fetch(client_id, client_secret, refresh_token)

            # Show success page
            self.send_html(get_success_page_html(playlist_count))

            # Signal to stop server
            print("\nSetup complete! Server will stop.")
            self.server.should_stop = True

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else str(e)
            self.send_html(get_error_page_html(f'Token exchange failed: {error_body}'))
        except Exception as e:
            self.send_html(get_error_page_html(f'Error: {str(e)}'))

    def run_playlist_fetch(self, client_id, client_secret, refresh_token):
        """Run the playlist fetch and return count."""
        # Get access token using refresh token
        auth_str = f"{client_id}:{client_secret}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        data = urllib.parse.urlencode({
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }).encode()

        req = urllib.request.Request(
            'https://accounts.spotify.com/api/token',
            data=data,
            headers={
                'Authorization': f'Basic {auth_b64}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            token_data = json.loads(resp.read().decode())
            access_token = token_data['access_token']

        # Fetch playlists
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


def main():
    """Run the setup wizard server."""
    hostname = get_hostname()
    local_ip = get_local_ip()

    # Generate SSL certificate if needed
    generate_self_signed_cert()

    url = f"https://{local_ip}:{PORT}"

    print()
    print("=" * 60)
    print("BeoSound 5c - Spotify Setup Wizard")
    print("=" * 60)
    print()
    print("Scan this QR code or open the URL from your phone:")
    print()
    print_qr_code(url)
    print(f"  {url}")
    print()
    print(f"  (or https://{hostname}:{PORT})")
    print()
    print("NOTE: Your browser will show a certificate warning.")
    print("      Tap 'Advanced' and 'Proceed' to continue.")
    print()
    print("Waiting for connection...")
    print()

    server = HTTPServer(('', PORT), SetupHandler)
    server.should_stop = False

    # Wrap socket with SSL
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(CERT_FILE, KEY_FILE)
    server.socket = ssl_context.wrap_socket(server.socket, server_side=True)

    try:
        while not server.should_stop:
            server.handle_request()
    except KeyboardInterrupt:
        print("\nServer stopped.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
