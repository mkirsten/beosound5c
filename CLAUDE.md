# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Bang & Olufsen BeoSound 5c recreation project that replaces the software of the BeoSound 5,  using modern technologies. It features a circular arc-based UI that integrates with Sonos players, Spotify, and original B&O hardware through multiple coordinated services.

## Device Access

**SSH to the BeoSound 5c hardware:**
```bash
ssh beosound5c.local
```
- Username: kirsten
- Code location on device: `/home/kirsten/beosound5c/`

## Development Commands

### System Service Management on the real hardware
```bash
# SSH to device first
ssh beosound5c.local

# Install all services
cd ~/beosound5c/services/system
sudo ./install-services.sh

# Check service status
./status-services.sh

# Stop all services
sudo systemctl stop beo-*

# Restart all services  
sudo systemctl restart beo-*

# View service logs
journalctl -u <service-name> -f
```

### Development Mode without real hardware (runs in web browser and uses trackpad + keyboard for input)
```bash
# Start web server for emulation mode
cd web
python3 -m http.server 8000

# Start media server (optional for Sonos integration)
cd services
python3 media.py

# Access interface at http://localhost:8000
```

### Python Dependencies
```bash
# Install Sonos integration dependencies
pip install -r tools/requirements_sonos.txt

# System packages required
sudo apt install -y python3-hidapi python3-websockets unclutter-xfixes chromium-browser
```

## Architecture Overview

### Service Communication Architecture
The system runs 6 coordinated systemd services:

- **beo-http** (port 8000): Static web server for the UI
- **beo-ui**: Chromium kiosk mode displaying the interface
- **beo-media** (port 8766): WebSocket server for Sonos player monitoring and artwork
- **beo-input** (port 8765): WebSocket server for USB HID hardware input processing
- **beo-masterlink**: IR/MasterLink bus sniffer for B&O remote controls
- **beo-bluetooth**: Bluetooth remote control processing

### Core Components

#### Web Interface (`web/`)
- **Circular Arc UI**: Touch-based navigation system mimicking original BS5
- **Hardware Emulation**: Mouse/keyboard simulation when no physical hardware present
- **JSON Configuration**: Scenes, settings, and playlists loaded from `web/json/`
- **Real-time Updates**: WebSocket connections to media and input services

#### Hardware Integration (`services/`)
- **`input.py`**: USB HID communication with original BS5 rotary encoder and buttons
- **`masterlink.py`**: B&O IR/MasterLink bus processing with Home Assistant webhooks
- **`bluetooth.sh`**: BeoRemote One bluetooth connectivity with debounced button handling
- **`media.py`**: Sonos player monitoring with artwork caching and WebSocket broadcasting

#### Service Dependencies
```
Hardware USB → input.py (8765) → WebSocket clients
Sonos → media.py (8766) → WebSocket clients  
Web files → HTTP server (8000) → ui.sh → Chromium kiosk
```

## Key Technical Details

### Hardware Integration
- **USB Permissions**: Requires udev rule for BS5 device (vendor 0cd4, product 1112)
- **Display**: Configured for 1024×768 @ 60Hz HDMI output
- **Input**: Supports both physical BS5 hardware and keyboard/mouse emulation

### **CRITICAL: Separate Hardware Input Systems**
The BeoSound 5c has **FOUR DISTINCT** hardware input systems that must be kept separate. On the real hardware, events from these are provided through the beo-input service using USB HID. If you are not running on the BS5, then events below are triggered using trackpad/mouse scrolling and keyboard, through dummy-hw.js

1. **Laser Pointer** (`laser` events):
   - Physical laser beam pointing at screen positions
   - Maps to positions 3-123 on the circular arc as the main menu, positioned to the very left
   - Controls view navigation and menu selection
   - Emulated by: Mouse wheel/trackpad scrolling
   - WebSocket event: `{type: 'laser', data: {position: 93}}`

2. **Navigation Wheel** (`nav` events):
   - Physical rotary wheel separate from laser pointer
   - Used for scrolling within views (softarc navigation in iframes)
   - Controls `topWheelPosition` (-1, 0, 1) and can have different speeds (given by an int)
   - Forwarded to iframe pages (music, settings, scenes) for internal navigation
   - Does NOT affect laser position or main menu navigation
   - Emulated by: Arrow Up/Down keys
   - WebSocket event: `{type: 'nav', data: {direction: 'clock', speed: 20}}`

3. **Volume Wheel** (`volume` events):
   - Physical volume control wheel
   - Separate from both laser pointer and navigation wheel
   - Controls volume level adjustments
   - Not implemented any functionality for this yet
   - Emulated by: PageUp/PageDown, +/- keys
   - WebSocket event: `{type: 'volume', data: {direction: 'counter', speed: 15}}`

4. **Button System** (`button` events):
   - Physical hardware buttons separate from all wheels
   - Three distinct buttons: LEFT, RIGHT, GO
   - Context-aware routing (webhooks vs iframe forwarding)
   - Emulated by: Arrow Left/Right keys, Enter, Space bar
   - WebSocket event: `{type: 'button', data: {button: 'go'}}`

**DO NOT CONFUSE** these systems - they serve completely different purposes and must remain separate!

### WebSocket Communication
- **Port 8765**: Hardware input events (navigation, buttons, volume)
- **Port 8766**: Media updates (track info, artwork, playback state)
- Real-time bidirectional communication for responsive UI updates

### Configuration Files
- **`web/json/scenes.json`**: Hierarchical scene definitions with icons
- **`web/json/settings.json`**: Application configuration
- **`web/json/playlists_with_tracks.json`**: Spotify playlist data

### Security Considerations
- Services run with appropriate user permissions
- USB device access controlled via udev rules
- WebSocket connections are local-only by default
- No sensitive credentials stored in source code

## Home Assistant Integration

The system integrates with Home Assistant for scene control and automation:
- MasterLink service sends IR commands to HA webhook
- Requires CORS configuration and trusted network authentication
- Kiosk mode removes HA sidebar for embedded display
- All logic what happens when a button is pressed is generally handeled in HA for flexibility

## File Structure Notes

- **`services/system/`**: Systemd service definitions and management scripts
- **`tools/`**: Utility scripts for Spotify OAuth, Sonos debugging, USB testing
- **`wip/`**: Experimental code and previous interface iterations
- **`web/softarc/`**: Arc-based navigation interface, used for subpages

## Development vs Production Environments

**Production Environment (beosound5c.local):**
- Raspberry Pi 5 with vanilla OS and required packages
- Physical BS5 hardware (USB input device, display)
- All 6 systemd services running (`beo-http`, `beo-ui`, `beo-media`, `beo-input`, `beo-masterlink`, `beo-bluetooth`)
- Hardware-specific features (USB HID, MasterLink, Bluetooth remotes)
- Access via: `ssh beosound5c.local`

**Development Environment:**
- Development machine (e.g., MacBook) without physical BS5 hardware
- Uses dummy hardware simulation (see `dummy-hw` in code)
- Typically runs only `media.py` manually and Python built-in web server
- System services not available - testing occurs on development machine
- Hardware input emulated via mouse/keyboard
- *IMPORTANT* This is how Claude runs the software

## Deploying Changes to Device

**IMPORTANT:** Changes made locally must be deployed to the device to take effect.

```bash
# Deploy a single file
scp web/json/scenes.json beosound5c.local:/home/kirsten/beosound5c/web/json/scenes.json

# Deploy entire web directory
rsync -avz --delete web/ beosound5c.local:/home/kirsten/beosound5c/web/

# Restart UI to pick up changes (clears browser cache)
ssh beosound5c.local "sudo systemctl restart beo-ui"

# Restart all services if needed
ssh beosound5c.local "sudo systemctl restart beo-*"
```

For Python service changes, also restart the specific service:
```bash
ssh beosound5c.local "sudo systemctl restart beo-input"  # or beo-media, etc.
```

## Development Tips

- Use emulation mode for UI development without physical hardware
- Media server can run standalone for Sonos integration testing
- Tests typically run on development machine where services are not running
- **Remember to deploy changes to device** - local edits won't show on the BS5

## Project Memories

