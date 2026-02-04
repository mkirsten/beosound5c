#!/bin/bash

# BeoSound 5c Debug Test Runner
# Helps run tests and interpret results for fast scrolling issues

echo "🎯 BeoSound 5c Test Runner & Debugger"
echo "====================================="
echo

# Function to check prerequisites
check_prerequisites() {
    echo "🔍 Checking prerequisites..."
    
    # Check if services are running
    if ! systemctl is-active --quiet beo-input; then
        echo "❌ beo-input service is not running"
        echo "   Fix: sudo systemctl start beo-input"
        return 1
    else
        echo "✅ beo-input service is running"
    fi
    
    if ! systemctl is-active --quiet beo-http; then
        echo "❌ beo-http service is not running"
        echo "   Fix: sudo systemctl start beo-http"
        return 1
    else
        echo "✅ beo-http service is running"
    fi
    
    # Check if ports are accessible
    if ! nc -z localhost 8765 2>/dev/null; then
        echo "❌ WebSocket port 8765 not accessible"
        echo "   Check: journalctl -u beo-input -n 10"
        return 1
    else
        echo "✅ WebSocket port 8765 accessible"
    fi
    
    if ! nc -z localhost 8000 2>/dev/null; then
        echo "❌ HTTP port 8000 not accessible"
        echo "   Check: journalctl -u beo-http -n 10"
        return 1
    else
        echo "✅ HTTP port 8000 accessible"
    fi
    
    echo "✅ All prerequisites met!"
    echo
    return 0
}

# Function to run basic automated tests
run_basic_tests() {
    echo "🧪 Running Basic Laser Position Tests"
    echo "-------------------------------------"
    
    echo "Testing key positions to validate basic functionality..."
    echo
    
    python3 tests/hardware/run-automated-tests.py --test laser
    
    echo
    echo "📊 Basic Test Analysis:"
    echo "• Look for ✅ PASS - position shows correct view"
    echo "• Look for ❌ FAIL - indicates a bug"
    echo "• Response times >500ms indicate performance issues"
    echo "• Failed tests in positions 76-123 suggest fast scroll bug"
    echo
}

# Function to run fast scroll debugging
run_fast_scroll_debug() {
    echo "🐛 Running Fast Scroll Debug Tests"
    echo "----------------------------------"
    
    echo "Testing rapid movements to identify fast scroll issues..."
    echo
    
    python3 tests/debug-fast-scroll.py
    
    echo
    echo "🔍 Fast Scroll Analysis Guide:"
    echo "• Response times <100ms may indicate UI not updating"
    echo "• Inconsistent timing suggests race conditions"
    echo "• Boundary failures (positions 75-76) indicate calibration issues"
    echo
}

# Function to run interactive test
run_interactive_test() {
    echo "🖱️  Starting Interactive Test"
    echo "-----------------------------"
    
    echo "Opening interactive test interface..."
    echo "Use this to manually test fast movements:"
    echo "1. Drag slider quickly from position 60 to 120"
    echo "2. Use quick buttons to jump between positions"
    echo "3. Watch for UI lag or incorrect views"
    echo
    
    # Check if web server is running, start if needed
    if ! nc -z localhost 8000 2>/dev/null; then
        echo "Starting web server..."
        cd web && python3 -m http.server 8000 &
        WEB_PID=$!
        sleep 2
    fi
    
    # Open test interface
    if command -v open >/dev/null 2>&1; then
        open "http://localhost:8000/tests/hardware/test-laser-mapping.html"
    else
        echo "Open this URL in your browser:"
        echo "http://localhost:8000/tests/hardware/test-laser-mapping.html"
    fi
    
    echo
    echo "🎯 What to Test Manually:"
    echo "• Set position to 60 (Music), then quickly to 120 (Now Playing)"
    echo "• Try position 123 (max) - should show Now Playing"
    echo "• Test boundary: position 75 (Music) vs 76 (Now Playing)"
    echo "• Look for 'Fast Range Sweep' test results"
    echo
}

# Function to show how to interpret results
show_result_guide() {
    echo "📋 How to Interpret Test Results"
    echo "================================"
    echo
    echo "✅ GOOD RESULTS:"
    echo "  ✅ PASS Position 90 → menu/playing (156ms)"
    echo "  → Position correctly shows Now Playing view"
    echo "  → Response time under 300ms is good"
    echo
    echo "❌ BAD RESULTS (Indicates Bugs):"
    echo "  ❌ FAIL Position 120 → menu/playing (got: menu/showing) (67ms)"
    echo "  → Position 120 should show Now Playing but shows Now Showing"
    echo "  → Very fast response (67ms) suggests UI didn't update properly"
    echo
    echo "⚠️  PERFORMANCE ISSUES:"
    echo "  ✅ PASS Position 90 → menu/playing (1250ms)"
    echo "  → Correct view but too slow (>1000ms)"
    echo "  → UI performance problem"
    echo
    echo "🎯 SPECIFIC TO YOUR FAST SCROLL BUG:"
    echo "Look for failures in positions 76-123 (Now Playing section):"
    echo "• Position shows wrong view (menu/showing instead of menu/playing)"
    echo "• Very fast response times (<100ms)"
    echo "• Inconsistent results on repeated tests"
    echo
    echo "🔧 COMMON FIXES:"
    echo "1. Fast scroll detected but wrong view:"
    echo "   → Check cursor-handler.js angle calculation"
    echo "   → Verify ui.js overlay activation thresholds"
    echo
    echo "2. Boundary position failures (75 vs 76):"
    echo "   → Re-calibrate MIN_LASER_POS, MAX_LASER_POS in cursor-handler.js"
    echo "   → Check position-to-angle mapping"
    echo
    echo "3. Slow response times:"
    echo "   → Reduce CSS transition times"
    echo "   → Check for WebSocket bottlenecks"
    echo
}

# Main menu
main_menu() {
    echo "Choose test to run:"
    echo "1) Check Prerequisites"
    echo "2) Run Basic Automated Tests"
    echo "3) Run Fast Scroll Debug Tests"
    echo "4) Run Interactive Visual Test"
    echo "5) Show Result Interpretation Guide"
    echo "6) Run All Tests"
    echo "q) Quit"
    echo
    read -p "Enter choice [1-6, q]: " choice
    
    case $choice in
        1) check_prerequisites ;;
        2) run_basic_tests ;;
        3) run_fast_scroll_debug ;;
        4) run_interactive_test ;;
        5) show_result_guide ;;
        6) 
            check_prerequisites && 
            run_basic_tests && 
            run_fast_scroll_debug
            ;;
        q) echo "Goodbye!"; exit 0 ;;
        *) echo "Invalid choice"; main_menu ;;
    esac
    
    echo
    read -p "Press Enter to return to menu..."
    main_menu
}

# Cleanup function
cleanup() {
    if [ -n "$WEB_PID" ]; then
        kill $WEB_PID 2>/dev/null
    fi
}

trap cleanup EXIT

# Start
main_menu