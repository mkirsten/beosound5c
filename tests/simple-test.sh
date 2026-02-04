#!/bin/bash

# Simple test runner that avoids any shell configuration issues

echo "🎯 BeoSound 5c Simple Development Test"
echo "======================================"

# Check if we're in the right directory
if [ ! -f "web/index.html" ]; then
    echo "❌ Please run this from the beosound5c project root:"
    echo "   cd $(pwd)"
    exit 1
fi

echo "✅ In correct directory: $(pwd)"

# Check if web server is running
if curl -s http://localhost:8000 >/dev/null 2>&1; then
    echo "✅ Web server is running on port 8000"
else
    echo "🌐 Starting web server..."
    cd web
    python3 -m http.server 8000 &
    WEB_PID=$!
    cd ..
    sleep 2
    
    if curl -s http://localhost:8000 >/dev/null 2>&1; then
        echo "✅ Web server started successfully"
    else
        echo "❌ Failed to start web server"
        exit 1
    fi
fi

echo ""
echo "🧪 Running laser position test..."
python3 tests/hardware/dev-laser-test.py

echo ""
echo "🎮 Running dummy hardware test..."
python3 tests/hardware/dev-dummy-test.py

echo ""
echo "🌐 Interactive test available at:"
echo "   http://localhost:8000/tests/hardware/test-laser-mapping.html"
echo ""
echo "💡 To test your fast scroll bug manually:"
echo "   1. Open the URL above in your browser"
echo "   2. Use the slider to test position 120"
echo "   3. Try 'Full Range Sweep' for automated testing"
echo "   4. Check browser console (F12) for debug messages"

# Cleanup function
cleanup() {
    if [ -n "$WEB_PID" ]; then
        kill $WEB_PID 2>/dev/null
    fi
}

trap cleanup EXIT