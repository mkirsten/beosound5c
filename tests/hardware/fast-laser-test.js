#!/usr/bin/env node

/**
 * Fast Laser Position Test for BeoSound 5c
 * 
 * This test runs in seconds, not minutes. It tests the pure mapping function
 * without requiring web servers, browsers, or complex setup.
 * 
 * Usage: node fast-laser-test.js
 */

const fs = require('fs');
const path = require('path');

// Load the laser position mapper
const mapperPath = path.join(__dirname, '../../web/js/laser-position-mapper.js');

// Require the mapper module directly
const {
    getViewForLaserPosition,
    getDetailedMappingInfo,
    laserPositionToAngle,
    findClosestMenuItem,
    getMenuItemAngle,
    LASER_MAPPING_CONFIG
} = require(mapperPath);

// Test utilities
let testCount = 0;
let passCount = 0;
let failCount = 0;

function test(description, testFunction) {
    testCount++;
    try {
        testFunction();
        console.log(`✅ ${description}`);
        passCount++;
    } catch (error) {
        console.log(`❌ ${description}`);
        console.log(`   Error: ${error.message}`);
        failCount++;
    }
}

function assertEqual(actual, expected, message) {
    if (actual !== expected) {
        throw new Error(`${message}: expected ${expected}, got ${actual}`);
    }
}

function assertApproximatelyEqual(actual, expected, tolerance = 0.1, message) {
    if (Math.abs(actual - expected) > tolerance) {
        throw new Error(`${message}: expected ~${expected}, got ${actual} (tolerance: ${tolerance})`);
    }
}

function assertViewPath(position, expectedPath, description) {
    const result = getViewForLaserPosition(position);
    assertEqual(result.path, expectedPath, `Position ${position} should map to ${expectedPath}`);
}

// Start testing
console.log('🎯 BeoSound 5c Fast Laser Position Test');
console.log('=' .repeat(50));

// Test 1: Basic position to angle conversion
test('Position to angle conversion - boundaries', () => {
    assertApproximatelyEqual(laserPositionToAngle(3), 150, 0.1, 'Min position');
    assertApproximatelyEqual(laserPositionToAngle(72), 180, 0.1, 'Mid position');
    assertApproximatelyEqual(laserPositionToAngle(123), 210, 0.1, 'Max position');
});

test('Position to angle conversion - edge cases', () => {
    assertApproximatelyEqual(laserPositionToAngle(0), 150, 0.1, 'Below min should clamp');
    assertApproximatelyEqual(laserPositionToAngle(200), 210, 0.1, 'Above max should clamp');
    assertApproximatelyEqual(laserPositionToAngle(37.5), 165, 0.5, 'Mid-range calculation');
});

// Test 2: Overlay zones
test('Bottom overlay zone (Now Playing)', () => {
    assertViewPath(123, 'menu/playing', 'Max position');
    assertViewPath(120, 'menu/playing', 'Near max position');
    // Position 106 is first position in bottom overlay (angle 200.0)
    assertViewPath(106, 'menu/playing', 'Bottom overlay start');
});

test('Top overlay zone (Now Showing)', () => {
    assertViewPath(3, 'menu/showing', 'Min position');
    assertViewPath(10, 'menu/showing', 'Near min position');
    assertViewPath(25, 'menu/showing', 'Top overlay boundary');
});

// Test 3: Menu item selection
test('Menu item selection - specific positions', () => {
    // Test positions that should hit menu items
    const menuItems = LASER_MAPPING_CONFIG.MENU_ITEMS;
    
    // Test each menu item by finding positions that map to their angles
    for (let i = 0; i < menuItems.length; i++) {
        const itemAngle = getMenuItemAngle(i);
        const expectedPath = menuItems[i].path;
        
        // Find a position that maps to this angle (approximately)
        let testPosition = null;
        for (let pos = 30; pos <= 90; pos++) {
            const angle = laserPositionToAngle(pos);
            if (Math.abs(angle - itemAngle) < 1) {
                testPosition = pos;
                break;
            }
        }
        
        if (testPosition) {
            assertViewPath(testPosition, expectedPath, `Menu item ${i} (${menuItems[i].title})`);
        }
    }
});

// Test 4: Boundary conditions
test('Boundary conditions - transition zones', () => {
    // Test positions around the overlay boundaries
    const result160 = getViewForLaserPosition(35); // Should be around 160 degrees
    const result200 = getViewForLaserPosition(95); // Should be around 200 degrees
    
    // These should be in menu area, not overlay
    assertEqual(result160.isOverlay, false, 'Position 35 should not be in overlay');
    assertEqual(result200.isOverlay, false, 'Position 95 should not be in overlay');
});

// Test 5: Fast scroll issue test
test('Fast scroll issue - position 120', () => {
    // This was the reported bug - position 120 should show Now Playing
    const result = getViewForLaserPosition(120);
    assertEqual(result.path, 'menu/playing', 'Position 120 should show Now Playing');
    assertEqual(result.reason, 'bottom_overlay', 'Position 120 should be bottom overlay');
    assertEqual(result.isOverlay, true, 'Position 120 should be overlay');
});

// Test 6: Edge case positions
test('Edge case positions', () => {
    // Test various edge cases
    const testCases = [
        { pos: 60, desc: 'Mid-range position' },
        { pos: 75, desc: 'Near boundary position' },
        { pos: 85, desc: 'Another boundary position' },
        { pos: 93, desc: 'Default position' }
    ];
    
    testCases.forEach(({ pos, desc }) => {
        const result = getViewForLaserPosition(pos);
        // Should return a valid view path
        assertEqual(typeof result.path, 'string', `${desc} should return string path`);
        assertEqual(typeof result.reason, 'string', `${desc} should return string reason`);
        assertEqual(typeof result.isOverlay, 'boolean', `${desc} should return boolean isOverlay`);
    });
});

// Test 7: Menu item angle calculation
test('Menu item angle calculation', () => {
    const startAngle = getMenuItemAngle(0);
    const endAngle = getMenuItemAngle(LASER_MAPPING_CONFIG.MENU_ITEMS.length - 1);
    
    // Menu should be centered around 180 degrees
    const centerAngle = (startAngle + endAngle) / 2;
    assertApproximatelyEqual(centerAngle, 180, 1, 'Menu should be centered around 180 degrees');
});

// Test 8: Detailed mapping info
test('Detailed mapping info', () => {
    const info = getDetailedMappingInfo(93);
    
    // Should contain all required fields
    assertEqual(typeof info.input, 'object', 'Should have input object');
    assertEqual(typeof info.output, 'object', 'Should have output object');
    assertEqual(typeof info.thresholds, 'object', 'Should have thresholds object');
    assertEqual(Array.isArray(info.menuItems), true, 'Should have menuItems array');
    assertEqual(typeof info.debug, 'object', 'Should have debug object');
});

// Test 9: Comprehensive range test
test('Comprehensive range test', () => {
    // Test every 5th position across the entire range
    for (let pos = 3; pos <= 123; pos += 5) {
        const result = getViewForLaserPosition(pos);
        
        // Should always return a valid result
        assertEqual(typeof result.path, 'string', `Position ${pos} should return valid path`);
        assertEqual(result.position, pos, `Position ${pos} should be stored correctly`);
        
        // Angle should be in valid range
        const angle = result.angle;
        if (angle < 150 || angle > 210) {
            throw new Error(`Position ${pos} produced invalid angle: ${angle}`);
        }
    }
});

// Test 10: Performance test
test('Performance test', () => {
    const startTime = Date.now();
    
    // Run mapping function 1000 times
    for (let i = 0; i < 1000; i++) {
        const pos = 3 + (i % 120); // Cycle through positions
        getViewForLaserPosition(pos);
    }
    
    const endTime = Date.now();
    const duration = endTime - startTime;
    
    // Should complete in reasonable time (under 100ms for 1000 calls)
    if (duration > 100) {
        throw new Error(`Performance test took ${duration}ms, should be under 100ms`);
    }
    
    console.log(`   Performance: 1000 calls in ${duration}ms`);
});

// Print summary
console.log('\n📊 Test Summary');
console.log('=' .repeat(30));
console.log(`Total Tests: ${testCount}`);
console.log(`Passed: ${passCount} ✅`);
console.log(`Failed: ${failCount} ❌`);
console.log(`Success Rate: ${((passCount / testCount) * 100).toFixed(1)}%`);

if (failCount === 0) {
    console.log('\n🎉 All tests passed! Laser position mapping is working correctly.');
} else {
    console.log('\n⚠️  Some tests failed. Please review the mapping logic.');
    process.exit(1);
}

// Show example mapping for debugging
console.log('\n🔍 Example Mappings:');
console.log('-' .repeat(40));
const examplePositions = [3, 30, 60, 93, 120, 123];
examplePositions.forEach(pos => {
    const result = getViewForLaserPosition(pos);
    console.log(`Position ${pos.toString().padStart(3)}: ${result.path} (${result.reason})`);
});

console.log('\n✅ Fast laser test completed successfully!');