#!/bin/bash

# BeoSound 5c Development Mode Test Runner
# Runs tests on development machine (macOS) without hardware services

echo "🎯 BeoSound 5c Development Mode Tests"
echo "====================================="
echo "Running tests on development machine without hardware services"
echo

# Function to check development prerequisites
check_dev_prerequisites() {
    echo "🔍 Checking development environment..."
    
    # Check Python availability
    if ! command -v python3 >/dev/null 2>&1; then
        echo "❌ Python 3 not found"
        echo "   Install: brew install python3"
        return 1
    else
        echo "✅ Python 3 available"
    fi
    
    # Check for required Python packages
    if ! python3 -c "import selenium" 2>/dev/null; then
        echo "⚠️  Selenium not available (needed for UI inspection)"
        echo "   Install: pip3 install selenium"
        echo "   Note: Tests will use fallback simulation mode"
    else
        echo "✅ Selenium available for UI testing"
    fi
    
    # Check if Chrome/Chromium is available
    if command -v "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" >/dev/null 2>&1; then
        echo "✅ Chrome browser available"
    elif command -v chromium >/dev/null 2>&1; then
        echo "✅ Chromium browser available"
    else
        echo "⚠️  Chrome/Chromium not found (needed for UI inspection)"
        echo "   Install: brew install --cask google-chrome"
        echo "   Note: Tests will use simulation mode"
    fi
    
    echo "✅ Development environment ready!"
    echo
    return 0
}

# Function to start development web server
start_dev_server() {
    echo "🌐 Starting development web server..."
    
    # Check if server is already running
    if curl -s http://localhost:8000 >/dev/null 2>&1; then
        echo "✅ Web server already running on port 8000"
        return 0
    fi
    
    # Start web server in background
    cd web
    python3 -m http.server 8000 > /dev/null 2>&1 &
    WEB_SERVER_PID=$!
    cd ..
    
    # Wait for server to start
    sleep 2
    
    if curl -s http://localhost:8000 >/dev/null 2>&1; then
        echo "✅ Web server started on port 8000 (PID: $WEB_SERVER_PID)"
        return 0
    else
        echo "❌ Failed to start web server"
        return 1
    fi
}

# Function to run development mode laser tests
run_dev_laser_tests() {
    echo "🧪 Running Development Mode Laser Position Tests"
    echo "------------------------------------------------"
    echo "Note: Using simulation mode (no hardware services required)"
    echo
    
    # Run the tests in development mode
    python3 tests/hardware/dev-laser-test.py
}

# Function to run interactive browser tests
run_interactive_tests() {
    echo "🖱️  Running Interactive Browser Tests"
    echo "-------------------------------------"
    echo "Opening test interface for manual validation..."
    echo
    
    # Ensure web server is running
    start_dev_server || return 1
    
    echo "🌐 Test interfaces available:"
    echo "  • Laser Position Test: http://localhost:8000/tests/hardware/test-laser-mapping.html"
    echo "  • Hardware Simulation: http://localhost:8000/tests/hardware/test-dummy-hardware.html"
    echo "  • Debug Dashboard: http://localhost:8000/tests/integration/debug-dashboard.html"
    echo
    
    # Open the main test interface
    if command -v open >/dev/null 2>&1; then
        echo "Opening laser position test..."
        open "http://localhost:8000/tests/hardware/test-laser-mapping.html"
        sleep 2
        
        echo "🎯 Manual Test Instructions:"
        echo "1. Use the position slider (3-123) to test different positions"
        echo "2. Click 'Full Range Sweep' for automated testing"
        echo "3. Try quick movements: drag slider fast from 60 to 120"
        echo "4. Check if position 120 shows 'Now Playing' (your bug area)"
        echo "5. Look at browser console (F12) for debug messages"
    else
        echo "Open these URLs in your browser:"
        echo "http://localhost:8000/tests/hardware/test-laser-mapping.html"
    fi
}

# Function to run dummy hardware validation
run_dummy_hardware_tests() {
    echo "🎮 Running Dummy Hardware Tests"
    echo "-------------------------------"
    echo "Testing mouse/keyboard emulation without real hardware"
    echo
    
    python3 tests/hardware/dev-dummy-test.py
}

# Function to show development test guide
show_dev_test_guide() {
    echo "📋 Development Mode Testing Guide"
    echo "================================="
    echo
    echo "🏠 DEVELOPMENT ENVIRONMENT:"
    echo "• macOS development machine"
    echo "• No physical BS5 hardware"
    echo "• No systemd services running"
    echo "• Uses dummy hardware simulation"
    echo "• Browser-based testing"
    echo
    echo "🎯 WHAT WE CAN TEST:"
    echo "✅ Laser position → UI view mapping"
    echo "✅ Mouse/keyboard emulation accuracy"
    echo "✅ UI responsiveness and transitions"
    echo "✅ Position boundary conditions"
    echo "✅ Fast scroll behavior (your bug)"
    echo
    echo "❌ WHAT WE CAN'T TEST:"
    echo "• Real WebSocket connections to services"
    echo "• Actual hardware timing"
    echo "• Service logs and systemd status"
    echo "• USB HID device communication"
    echo
    echo "🔧 TESTING APPROACH:"
    echo "1. Browser-based simulation using dummy-hardware.js"
    echo "2. Direct DOM inspection for UI state validation"
    echo "3. JavaScript-based position testing"
    echo "4. Manual interaction with test interfaces"
    echo
    echo "🐛 FOR YOUR FAST SCROLL BUG:"
    echo "• Use position slider to test rapid movement to 120"
    echo "• Check browser console for timing messages"
    echo "• Verify DOM changes during fast transitions"
    echo "• Test boundary positions 75 vs 76"
    echo
}

# Function to clean up
cleanup() {
    if [ -n "$WEB_SERVER_PID" ]; then
        echo "🧹 Stopping web server (PID: $WEB_SERVER_PID)"
        kill $WEB_SERVER_PID 2>/dev/null
    fi
}

# Main menu for development testing
main_menu() {
    echo "Choose development test to run:"
    echo "1) Check Development Prerequisites"
    echo "2) Start Web Server Only"
    echo "3) Run Automated Laser Position Tests (Simulation)"
    echo "4) Run Interactive Browser Tests (Manual)"
    echo "5) Run Dummy Hardware Tests"
    echo "6) Show Development Testing Guide"
    echo "7) Run All Development Tests"
    echo "q) Quit"
    echo
    read -p "Enter choice [1-7, q]: " choice
    
    case $choice in
        1) check_dev_prerequisites ;;
        2) start_dev_server ;;
        3) 
            start_dev_server && run_dev_laser_tests
            ;;
        4) run_interactive_tests ;;
        5) run_dummy_hardware_tests ;;
        6) show_dev_test_guide ;;
        7) 
            check_dev_prerequisites && 
            start_dev_server && 
            run_dev_laser_tests &&
            echo "✅ Automated tests complete. Opening interactive tests..." &&
            run_interactive_tests
            ;;
        q) echo "Goodbye!"; exit 0 ;;
        *) echo "Invalid choice"; main_menu ;;
    esac
    
    echo
    read -p "Press Enter to return to menu..."
    main_menu
}

# Set up cleanup trap
trap cleanup EXIT

# Check if we're in the right directory
if [ ! -f "web/index.html" ]; then
    echo "❌ Please run this script from the beosound5c project root directory"
    echo "   cd $(pwd)"
    exit 1
fi

# Start main menu
main_menu