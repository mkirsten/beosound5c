# Beosound 5c Recreated

## 📋 Project Overview

This project recreates the Bang & Olufsen BeoSound 5 experience using modern web technologies and hardware interfacing. It provides a touch-based circular UI that mimics the original BeoSound 5's interface while integrating with modern audio systems like Sonos and Spotify.

## 🏗️ Codebase Structure

### 📁 Directory Overview

```
├── web/                    # Web interface and UI components
├── services/               # Core system services (Python/Shell)
├── tools/                  # Utility tools and helper scripts
└── wip/                    # Work in progress / experimental code
```

### 🌐 Web Interface (`web/`)

The web interface provides the main user experience with a circular touch-based UI:

- **`index.html`** - Main application entry point with circular arc UI
- **`music.html`** - Deprecated music browser interface
- **`styles.css`** - Core styling for the circular interface
- **`js/`** - JavaScript modules:
  - `ui.js` - Main UI logic and arc-based navigation
  - `cursor-handler.js` - Event handling
  - `dummy-hardware.js` - Hardware simulation for emulation without a BS5 device
  - `arcs.js` - Arc geometry calculations 
- **`json/`** - Configuration and data files, could be provided remotely from e.g., HA
  - `scenes.json` - Scene definitions with icons and hierarchical structure
  - `settings.json` - Application settings and configuration
  - `playlists_with_tracks.json` - Spotify playlist data with track details
- **`softarc/`** - The arc formed navigation UI
  - `music.html`, `scenes.html`, `settings.html` - Individual page interfaces
  - `script.js` - Shared JavaScript functionality
  - `styles.css` - Styling

### ⚙️ Core Services (`services/`)

The system runs multiple coordinated services for hardware interfacing and media control:

#### **`masterlink.py`**
- **Purpose**: Interfaces with Bang & Olufsen custom daughter board to read IR (and MasterLink)
- **Features**: 
  - USB communication with B&O devices
  - Message queuing with deduplication
  - Home Assistant webhook integration
  - Beo4/Beo5/Beo6 remote control processing
- **Endpoints**: Sends data to Home Assistant webhook and local WebSocket

#### **`media.py`**
- **Purpose**: Provide stable media updates from Sonos
- **Features**:
  - Real-time Sonos player monitoring with artwork fetching
  - WebSocket server on port 8766 for media updates
  - Automatic change detection and push notifications
  - Album artwork processing and caching, to overcome HA limitations/bugs
- **Integration**: Communicates with Sonos devices

#### **`input.py`**
- **Purpose**: Input processing to detect user input and send to the UI through WebSockets
- **Features**:
  - HID interface with rotary encoder
  - Navigation, volume, and button event processing
  - LED and backlight control, including screen management
  - Power button debouncing
- **Hardware**: Direct USB HID communication with original BS5 hardware

#### **`ui.sh`** (48 lines)
- **Purpose**: Launches the kiosk-mode web interface
- **Features**:
  - Chromium browser in fullscreen kiosk mode
  - Cache clearing and performance optimization
  - Screen resolution enforcement (1024×768 @ 60fps)
  - Display server management
- **Hardware**: Shows on HDMI 1 on the built in BS5 screen

#### **`bluetooth.sh`** (201 lines)
- **Purpose**: Bluetooth connectivity and remote control handling
- **Features**
  - Power management and reconnection logic
  - Mapping of bluetooth events
  - Debounce logic to accurately detect clicks as well as holds (for scrolling)
- **Hardware**: Uses Raspberry Pi 5 built in bluetooth to communicate with a BeoRemote One (and potentially later BeoSound Essence)

### 🛠️ System Services (`services/system/`)

Systemd service definitions and management scripts:

- **Service Files**: `beo-*.service` files for each component
- **Management Scripts**:
  - `install-services.sh` - Automated service installation
  - `uninstall-services.sh` - Service removal
  - `status-services.sh` - Service status monitoring
- **Service Dependencies**:
  - `beo-http` (port 8000) → `beo-ui` → UI display
  - `beo-media` (port 8766) → Media/Sonos integration  
  - `beo-input` (port 8765) → Hardware input processing
  - `beo-bluetooth`, `beo-masterlink` → Remote control processing

### 🔧 Tools & Utilities (`tools/`)

#### **Spotify Integration (`tools/spotify/`)**
- **`getplaylists.py`** - Fetches Spotify playlists and writes to `web/json/playlists_with_tracks.json`
- **`spotifyserver.py`** - PKCE OAuth2 server for Spotify authentication (port 8888)

#### **Sonos Tools (`tools/`)**
- **`sonos_artwork_api.py`** - Standalone Sonos artwork API server for testing
- **`sonos_artwork_viewer.py`** - Direct Sonos artwork viewer utility
- **`requirements_sonos.txt`** - Python dependencies for Sonos integration

#### **Bluetooth Tools (`tools/bt/`)**
- **`adv.py`**, `dump.py` - Bluetooth advertising and debugging utilities
- **`gatt.sh`**, `beoremote_gatt.sh` - GATT service management
- **`beo2.sh`** - Bluetooth remote control configuration

#### **USB Tools (`tools/usb/`)**
- **`client.py`** - USB client communication utility
- **`dump.py`** - USB traffic analysis and debugging
- **`usbsend.py`** - USB message sending utility

#### **System Utilities (`tools/`)**
- **`ap_scan.sh`** - Access point scanning for WiFi
- **`monitor.sh`** - System monitoring script to detect e.g., power throttling

### 🧪 Work in Progress (`wip/`)

Experimental and development code:
- **`cameratest.html`** - Camera integration testing
- **Splash screens** - Boot/loading screen assets for later implementation
- **`spotify/`** - Alternative Spotify integration experiments
- **`old-web/`** - Previous web interface iterations

## 🔄 Service Communication Flow

```
Hardware Input over USB → input.py (port 8765) → WebSocket clients
MasterLink Bus ad IR → masterlink.py → Home Assistant webhook
Sonos Player → media.py (port 8766) → WebSocket clients
Web Browser ← ui.sh ← HTTP Server (port 8000) ← Static files
```

## 🚀 Quick Start

1. **Install System Services**:
   ```bash
   cd services/system
   sudo ./install-services.sh
   ```

2. **Access Interface**:
   - Main UI: `http://localhost:8000`
---

### 🖥 BeoSound 5 Panel — Forced HDMI Settings (1024×768 @ 60Hz)
```ini
hdmi_force_hotplug=1      # Pretend HDMI is always connected
disable_overscan=1        # Remove any black borders

hdmi_group=2              # Use DMT timings (computer monitor)
hdmi_mode=16              # 1024x768 @ 60 Hz (DMT mode 16)
hdmi_drive=2              # Full HDMI (with audio), not DVI mode
```

### 📐 Match Framebuffer to Panel Resolution
```ini
framebuffer_width=1024
framebuffer_height=768
```

---

## 🔌 USB Permissions (BeoSound 5 Rotary Encoder)

Create a custom `udev` rule to allow non-root access to the device:

```bash
sudo tee /etc/udev/rules.d/99-bs5.rules <<'EOF'
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", \
  ATTRS{idVendor}=="0cd4", ATTRS{idProduct}=="1112", \
  MODE="0666"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```

---

## 📦 Install Required Packages

### 🧼 Install correct Python packages
```bash
sudo apt update
sudo apt install -y libhidapi-hidraw0 libhidapi-dev python3-dev python3-hidapi python3-hid python3-websockets python3-websocket unclutter-xfixes
sudo apt install --no-install-recommends xserver-xorg x11-xserver-utils xinit openbox chromium-browser
```

---

## 🏠 Home Assistant Configuration

### 🖼 Kiosk Mode for Fullscreen Display

Use [`kiosk-mode`](https://github.com/NemesisRE/kiosk-mode) to remove the HA sidebar and top bar when embedding in an iframe or touch interface.

### 🛠 `configuration.yaml`

Add the following sections to your HA config:

```yaml
frontend:
  themes: !include_dir_merge_named themes
  extra_module_url:
    - /hacsfiles/kiosk-mode/kiosk-mode.js?v1.0.0

http:
  cors_allowed_origins:
    - "*"  # Allow any client to call HA's REST API
  use_x_forwarded_for: true
  use_x_frame_options: false
  trusted_proxies:
    - 127.0.0.1

homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - 192.168.1.233
        - 192.168.1.75
      trusted_users:
        192.168.1.233:
          - fa968e59c60a41ada8617d51349fd341
        192.168.1.75:
          - fa968e59c60a41ada8617d51349fd341
      allow_bypass_login: true
    - type: homeassistant
```

---
