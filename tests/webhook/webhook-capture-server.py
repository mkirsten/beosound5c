#!/usr/bin/env python3
"""
Webhook Capture Server for BeoSound 5c Testing

This standalone server captures and logs all webhook requests sent by the masterlink.py service,
providing debugging and testing capabilities for Home Assistant integration.

Usage:
    python3 webhook-capture-server.py [--port 8123] [--host 0.0.0.0]
    
Features:
- Captures all webhook payloads with timestamps
- Validates JSON structure and content
- Provides real-time logging and web dashboard
- Simulates Home Assistant webhook endpoint
- Stores webhook history for analysis
"""

import asyncio
import json
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import threading
import argparse
import sys
from collections import deque

class WebhookCapture:
    def __init__(self, max_history=100):
        self.webhooks = deque(maxlen=max_history)
        self.stats = {
            'total_received': 0,
            'total_valid': 0,
            'total_invalid': 0,
            'last_received': None,
            'commands_seen': set()
        }
        self.lock = threading.Lock()
    
    def add_webhook(self, webhook_data):
        with self.lock:
            webhook_data['id'] = self.stats['total_received'] + 1
            webhook_data['received_at'] = datetime.now().isoformat()
            
            self.webhooks.append(webhook_data)
            self.stats['total_received'] += 1
            self.stats['last_received'] = webhook_data['received_at']
            
            if webhook_data.get('valid', False):
                self.stats['total_valid'] += 1
                if 'payload' in webhook_data and 'key_name' in webhook_data['payload']:
                    self.stats['commands_seen'].add(webhook_data['payload']['key_name'])
            else:
                self.stats['total_invalid'] += 1
    
    def get_recent_webhooks(self, limit=10):
        with self.lock:
            return list(self.webhooks)[-limit:]
    
    def get_stats(self):
        with self.lock:
            stats_copy = self.stats.copy()
            stats_copy['commands_seen'] = list(self.stats['commands_seen'])
            return stats_copy

# Global webhook capture instance
webhook_capture = WebhookCapture()

class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default HTTP server logging
        pass
    
    def do_POST(self):
        """Handle POST requests (webhook captures)"""
        try:
            # Parse the webhook path
            path = self.path
            content_length = int(self.headers.get('Content-Length', 0))
            
            # Read the request body
            body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else ''
            
            # Parse JSON payload
            payload = None
            valid_json = False
            error_message = None
            
            try:
                if body:
                    payload = json.loads(body)
                    valid_json = True
            except json.JSONDecodeError as e:
                error_message = f"Invalid JSON: {str(e)}"
            
            # Validate expected webhook structure
            valid_webhook = False
            validation_errors = []
            
            if valid_json and payload:
                # Check for expected fields
                required_fields = ['device_name', 'key_name']
                for field in required_fields:
                    if field not in payload:
                        validation_errors.append(f"Missing required field: {field}")
                
                # Check device name matches expected
                if payload.get('device_name') != 'Church':
                    validation_errors.append(f"Unexpected device_name: {payload.get('device_name')}")
                
                valid_webhook = len(validation_errors) == 0
            
            # Create webhook record
            webhook_data = {
                'method': 'POST',
                'path': path,
                'headers': dict(self.headers),
                'body': body,
                'payload': payload,
                'valid': valid_json and valid_webhook,
                'error_message': error_message,
                'validation_errors': validation_errors,
                'client_address': self.client_address[0]
            }
            
            # Store webhook
            webhook_capture.add_webhook(webhook_data)
            
            # Log to console
            status = "✅ VALID" if webhook_data['valid'] else "❌ INVALID"
            command = payload.get('key_name', 'unknown') if payload else 'unknown'
            count = payload.get('count', 1) if payload else 1
            
            print(f"{datetime.now().strftime('%H:%M:%S')} {status} {command:>8} (count: {count:>2}) from {self.client_address[0]}")
            
            if error_message:
                print(f"                   Error: {error_message}")
            
            if validation_errors:
                for error in validation_errors:
                    print(f"                   Warning: {error}")
            
            # Send response (simulate Home Assistant success)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            
            response = {
                'status': 'success',
                'message': 'Webhook received and processed',
                'webhook_id': webhook_data['id']
            }
            self.wfile.write(json.dumps(response).encode('utf-8'))
            
        except Exception as e:
            print(f"❌ ERROR processing webhook: {str(e)}")
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            
            error_response = {
                'status': 'error',
                'message': str(e)
            }
            self.wfile.write(json.dumps(error_response).encode('utf-8'))
    
    def do_GET(self):
        """Handle GET requests (dashboard and API)"""
        try:
            if self.path == '/':
                # Serve the dashboard
                self.serve_dashboard()
            elif self.path == '/api/webhooks':
                # Serve recent webhooks as JSON
                self.serve_webhooks_api()
            elif self.path == '/api/stats':
                # Serve statistics as JSON
                self.serve_stats_api()
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'Not Found')
        except Exception as e:
            print(f"❌ ERROR serving GET request: {str(e)}")
            self.send_response(500)
            self.end_headers()
    
    def serve_dashboard(self):
        """Serve the webhook monitoring dashboard"""
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>BeoSound 5c Webhook Monitor</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .stat-number { font-size: 24px; font-weight: bold; color: #2196F3; }
        .stat-label { color: #666; margin-top: 5px; }
        .webhooks { background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .webhook-header { padding: 20px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
        .webhook-item { padding: 15px 20px; border-bottom: 1px solid #eee; font-family: monospace; }
        .webhook-item:last-child { border-bottom: none; }
        .valid { border-left: 4px solid #4CAF50; }
        .invalid { border-left: 4px solid #f44336; }
        .webhook-time { color: #666; font-size: 12px; }
        .webhook-command { font-weight: bold; color: #2196F3; }
        .webhook-details { color: #666; margin-top: 5px; font-size: 12px; }
        .auto-refresh { margin-left: 10px; }
        .commands-seen { margin-top: 10px; }
        .command-tag { display: inline-block; background: #e3f2fd; color: #1976d2; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin: 2px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎵 BeoSound 5c Webhook Monitor</h1>
            <p>Real-time monitoring of Home Assistant webhook calls from masterlink.py service</p>
        </div>
        
        <div class="stats" id="stats">
            <div class="stat-card">
                <div class="stat-number" id="total-webhooks">0</div>
                <div class="stat-label">Total Webhooks</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="valid-webhooks">0</div>
                <div class="stat-label">Valid Webhooks</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="invalid-webhooks">0</div>
                <div class="stat-label">Invalid Webhooks</div>
            </div>
            <div class="stat-card">
                <div class="stat-number" id="last-received">Never</div>
                <div class="stat-label">Last Received</div>
            </div>
        </div>
        
        <div class="webhooks">
            <div class="webhook-header">
                <h2>Recent Webhooks</h2>
                <label class="auto-refresh">
                    <input type="checkbox" id="auto-refresh" checked> Auto-refresh (2s)
                </label>
            </div>
            <div id="webhook-list">
                <div style="padding: 40px; text-align: center; color: #666;">
                    Waiting for webhooks... Make sure masterlink.py is running and sending to this server.
                </div>
            </div>
        </div>
    </div>

    <script>
        async function updateDashboard() {
            try {
                // Fetch stats
                const statsResponse = await fetch('/api/stats');
                const stats = await statsResponse.json();
                
                document.getElementById('total-webhooks').textContent = stats.total_received;
                document.getElementById('valid-webhooks').textContent = stats.total_valid;
                document.getElementById('invalid-webhooks').textContent = stats.total_invalid;
                
                const lastReceived = stats.last_received ? 
                    new Date(stats.last_received).toLocaleTimeString() : 'Never';
                document.getElementById('last-received').textContent = lastReceived;
                
                // Fetch recent webhooks
                const webhooksResponse = await fetch('/api/webhooks');
                const webhooks = await webhooksResponse.json();
                
                const webhookList = document.getElementById('webhook-list');
                if (webhooks.length === 0) {
                    webhookList.innerHTML = '<div style="padding: 40px; text-align: center; color: #666;">No webhooks received yet...</div>';
                } else {
                    webhookList.innerHTML = webhooks.reverse().map(webhook => {
                        const time = new Date(webhook.received_at).toLocaleTimeString();
                        const command = webhook.payload?.key_name || 'unknown';
                        const count = webhook.payload?.count || 1;
                        const device = webhook.payload?.device_name || 'unknown';
                        const statusClass = webhook.valid ? 'valid' : 'invalid';
                        const statusIcon = webhook.valid ? '✅' : '❌';
                        
                        let details = `Device: ${device}, Client: ${webhook.client_address}`;
                        if (webhook.error_message) {
                            details += `, Error: ${webhook.error_message}`;
                        }
                        if (webhook.validation_errors && webhook.validation_errors.length > 0) {
                            details += `, Warnings: ${webhook.validation_errors.join(', ')}`;
                        }
                        
                        return `
                            <div class="webhook-item ${statusClass}">
                                <div class="webhook-time">${time} ${statusIcon}</div>
                                <div class="webhook-command">${command} (count: ${count})</div>
                                <div class="webhook-details">${details}</div>
                            </div>
                        `;
                    }).join('');
                }
                
            } catch (error) {
                console.error('Failed to update dashboard:', error);
            }
        }
        
        // Initial load
        updateDashboard();
        
        // Auto-refresh setup
        let refreshInterval;
        function setupAutoRefresh() {
            const checkbox = document.getElementById('auto-refresh');
            if (checkbox.checked) {
                refreshInterval = setInterval(updateDashboard, 2000);
            } else {
                clearInterval(refreshInterval);
            }
        }
        
        document.getElementById('auto-refresh').addEventListener('change', setupAutoRefresh);
        setupAutoRefresh();
    </script>
</body>
</html>
        """
        
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))
    
    def serve_webhooks_api(self):
        """Serve recent webhooks as JSON API"""
        webhooks = webhook_capture.get_recent_webhooks(20)
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        self.wfile.write(json.dumps(webhooks, indent=2).encode('utf-8'))
    
    def serve_stats_api(self):
        """Serve statistics as JSON API"""
        stats = webhook_capture.get_stats()
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        self.wfile.write(json.dumps(stats, indent=2).encode('utf-8'))

def main():
    parser = argparse.ArgumentParser(description='BeoSound 5c Webhook Capture Server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8123, help='Port to bind to (default: 8123)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    print("🎵 BeoSound 5c Webhook Capture Server")
    print("=" * 50)
    print(f"🌐 Starting server on http://{args.host}:{args.port}")
    print(f"📊 Dashboard: http://{args.host}:{args.port}/")
    print(f"🔗 API: http://{args.host}:{args.port}/api/webhooks")
    print("=" * 50)
    print("Configure masterlink.py to send webhooks to this server:")
    print(f'   WEBHOOK_URL = "http://{args.host}:{args.port}/api/webhook/beosound5c"')
    print("=" * 50)
    print("Waiting for webhooks...")
    print()
    
    try:
        server = HTTPServer((args.host, args.port), WebhookHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Shutting down webhook capture server...")
        server.shutdown()
    except Exception as e:
        print(f"❌ Server error: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main()