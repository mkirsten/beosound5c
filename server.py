#!/usr/bin/env python3
"""
Simple HTTP Server for BeoSound 5 Controller

This script starts a local web server to serve the BeoSound 5 web application,
avoiding the security limitations of the file:// protocol.

Usage:
    python server.py

Then visit:
    http://localhost:8000/web/index.html
"""

import http.server
import socketserver
import webbrowser
import os
import time
from pathlib import Path

# Configuration
PORT = 8000
WEB_PATH = "/web/index.html"

class BeoSoundHandler(http.server.SimpleHTTPRequestHandler):
    """Custom handler to provide better logging for the BeoSound app"""
    
    def log_message(self, format, *args):
        """Override to provide colored and formatted logs"""
        if "200" in args[1]:
            status_color = "\033[92m"  # Green
        elif "404" in args[1]:
            status_color = "\033[91m"  # Red
        else:
            status_color = "\033[93m"  # Yellow
        
        print(f"{status_color}{self.log_date_time_string()} {args[1]}\033[0m {self.address_string()} -> {args[0]}")

def main():
    """Start the server and open the browser"""
    # Get the directory where this script is located
    script_dir = Path(__file__).parent.absolute()
    os.chdir(script_dir)
    
    # Start the server
    handler = BeoSoundHandler
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"\033[94m{'=' * 50}\033[0m")
        print(f"\033[1m\033[96mBeoSound 5 Controller Server\033[0m")
        print(f"\033[94m{'=' * 50}\033[0m")
        print(f"\033[92m✓\033[0m Server started at http://localhost:{PORT}")
        print(f"\033[92m✓\033[0m Application URL: \033[4mhttp://localhost:{PORT}{WEB_PATH}\033[0m")
        print("\nPress Ctrl+C to stop the server...")
        
        # Open browser after a short delay
        def open_browser():
            """Open the browser after a short delay"""
            time.sleep(1)
            webbrowser.open(f"http://localhost:{PORT}{WEB_PATH}")
        
        import threading
        threading.Thread(target=open_browser).start()
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\033[93mShutting down server...\033[0m")
            httpd.shutdown()

if __name__ == "__main__":
    main() 