#!/bin/bash

# BeoSound 5c Automated Test Runner
# This script runs all automated tests and generates reports

set -e

echo "🚀 BeoSound 5c Automated Test Suite"
echo "======================================"
echo

# Check if required services are running
check_services() {
    echo "🔍 Checking required services..."
    
    # Check if web server is running
    if ! curl -s http://localhost:8000 > /dev/null 2>&1; then
        echo "⚠️  Web server not running on port 8000"
        echo "   Starting web server..."
        cd web && python3 -m http.server 8000 &
        WEB_SERVER_PID=$!
        sleep 2
        echo "   ✅ Web server started (PID: $WEB_SERVER_PID)"
    else
        echo "   ✅ Web server is running"
    fi
    
    # Check if input service WebSocket is available
    if ! nc -z localhost 8765 2>/dev/null; then
        echo "   ⚠️  Input service WebSocket not available (port 8765)"
        echo "   Run: sudo systemctl status beo-input"
    else
        echo "   ✅ Input service WebSocket is available"
    fi
    
    # Check if webhook capture server is running
    if ! curl -s http://localhost:8123 > /dev/null 2>&1; then
        echo "   ⚠️  Webhook capture server not running (port 8123)"
        echo "   Start with: python3 tests/webhook/webhook-capture-server.py &"
    else
        echo "   ✅ Webhook capture server is running"
    fi
    echo
}

# Run laser position tests
run_laser_tests() {
    echo "🎯 Running Laser Position Tests"
    echo "--------------------------------"
    
    if command -v node > /dev/null 2>&1; then
        echo "Using Node.js test runner..."
        cd tests/hardware
        node automated-laser-test.js
        cd ../..
    else
        echo "Node.js not found, using Python test runner..."
        python3 tests/hardware/run-automated-tests.py --test laser
    fi
    echo
}

# Run dummy hardware tests
run_hardware_tests() {
    echo "🎮 Running Dummy Hardware Tests"
    echo "--------------------------------"
    python3 tests/hardware/run-automated-tests.py --test hardware
    echo
}

# Run webhook tests
run_webhook_tests() {
    echo "🔗 Running Webhook Tests"
    echo "-------------------------"
    python3 tests/hardware/run-automated-tests.py --test webhook
    echo
}

# Run all tests
run_all_tests() {
    echo "🧪 Running All Tests"
    echo "--------------------"
    python3 tests/hardware/run-automated-tests.py --test all
    echo
}

# Generate summary report
generate_summary() {
    echo "📊 Test Summary"
    echo "==============="
    
    # Find latest report files
    LATEST_REPORTS=$(find tests/reports -name "*.json" -type f -exec ls -t {} + 2>/dev/null | head -3)
    
    if [ -n "$LATEST_REPORTS" ]; then
        echo "Recent test reports:"
        for report in $LATEST_REPORTS; do
            echo "  📄 $(basename $report)"
        done
        echo
        echo "View detailed reports in: tests/reports/"
    else
        echo "No test reports found."
    fi
}

# Main execution
main() {
    # Parse command line arguments
    TEST_TYPE=${1:-all}
    
    echo "Test type: $TEST_TYPE"
    echo
    
    # Create reports directory
    mkdir -p tests/reports
    
    # Check prerequisites
    check_services
    
    # Run specified tests
    case $TEST_TYPE in
        "laser")
            run_laser_tests
            ;;
        "hardware")
            run_hardware_tests
            ;;
        "webhook")
            run_webhook_tests
            ;;
        "all")
            run_all_tests
            ;;
        *)
            echo "❌ Invalid test type: $TEST_TYPE"
            echo "Usage: $0 [laser|hardware|webhook|all]"
            exit 1
            ;;
    esac
    
    # Generate summary
    generate_summary
    
    echo "✅ Test execution completed!"
}

# Cleanup function
cleanup() {
    if [ -n "$WEB_SERVER_PID" ]; then
        echo "🧹 Cleaning up web server (PID: $WEB_SERVER_PID)"
        kill $WEB_SERVER_PID 2>/dev/null || true
    fi
}

# Set up cleanup trap
trap cleanup EXIT

# Run main function
main "$@"