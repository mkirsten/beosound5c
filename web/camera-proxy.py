#!/usr/bin/env python3
"""Simple camera proxy server using only standard library."""
import os
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HA_URL = os.getenv('HA_URL', 'http://homeassistant.local:8123')
HA_TOKEN = os.getenv('HA_TOKEN', '')

class CameraProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[CAMERA] {args[0]}")

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == '/camera/snapshot':
            entity = params.get('entity', ['camera.doorbell_medium_resolution_channel'])[0]
            self.proxy_snapshot(entity)
        elif parsed.path == '/camera/stream':
            entity = params.get('entity', ['camera.doorbell_medium_resolution_channel'])[0]
            self.proxy_stream(entity)
        else:
            self.send_error(404, 'Not Found')

    def proxy_snapshot(self, entity):
        camera_url = f'{HA_URL}/api/camera_proxy/{entity}'
        print(f"[CAMERA] Snapshot: {entity}")

        try:
            req = urllib.request.Request(camera_url)
            if HA_TOKEN:
                req.add_header('Authorization', f'Bearer {HA_TOKEN}')

            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read()
                content_type = response.headers.get('Content-Type', 'image/jpeg')

                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(content)
        except Exception as e:
            print(f"[ERROR] {e}")
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(f'{{"error": "{str(e)}"}}'.encode())

    def proxy_stream(self, entity):
        # For MJPEG stream, we'll just return snapshots for now
        # (Full MJPEG streaming requires more complex handling)
        self.proxy_snapshot(entity)

if __name__ == '__main__':
    port = 8767
    print(f'Camera proxy starting on http://0.0.0.0:{port}')
    print(f'HA_URL: {HA_URL}')
    print(f'HA_TOKEN: {"set" if HA_TOKEN else "NOT SET"}')

    server = HTTPServer(('0.0.0.0', port), CameraProxyHandler)
    server.serve_forever()
