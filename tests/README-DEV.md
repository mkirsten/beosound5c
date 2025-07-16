# 🍎 macOS Development Testing for BeoSound 5c

## Overview

This testing framework is designed for **development machines (macOS)** where the BeoSound 5c hardware services are **NOT running**. It uses browser automation and JavaScript simulation to test your laser pointer position mapping and fast scroll issues.

## Quick Start

```bash
cd /Users/kirsten/Development/beosound5c

# Start web server
cd web && python3 -m http.server 8000 &
cd ..

# Run development tests
./tests/run-dev-tests.sh
```

## Development vs Production Testing

### 🏠 Development Environment (macOS)
- **Machine:** macBook/iMac without physical BS5 hardware
- **Services:** No systemd services running (beo-input, beo-http, etc.)
- **WebSocket:** No real WebSocket server on port 8765
- **Hardware:** Uses dummy-hardware.js simulation
- **Testing:** Browser-based automation and manual interaction

### 🔧 Production Environment (Raspberry Pi)
- **Machine:** Raspberry Pi 5 with physical BS5 hardware
- **Services:** All 6 systemd services running
- **WebSocket:** Real input.py WebSocket server
- **Hardware:** Actual USB HID device, laser pointer, buttons
- **Testing:** Real hardware integration tests

## What Can Be Tested in Development

### ✅ AVAILABLE IN DEVELOPMENT MODE:
- **Laser position → UI view mapping** (JavaScript simulation)
- **Mouse/keyboard emulation accuracy** (dummy-hardware.js validation)
- **UI responsiveness and transitions** (browser automation)
- **Position boundary conditions** (DOM inspection)
- **Fast scroll behavior** (your specific bug testing)
- **Menu structure validation** (6-item circular menu)

### ❌ REQUIRES PRODUCTION HARDWARE:
- Real WebSocket connections to services
- Actual hardware timing and latency
- USB HID device communication
- System service logs (`journalctl` commands)
- Physical button/encoder input
- Real laser pointer calibration

## Development Test Commands

### 1. Interactive Test Runner
```bash
./tests/run-dev-tests.sh
```
**Menu options:**
- Option 3: Automated laser position tests
- Option 4: Interactive browser tests  
- Option 7: Run all development tests

### 2. Direct Commands
```bash
# Automated browser testing
python3 tests/hardware/dev-laser-test.py

# Dummy hardware validation
python3 tests/hardware/dev-dummy-test.py

# Interactive manual testing
open http://localhost:8000/tests/hardware/test-laser-mapping.html
```

## Understanding Development Test Output

### Fast Scroll Bug Detection (Your Issue)

#### ❌ BUG DETECTED:
```
Testing position 120: Fast scroll test position
    ❌ FAIL menu/playing (got: menu/showing) - 45.2ms
    ⚠️  FAST SCROLL BUG: Position 120 in Now Playing area

❌ FAST SCROLL BUG DETECTED:
Failed tests in Now Playing area (76-123):
   Position 120: Expected 'menu/playing', Got 'menu/showing'
```

**Meaning:** Fast movement to position 120 shows wrong view ("Now Showing" instead of "Now Playing")

#### ✅ BUG FIXED:
```
Testing position 120: Fast scroll test position
    ✅ PASS menu/playing - 167.2ms

✅ NO FAST SCROLL ISSUES:
All Now Playing positions (76-123) working correctly
```

## Development Environment Setup

### Prerequisites
```bash
# Install Python 3 (if not already installed)
brew install python3

# Install Chrome (for browser automation)
brew install --cask google-chrome

# Install Selenium (optional, improves testing)
pip3 install selenium

# Install any missing Python packages
pip3 install requests  # for webhook testing
```

### Start Development Server
```bash
cd /Users/kirsten/Development/beosound5c/web
python3 -m http.server 8000 &
```

## Testing Your Specific Fast Scroll Bug

### Problem Description
When laser pointer moves quickly to bottom area (positions 76-123 = "Now Playing"), it sometimes shows wrong view.

### Development Testing Approach

1. **Automated Position Testing:**
   ```bash
   python3 tests/hardware/dev-laser-test.py
   ```
   - Tests all positions including problematic 120-123 range
   - Uses JavaScript to simulate laser events
   - Measures UI response timing

2. **Interactive Manual Testing:**
   ```bash
   open http://localhost:8000/tests/hardware/test-laser-mapping.html
   ```
   - Use slider to test fast movements
   - Drag quickly from position 60 → 120
   - Check browser console for debug messages

3. **Browser Console Debugging:**
   - Press F12 → Console tab
   - Look for messages like:
     ```
     [DEBUG] Fast scroll detected: 180.0 -> 205.2
     [MENU DEBUG] Item 5 (PLAYING) - angle: 205
     ```

## Files for Development Testing

```
tests/
├── README-DEV.md               ← This file
├── DEV-QUICKSTART.md           ← Quick start guide
├── run-dev-tests.sh            ← Interactive test runner
├── hardware/
│   ├── dev-laser-test.py       ← Browser automation tests
│   ├── dev-dummy-test.py       ← Dummy hardware validation
│   ├── test-laser-mapping.html ← Interactive visual test
│   └── test-dummy-hardware.html ← Manual hardware testing
└── reports/                    ← Generated test reports
```

## Debugging Fast Scroll Issues

Based on development test results, check these code locations:

### 1. Position → Angle Conversion
**File:** `web/js/cursor-handler.js`
```javascript
function processLaserEvent(data) {
    const MIN_LASER_POS = 3;     // Check these values
    const MID_LASER_POS = 72;    // match your hardware
    const MAX_LASER_POS = 123;   // calibration
    
    // Check angle calculation for position 120
}
```

### 2. Angle → UI View Logic
**File:** `web/js/ui.js`
```javascript
const bottomOverlayStart = 200;     // May need adjustment
const bottomTransitionStart = 192;  // for fast movements

// Check overlay activation timing
```

### 3. Menu Item Mapping
**File:** `web/js/ui.js`
```javascript
this.menuItems = [
    {title: 'SHOWING', path: 'menu/showing'},    // 155°
    {title: 'SETTINGS', path: 'menu/settings'},  // 165°
    {title: 'SECURITY', path: 'menu/security'},  // 175°
    {title: 'SCENES', path: 'menu/scenes'},      // 185°
    {title: 'MUSIC', path: 'menu/music'},        // 195°
    {title: 'PLAYING', path: 'menu/playing'}     // 205°
];
```

## Limitations of Development Testing

### Cannot Test:
- Real hardware timing issues
- Actual WebSocket latency
- USB HID communication delays
- Service integration problems
- Physical button debouncing

### For These Issues:
- Test on actual Raspberry Pi hardware
- Use production test scripts
- Check service logs with `journalctl`

## Next Steps After Development Testing

### If Development Tests Fail:
1. Fix JavaScript issues in cursor-handler.js or ui.js
2. Adjust timing thresholds
3. Test again in development
4. Deploy to production for final validation

### If Development Tests Pass:
1. Issue might be hardware-specific
2. Test on production Raspberry Pi
3. Check hardware calibration values
4. Verify service timing

## Production Testing

Once development issues are resolved, test on actual hardware:

```bash
# On Raspberry Pi with hardware
sudo systemctl status beo-input beo-http
./tests/run-tests.sh laser
```

The development tests help you **identify and fix the core logic issues** before deploying to hardware!