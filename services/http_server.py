#!/usr/bin/env python3
"""BeoSound 5c HTTP server with no-cache headers.

Drop-in replacement for `python3 -m http.server 8000`.
Adds Cache-Control: no-store to every response so Chromium's
in-memory HTTP cache never serves stale files (playlist JSON,
JS, CSS, etc.).  This is appropriate for a local kiosk app.
"""

import http.server
import sys
import urllib.request
import urllib.error

sys.path.insert(0, __file__.rsplit('/', 1)[0])  # ensure services/ is on path
from lib.endpoints import input_url  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

# Paths proxied to beo-input (port 8767)
_PROXY_PREFIXES = ('/config', '/update/', '/discover/')


def _proxy_to_input(handler, method: str) -> bool:
    """Forward request to beo-input on port 8767. Returns True if handled."""
    if not any(handler.path == p or handler.path.startswith(p)
               for p in _PROXY_PREFIXES):
        return False

    url = input_url(handler.path)
    body = None
    if method == 'POST':
        length = int(handler.headers.get('Content-Length', 0))
        body = handler.rfile.read(length) if length else b''
    ct = handler.headers.get('Content-Type', 'application/json')

    try:
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={'Content-Type': ct})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            handler.send_response(resp.status)
            handler.send_header('Content-Type',
                                 resp.headers.get('Content-Type', 'application/json'))
            handler.send_header('Access-Control-Allow-Origin', '*')
            handler.end_headers()
            handler.wfile.write(data)
    except urllib.error.HTTPError as e:
        data = e.read()
        handler.send_response(e.code)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Access-Control-Allow-Origin', '*')
        handler.end_headers()
        handler.wfile.write(data)
    except Exception as e:
        handler.send_response(502)
        handler.send_header('Content-Type', 'text/plain')
        handler.end_headers()
        handler.wfile.write(str(e).encode())
    return True


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def _redirect_config(self):
        self.send_response(302)
        self.send_header('Location', '/softarc/config.html')
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path == '/config':
            self._redirect_config()
            return
        if _proxy_to_input(self, 'GET'):
            return
        super().do_GET()

    def do_HEAD(self):
        if self.path == '/config':
            self._redirect_config()
            return
        super().do_HEAD()

    def do_POST(self):
        if _proxy_to_input(self, 'POST'):
            return
        self.send_response(404)
        self.end_headers()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    with http.server.ThreadingHTTPServer(("", PORT), NoCacheHandler) as httpd:
        print(f"Serving on port {PORT} (no-cache)")
        httpd.serve_forever()
