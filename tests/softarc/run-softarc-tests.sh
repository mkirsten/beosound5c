#!/bin/bash

# BeoSound 5c Softarc Navigation Test Runner
# This script runs the complete automated test suite for the softarc navigation system

echo "🎯 BeoSound 5c Softarc Navigation Test Suite"
echo "============================================"
echo ""

# Check if Node.js is available
if ! command -v node &> /dev/null
then
    echo "❌ Node.js is not installed. Please install Node.js to run these tests."
    exit 1
fi

# Change to the test directory
cd "$(dirname "$0")"

# Run the basic test first
echo "📋 Running basic validation tests..."
node test-softarc-navigation.js
basic_result=$?

if [ $basic_result -eq 0 ]; then
    echo ""
    echo "✅ Basic tests passed. Running comprehensive test suite..."
    echo ""
    
    # Track overall results
    all_passed=true
    
    # Run the complete navigation test
    echo "📋 Running complete navigation tests..."
    node test-complete-navigation.js
    complete_result=$?
    [ $complete_result -ne 0 ] && all_passed=false
    
    echo ""
    echo "📋 Running real-world bug detection tests..."
    node test-real-world-bugs.js
    bugs_result=$?
    [ $bugs_result -ne 0 ] && all_passed=false
    
    echo ""
    echo "📋 Running edge case tests..."
    node test-navigation-edge-cases.js
    edge_result=$?
    [ $edge_result -ne 0 ] && all_passed=false
    
    echo ""
    echo "📊 Complete Test Suite Summary"
    echo "============================="
    echo "Basic Tests:        $([ $basic_result -eq 0 ] && echo '✅ PASSED' || echo '❌ FAILED')"
    echo "Navigation Tests:   $([ $complete_result -eq 0 ] && echo '✅ PASSED' || echo '❌ FAILED')"
    echo "Bug Detection:      $([ $bugs_result -eq 0 ] && echo '✅ PASSED' || echo '❌ FAILED')"
    echo "Edge Cases:         $([ $edge_result -eq 0 ] && echo '✅ PASSED' || echo '❌ FAILED')"
    echo ""
    
    if [ "$all_passed" = true ]; then
        echo "🎉 All test suites passed!"
        echo "✅ The softarc navigation system is working correctly"
        echo ""
        echo "✨ Test Coverage:"
        echo "• Complete navigation workflow validated"
        echo "• Real-world bugs properly detected"
        echo "• Edge cases handled correctly"
        echo "• Infinite loop prevention working"
        echo "• Duplicate method detection active"
        echo "• Animation flag race conditions handled"
    else
        echo "⚠️  Some tests failed."
        echo "❌ Issues detected that need to be addressed"
        echo ""
        echo "🔍 Common Issues to Check:"
        echo "• Infinite render loops (alternating between render and renderChildItems)"
        echo "• Duplicate method definitions in the ArcList class"
        echo "• Animation flag getting stuck as true"
        echo "• View mode transitions not working properly"
        echo "• Breadcrumb element visibility issues"
        echo "• Container not showing any children (black screen)"
        echo ""
        echo "🎯 Debugging Tips:"
        echo "1. Check for duplicate renderChildItems() methods"
        echo "2. Ensure loadParentChildren() doesn't call render() directly"
        echo "3. Verify animation flag is properly reset with try-finally blocks"
        echo "4. Check that breadcrumb positioning is correct (-80px not -150px)"
        echo "5. Run individual test files for more detailed output"
    fi
else
    echo ""
    echo "❌ Basic tests failed. Please check the test setup and dependencies."
    exit 1
fi

echo ""
echo "🚀 Test Suite Complete"
echo "All tests can be run without human intervention"
echo "Use this script to validate navigation fixes and improvements"