#!/usr/bin/env python3
"""
Simple camera proxy test server using only standard library.
Proxies requests to Home Assistant camera API to avoid CORS issues.

Usage:
    python3 camera-proxy-test.py

Then open: http://localhost:8001/test
"""

import http.server
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
import socketserver
import os

# Configuration - use environment variables
HA_URL = os.getenv('HA_URL', 'http://homeassistant.local:8123')
HA_TOKEN = os.getenv('HA_TOKEN', '')
CAMERA_ENTITY = os.getenv('CAMERA_ENTITY', 'camera.doorbell_medium_resolution_channel')
PORT = 8001


class CameraProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == '/test':
            self.serve_test_page()
        elif path == '/camera/snapshot':
            entity = query.get('entity', [CAMERA_ENTITY])[0]
            self.proxy_snapshot(entity)
        elif path == '/camera/stream':
            entity = query.get('entity', [CAMERA_ENTITY])[0]
            self.proxy_stream(entity)
        else:
            self.send_error(404, 'Not found')

    def proxy_snapshot(self, entity):
        """Proxy camera snapshot from Home Assistant."""
        url = f'{HA_URL}/api/camera_proxy/{entity}'
        print(f'[SNAPSHOT] Fetching: {url}')

        try:
            req = urllib.request.Request(url)
            req.add_header('Authorization', f'Bearer {HA_TOKEN}')

            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read()
                content_type = resp.headers.get('Content-Type', 'image/jpeg')

                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', len(content))
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(content)
                print(f'[SNAPSHOT] Success: {len(content)} bytes')

        except urllib.error.HTTPError as e:
            print(f'[SNAPSHOT] HTTP Error: {e.code}')
            self.send_error(e.code, str(e.reason))
        except Exception as e:
            print(f'[SNAPSHOT] Error: {e}')
            self.send_error(500, str(e))

    def proxy_stream(self, entity):
        """Proxy MJPEG stream from Home Assistant."""
        url = f'{HA_URL}/api/camera_proxy_stream/{entity}'
        print(f'[STREAM] Fetching: {url}')

        try:
            req = urllib.request.Request(url)
            req.add_header('Authorization', f'Bearer {HA_TOKEN}')

            with urllib.request.urlopen(req, timeout=30) as resp:
                content_type = resp.headers.get('Content-Type', 'multipart/x-mixed-replace')

                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_cors_headers()
                self.end_headers()

                # Stream the content
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        except urllib.error.HTTPError as e:
            print(f'[STREAM] HTTP Error: {e.code}')
            self.send_error(e.code, str(e.reason))
        except BrokenPipeError:
            print('[STREAM] Client disconnected')
        except Exception as e:
            print(f'[STREAM] Error: {e}')
            self.send_error(500, str(e))

    def serve_test_page(self):
        """Serve a simple test page."""
        html = f'''<!DOCTYPE html>
<html>
<head>
    <title>Camera Proxy Test</title>
    <style>
        body {{ background: #000; color: #fff; font-family: sans-serif; padding: 20px; }}
        h1 {{ color: #6699ff; }}
        .container {{ display: flex; gap: 20px; flex-wrap: wrap; }}
        .box {{ background: #111; padding: 15px; border-radius: 8px; }}
        .box h3 {{ margin: 0 0 10px 0; color: #6699ff; font-size: 14px; }}
        .feed {{ width: 492px; height: 492px; background: #222; border-radius: 8px; overflow: hidden; display: flex; align-items: center; justify-content: center; }}
        .feed img {{ width: 100%; height: 100%; object-fit: cover; }}
        .status {{ margin-top: 10px; font-size: 12px; }}
        .success {{ color: #4CAF50; }}
        .error {{ color: #f44336; }}
        .note {{ background: #1a1a2e; padding: 10px; border-radius: 4px; margin-top: 20px; font-size: 12px; }}
    </style>
</head>
<body>
    <h1>Camera Proxy Test - BeoSound 5c</h1>
    <p>Entity: <code>{CAMERA_ENTITY}</code></p>

    <div class="container">
        <div class="box">
            <h3>1. Snapshot (refreshes every 2s)</h3>
            <div class="feed" id="snapshot-container">
                <img id="snapshot" src="/camera/snapshot">
            </div>
            <div class="status" id="snapshot-status">Loading...</div>
        </div>

        <div class="box">
            <h3>2. MJPEG Stream (live video)</h3>
            <div class="feed" id="stream-container">
                <img id="stream" src="/camera/stream">
            </div>
            <div class="status" id="stream-status">Loading...</div>
        </div>
    </div>

    <div class="note">
        <strong>Result:</strong> If you see the camera feed, the proxy approach works!<br>
        We'll integrate this into the BS5 input.py service for the final implementation.
    </div>

    <script>
        const snapshot = document.getElementById('snapshot');
        const snapshotStatus = document.getElementById('snapshot-status');
        const snapshotContainer = document.getElementById('snapshot-container');
        let snapshotCount = 0;

        snapshot.onload = () => {{
            snapshotCount++;
            snapshotStatus.textContent = '✓ Working! Refreshed ' + snapshotCount + ' times';
            snapshotStatus.className = 'status success';
        }};
        snapshot.onerror = () => {{
            snapshotStatus.textContent = '✗ Failed to load snapshot';
            snapshotStatus.className = 'status error';
            snapshotContainer.innerHTML = '<p style="color:#f44336;padding:20px;text-align:center">Failed to load</p>';
        }};

        // Refresh snapshot every 2 seconds
        setInterval(() => {{
            if (snapshotCount > 0) {{
                snapshot.src = '/camera/snapshot?t=' + Date.now();
            }}
        }}, 2000);

        const stream = document.getElementById('stream');
        const streamStatus = document.getElementById('stream-status');
        const streamContainer = document.getElementById('stream-container');

        stream.onload = () => {{
            streamStatus.textContent = '✓ Stream connected!';
            streamStatus.className = 'status success';
        }};
        stream.onerror = () => {{
            streamStatus.textContent = '✗ Stream failed (snapshots may still work)';
            streamStatus.className = 'status error';
        }};
    </script>
</body>
</html>'''
        content = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Handle requests in separate threads."""
    daemon_threads = True


if __name__ == '__main__':
    print(f'Starting camera proxy test server on port {PORT}')
    print(f'Camera entity: {CAMERA_ENTITY}')
    print(f'Open: http://localhost:{PORT}/test')
    print()

    server = ThreadedHTTPServer(('', PORT), CameraProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down...')
        server.shutdown()
