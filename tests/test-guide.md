# BeoSound 5c Test Guide

## 1. How to Run the Tests

### Prerequisites
```bash
# Make sure services are running
sudo systemctl status beo-input    # Should be active
sudo systemctl status beo-http     # Should be active

# Check ports are available
netstat -an | grep ":8000\|:8765"  # Should show listening ports
```

### Option A: Quick Automated Test
```bash
cd /Users/kirsten/Development/beosound5c

# Run all laser position tests
./tests/run-tests.sh laser

# Or run specific test scenarios
python3 tests/hardware/run-automated-tests.py --test laser
```

### Option B: Interactive Visual Test
```bash
# Start web server
cd web
python3 -m http.server 8000 &

# Open test interface
open http://localhost:8000/tests/hardware/test-laser-mapping.html
```

### Option C: Manual Debug Testing
```bash
# Start services if needed
sudo systemctl start beo-input
sudo systemctl start beo-http

# Run the debug dashboard
open http://localhost:8000/tests/integration/debug-dashboard.html
```

## 2. Understanding Test Output

### Successful Test Output
```
🎯 Starting Automated Laser Position Test
==================================================
Testing 20 position mappings
Expected mappings:
  Position   3 → menu/showing
  Position  10 → menu/showing
  Position  30 → menu/settings
  Position  40 → menu/security
  Position  60 → menu/music
  Position  90 → menu/playing
  Position 120 → menu/playing

🔌 Connecting to WebSocket server...
✅ Connected to hardware WebSocket server
🧪 Starting position tests...

[1/20] Testing position 3...
    ✅ PASS Position 3 → menu/showing (127ms)
[2/20] Testing position 10...
    ✅ PASS Position 10 → menu/showing (98ms)
[3/20] Testing position 30...
    ✅ PASS Position 30 → menu/settings (112ms)
[4/20] Testing position 40...
    ✅ PASS Position 40 → menu/security (89ms)
[5/20] Testing position 60...
    ✅ PASS Position 60 → menu/music (156ms)
[6/20] Testing position 90...
    ✅ PASS Position 90 → menu/playing (203ms)
[7/20] Testing position 120...
    ❌ FAIL Position 120 → menu/playing (got: menu/showing) (67ms)

📊 Test Results Summary
==================================================
Total Tests: 20
Passed: 19 ✅
Failed: 1 ❌
Success Rate: 95%
Total Duration: 2847ms

❌ Failed Tests:
  Position 120: Expected 'menu/playing', Got 'menu/showing'
```

### What Each Part Means

**✅ PASS** - Laser position correctly shows expected UI view
**❌ FAIL** - Position shows wrong view (indicates bug)
**Timing (ms)** - How long UI took to respond (fast = good)

### Interpreting Failures

**Common Failure Patterns:**

1. **Fast Scrolling Bug:**
   ```
   ❌ FAIL Position 120 → menu/playing (got: menu/showing) (67ms)
   ```
   - Position 120 should show "Now Playing" but shows "Now Showing"
   - Very fast response time (67ms) suggests UI didn't update properly

2. **WebSocket Connection Issues:**
   ```
   ❌ WebSocket connection failed: Connection refused
   ```
   - input.py service not running
   - Port 8765 blocked

3. **UI Update Lag:**
   ```
   ✅ PASS Position 90 → menu/playing (1250ms)
   ```
   - Slow response (>1000ms) indicates UI performance issues

4. **Boundary Issues:**
   ```
   ❌ FAIL Position 42 → menu/security (got: menu/scenes)
   ```
   - Position boundaries need adjustment

## 3. Debugging Fast Scrolling Issues

### The Problem
When laser pointer moves quickly to bottom (Now Playing), the UI sometimes:
- Shows wrong view temporarily
- Jumps between views
- Gets "stuck" in transition

### Debug Steps

#### Step 1: Visual Inspection
```bash
# Open interactive test
cd web && python3 -m http.server 8000
open http://localhost:8000/tests/hardware/test-laser-mapping.html

# Test fast movements:
1. Set position to 60 (music)
2. Quickly drag slider to 120 (should be playing)
3. Observe if UI shows correct view
```

#### Step 2: Check Console Logs
```bash
# Open browser console (F12) and look for:
[DEBUG] Fast scroll detected: 180.0 -> 205.2
[MENU DEBUG] Item 5 (PLAYING) - angle: 205, current: 205.2, selectedMenuItem: 5
[DEBUG] Activating bottom overlay (now playing) at angle 205.2
```

#### Step 3: Timing Analysis
```bash
# Run automated test focusing on fast transitions
python3 tests/hardware/run-automated-tests.py --test laser

# Look for:
- Response times > 500ms (too slow)
- Failed tests in positions 76-123 range
- "Fast scroll detected" messages
```

### Expected vs Actual Behavior

**Expected (Correct):**
```
Position 120 → Angle 208° → "Now Playing" view → ✅ PASS
```

**Actual (Bug):**
```
Position 120 → Angle 208° → Menu briefly visible → "Now Showing" → ❌ FAIL
```

### Common Fast Scrolling Bugs

1. **Menu Animation Lag:**
   - UI shows menu items while transitioning
   - Fix: Check `menuAnimationState` timing

2. **Angle Boundary Issues:**
   - Position 120 maps to wrong angle
   - Fix: Verify `translateToRange()` calibration

3. **Overlay Activation Delays:**
   - `isNowPlayingOverlayActive` not set quickly enough
   - Fix: Reduce transition thresholds

## 4. Test Scenarios to Try

### Scenario 1: Boundary Testing
```bash
# Test edge cases
positions=(25 26 35 36 42 43 52 53 75 76)
for pos in "${positions[@]}"; do
    echo "Testing boundary position $pos"
    # Set position and verify correct view
done
```

### Scenario 2: Fast Movement Testing
```bash
# Simulate rapid movements
curl -X POST localhost:8765/ws -d '{"type":"laser","data":{"position":60}}'
sleep 0.1
curl -X POST localhost:8765/ws -d '{"type":"laser","data":{"position":120}}'
# Check if view changes correctly
```

### Scenario 3: Performance Testing
```bash
# Measure response times
./tests/run-tests.sh laser | grep "ms)" | sort -n
# Look for consistently slow responses
```

## 5. Fixing Issues Found

### If Tests Show Fast Scrolling Bug:

1. **Check cursor-handler.js:**
   ```javascript
   // Look for this function and timing
   function processLaserEvent(data) {
       // Verify MIN_LASER_POS, MAX_LASER_POS mapping
       // Check if angle calculation is correct
   }
   ```

2. **Check ui.js overlay logic:**
   ```javascript
   // Look for these thresholds
   const bottomOverlayStart = 200;  
   const bottomTransitionStart = 192;
   // May need adjustment for position 120
   ```

3. **Verify WebSocket timing:**
   ```bash
   # Check if events are processed too quickly
   journalctl -u beo-input -f | grep "laser"
   ```

### If Tests Pass But Real Hardware Fails:

1. **Calibration Issue:**
   - Real hardware positions don't match simulated positions
   - Need to re-calibrate MIN_LASER_POS, MID_LASER_POS, MAX_LASER_POS

2. **Timing Differences:**
   - Real hardware sends events faster/slower than simulation
   - Adjust debouncing in input.py

## 6. Success Criteria

**Good Test Results:**
- ✅ Success rate > 90%
- ✅ Response times < 300ms
- ✅ No boundary position failures
- ✅ Consistent behavior on repeated runs

**Need Investigation:**
- ❌ Success rate < 80%
- ❌ Any response times > 1000ms
- ❌ Failures in positions 76-123 (Now Playing area)
- ❌ Inconsistent results between runs

The tests will help you identify exactly where the fast scrolling bug occurs and provide timing data to fix it!