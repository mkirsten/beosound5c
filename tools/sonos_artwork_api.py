#!/usr/bin/env python3
"""
Sonos Artwork API Server
A simple Flask API that serves Sonos artwork directly, bypassing HA proxy issues.

Usage: python3 sonos_artwork_api.py [--port 8080] [--ip SONOS_IP]

Requirements:
    pip install flask soco pillow requests

Endpoints:
    GET /artwork - Returns current artwork as image
    GET /info - Returns current track info as JSON
    GET /health - Health check
"""

import argparse
import sys
import os
import json
from io import BytesIO
import base64
import time
from urllib.parse import urlparse

try:
    from flask import Flask, jsonify, send_file, request
except ImportError:
    print("ERROR: Flask not installed. Install with: pip install flask")
    sys.exit(1)

# Import our Sonos artwork viewer class
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from sonos_artwork_viewer import SonosArtworkViewer

app = Flask(__name__)

# Global configuration
SONOS_IP = '192.168.1.111'
viewer = None

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'sonos_ip': SONOS_IP,
        'timestamp': int(time.time())
    })

@app.route('/info')
def get_track_info():
    """Get current track information as JSON."""
    try:
        track_info = viewer.get_current_track_info()
        if not track_info:
            return jsonify({'error': 'No track information available'}), 404
        
        return jsonify({
            'status': 'success',
            'track_info': track_info,
            'timestamp': int(time.time())
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/artwork')
def get_artwork():
    """Get current artwork as image file."""
    try:
        # Get artwork URL
        artwork_url = viewer.get_artwork_url()
        if not artwork_url:
            return jsonify({'error': 'No artwork available'}), 404
        
        # Fetch artwork
        image = viewer.fetch_artwork(artwork_url)
        if not image:
            return jsonify({'error': 'Failed to fetch artwork'}), 500
        
        # Convert image to bytes
        img_io = BytesIO()
        
        # Convert to RGB if necessary (for JPEG)
        if image.mode in ('RGBA', 'LA', 'P'):
            rgb_image = image.convert('RGB')
            image = rgb_image
        
        image.save(img_io, 'JPEG', quality=95)
        img_io.seek(0)
        
        return send_file(
            img_io,
            mimetype='image/jpeg',
            as_attachment=False,
            download_name='sonos_artwork.jpg'
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/artwork/base64')
def get_artwork_base64():
    """Get current artwork as base64 encoded JSON."""
    try:
        # Get artwork URL
        artwork_url = viewer.get_artwork_url()
        if not artwork_url:
            return jsonify({'error': 'No artwork available'}), 404
        
        # Fetch artwork
        image = viewer.fetch_artwork(artwork_url)
        if not image:
            return jsonify({'error': 'Failed to fetch artwork'}), 500
        
        # Convert image to base64
        img_io = BytesIO()
        
        # Convert to RGB if necessary (for JPEG)
        if image.mode in ('RGBA', 'LA', 'P'):
            rgb_image = image.convert('RGB')
            image = rgb_image
        
        image.save(img_io, 'JPEG', quality=95)
        img_io.seek(0)
        
        # Encode as base64
        img_base64 = base64.b64encode(img_io.getvalue()).decode('utf-8')
        
        return jsonify({
            'status': 'success',
            'artwork': f'data:image/jpeg;base64,{img_base64}',
            'size': image.size,
            'mode': image.mode,
            'timestamp': int(time.time())
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/combined')
def get_combined():
    """Get both track info and artwork in one request."""
    try:
        # Get track info
        track_info = viewer.get_current_track_info()
        
        # Get artwork URL
        artwork_url = viewer.get_artwork_url()
        artwork_base64 = None
        artwork_size = None
        
        if artwork_url:
            # Fetch artwork
            image = viewer.fetch_artwork(artwork_url)
            if image:
                # Convert image to base64
                img_io = BytesIO()
                
                # Convert to RGB if necessary (for JPEG)
                if image.mode in ('RGBA', 'LA', 'P'):
                    rgb_image = image.convert('RGB')
                    image = rgb_image
                
                image.save(img_io, 'JPEG', quality=95)
                img_io.seek(0)
                
                # Encode as base64
                artwork_base64 = base64.b64encode(img_io.getvalue()).decode('utf-8')
                artwork_size = image.size
        
        return jsonify({
            'status': 'success',
            'track_info': track_info or {},
            'artwork': f'data:image/jpeg;base64,{artwork_base64}' if artwork_base64 else None,
            'artwork_size': artwork_size,
            'timestamp': int(time.time())
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def main():
    global SONOS_IP, viewer
    
    parser = argparse.ArgumentParser(
        description="Sonos Artwork API Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 sonos_artwork_api.py
  python3 sonos_artwork_api.py --port 8080 --ip 192.168.1.111
  
Endpoints:
  GET /artwork - Returns current artwork as image
  GET /info - Returns current track info as JSON
  GET /combined - Both track info and artwork
  GET /health - Health check
        """
    )
    
    parser.add_argument(
        '--port', 
        type=int,
        default=8080,
        help='Port to run the API server on (default: 8080)'
    )
    
    parser.add_argument(
        '--ip', 
        default='192.168.1.111',
        help='Sonos player IP address (default: 192.168.1.111)'
    )
    
    parser.add_argument(
        '--host', 
        default='0.0.0.0',
        help='Host to bind to (default: 0.0.0.0)'
    )
    
    parser.add_argument(
        '--debug', 
        action='store_true',
        help='Run in debug mode'
    )
    
    args = parser.parse_args()
    
    SONOS_IP = args.ip
    viewer = SonosArtworkViewer(SONOS_IP)
    
    print(f"Starting Sonos Artwork API Server...")
    print(f"Sonos IP: {SONOS_IP}")
    print(f"Server: http://{args.host}:{args.port}")
    print(f"Endpoints:")
    print(f"  GET /artwork - Current artwork as image")
    print(f"  GET /info - Current track info as JSON")
    print(f"  GET /combined - Both track info and artwork")
    print(f"  GET /health - Health check")
    
    app.run(host=args.host, port=args.port, debug=args.debug)

if __name__ == "__main__":
    main() 