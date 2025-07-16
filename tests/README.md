# BeoSound 5c Testing Framework

This testing framework provides comprehensive tools to debug and validate the three main issues you identified:

1. **Laser pointer position → UI view mapping**
2. **Dummy hardware emulation accuracy** 
3. **Webhook delivery and Home Assistant integration**

## Quick Start

### 1. Webhook Testing
Test Home Assistant webhook integration and debug delivery issues:

```bash
# Start the webhook capture server (simulates Home Assistant)
cd tests/webhook
python3 webhook-capture-server.py

# Configure masterlink.py to send webhooks to test server
# Edit services/masterlink.py and change:
WEBHOOK_URL = "http://localhost:8123/api/webhook/beosound5c"

# View real-time webhook dashboard at:
# http://localhost:8123/
```

**Features:**
- Real-time webhook capture and validation
- JSON payload inspection and error detection
- Timing analysis and retry behavior monitoring
- Web dashboard with automatic refresh

### 2. Laser Position Mapping Tests
Validate that laser positions show the correct UI views:

```bash
# Start your web server
cd web
python3 -m http.server 8000

# Open the laser mapping test suite
open tests/hardware/test-laser-mapping.html
# Or navigate to: http://localhost:8000/tests/hardware/test-laser-mapping.html
```

**Features:**
- Interactive position slider (3-123 range)
- Automated sweep tests across full range
- Boundary condition testing
- Expected vs actual view comparison
- Visual feedback and pass/fail reporting

### 3. Dummy Hardware Validation
Compare dummy hardware simulation against real hardware behavior:

```bash
# Ensure input.py service is running
sudo systemctl status beo-input

# Open the dummy hardware test suite  
open tests/hardware/test-dummy-hardware.html
```

**Features:**
- Side-by-side real vs simulated hardware comparison
- Keyboard/mouse mapping validation
- Event timing and accuracy analysis
- Interactive controls for manual testing
- Automated accuracy test suite

### 4. Real-time Debug Dashboard
Monitor all services and events in real-time:

```bash
# Open the debug dashboard
open tests/integration/debug-dashboard.html
```

**Features:**
- Live service status monitoring
- WebSocket connection health
- Event timeline visualization  
- Performance metrics tracking
- Webhook flow visualization

## Test Scenarios

### Scenario 1: Laser Position Debugging
**Problem:** Laser pointer at position X doesn't show expected UI view

**Solution:**
1. Open `test-laser-mapping.html`
2. Set position to X using slider or quick buttons
3. Observe UI preview iframe
4. Run boundary tests to find mapping issues
5. Check console for WebSocket connection errors

### Scenario 2: Mouse/Keyboard Emulation Issues  
**Problem:** Mouse wheel or keyboard controls don't work as expected

**Solution:**
1. Open `test-dummy-hardware.html`
2. Start dummy hardware simulation
3. Use keyboard/mouse controls and observe event log
4. Compare with real hardware events if available
5. Run automated accuracy test to measure timing

### Scenario 3: Home Assistant Webhook Failures
**Problem:** Webhooks aren't reaching Home Assistant or failing

**Solution:**
1. Start `webhook-capture-server.py`
2. Configure masterlink.py to send to test server
3. Monitor webhook dashboard for delivery and errors
4. Validate JSON payload structure
5. Test webhook retry behavior and timing

## File Structure

```
tests/
├── README.md                           # This file
├── webhook/
│   └── webhook-capture-server.py       # HA webhook test server
├── hardware/
│   ├── test-laser-mapping.html        # Laser position validation
│   └── test-dummy-hardware.html       # Hardware emulation testing
└── integration/
    └── debug-dashboard.html            # Real-time monitoring
```

## Configuration

### Webhook Testing
To test webhooks with your actual masterlink.py service:

1. Edit `services/masterlink.py`:
   ```python
   WEBHOOK_URL = "http://localhost:8123/api/webhook/beosound5c"
   ```

2. Start the capture server:
   ```bash
   python3 tests/webhook/webhook-capture-server.py
   ```

3. Restart masterlink service:
   ```bash
   sudo systemctl restart beo-masterlink
   ```

### Hardware Testing
Make sure the required services are running:

```bash
# Check service status
./services/system/status-services.sh

# Start services if needed
sudo ./services/system/install-services.sh
```

## Advanced Usage

### Position Mapping Reference
The laser position (3-123) maps to these UI views:

```
Position 3-25:   → Now Showing (Apple TV media artwork)
Position 26-35:  → Settings
Position 36-42:  → Security/Camera view  
Position 43-52:  → Scenes control
Position 53-75:  → Music/Playlists
Position 76-123: → Now Playing (music artwork)
```

**Menu Structure:**
The circular menu contains 6 items at angles 155°-205° (5° steps):
1. **SHOWING** (155°) - Apple TV media display
2. **SETTINGS** (165°) - System configuration  
3. **SECURITY** (175°) - Camera/security view
4. **SCENES** (185°) - Home automation scenes
5. **MUSIC** (195°) - Spotify playlists
6. **PLAYING** (205°) - Current music playback

Edit position mapping in `test-laser-mapping.html`:

```javascript
const positionMapping = {
    'showing': { range: [3, 25], expected: 'menu/showing' },
    'settings': { range: [26, 35], expected: 'menu/settings' },
    'security': { range: [36, 42], expected: 'menu/security' },
    'scenes': { range: [43, 52], expected: 'menu/scenes' },
    'music': { range: [53, 75], expected: 'menu/music' },
    'playing': { range: [76, 123], expected: 'menu/playing' }
};
```

### Webhook Payload Validation
Customize expected webhook structure in `webhook-capture-server.py`:

```python
required_fields = ['device_name', 'key_name', 'timestamp']
expected_device_name = 'Church'
```

### Event Timing Analysis
Adjust timing tolerances in `test-dummy-hardware.html`:

```javascript
const tolerance = 100; // 100ms tolerance for event matching
```

## Troubleshooting

### Common Issues

**1. WebSocket Connection Failed**
- Ensure input.py service is running: `sudo systemctl status beo-input`
- Check port 8765 is not blocked by firewall
- Verify WebSocket URL in test files

**2. Iframe Cross-Origin Issues**
- Run tests from web server (not file://)
- Use `python3 -m http.server 8000` in project root
- Access via `http://localhost:8000/tests/...`

**3. Webhook Server Not Receiving**
- Check masterlink.py WEBHOOK_URL configuration
- Verify webhook-capture-server.py is running on correct port
- Check network connectivity between services

**4. Dummy Hardware Not Working**
- Ensure dummy-hardware.js module loads correctly
- Check browser console for JavaScript errors
- Verify mouse/keyboard events aren't being blocked

### Logs and Debugging

**Service Logs:**
```bash
# View specific service logs
journalctl -u beo-input -f
journalctl -u beo-masterlink -f

# View all BeoSound logs
journalctl -u beo-* -f
```

**Browser Console:**
- Open browser developer tools (F12)
- Check Console tab for JavaScript errors
- Monitor Network tab for WebSocket connections

**Test Logs:**
- All test interfaces include built-in logging
- Events are timestamped and categorized
- Export logs using browser's save functionality

## Contributing

To add new test scenarios:

1. Create test files in appropriate subdirectory
2. Follow existing naming convention
3. Include comprehensive error handling
4. Add documentation to this README
5. Test with both real and simulated hardware

## Support

If you encounter issues with the testing framework:

1. Check the troubleshooting section above
2. Review browser console for errors  
3. Verify all required services are running
4. Check network connectivity between components