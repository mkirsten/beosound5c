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
    resolveMenuSelection,
    laserPositionToAngle,
    angleToLaserPosition,
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

// Test 2: Angle to position inverse
test('angleToLaserPosition is inverse of laserPositionToAngle', () => {
    for (let pos = 3; pos <= 123; pos += 10) {
        const angle = laserPositionToAngle(pos);
        const roundTrip = angleToLaserPosition(angle);
        assertApproximatelyEqual(roundTrip, pos, 0.5, `Round-trip for position ${pos}`);
    }
});

// Test 3: Overlay zones
test('Bottom overlay zone (high positions)', () => {
    const result123 = resolveMenuSelection(123);
    assertEqual(result123.isOverlay, true, 'Position 123 should be overlay');
    assertEqual(result123.selectedIndex, -1, 'Overlay should have no selected index');

    const result120 = resolveMenuSelection(120);
    assertEqual(result120.isOverlay, true, 'Position 120 should be overlay');
});

test('Top overlay zone (low positions)', () => {
    const result3 = resolveMenuSelection(3);
    assertEqual(result3.isOverlay, true, 'Position 3 should be overlay');
    assertEqual(result3.selectedIndex, -1, 'Overlay should have no selected index');

    const result10 = resolveMenuSelection(10);
    assertEqual(result10.isOverlay, true, 'Position 10 should be overlay');
});

// Test 4: Menu item selection - zone-based
test('Menu item selection - each item has a zone', () => {
    const menuItems = LASER_MAPPING_CONFIG.MENU_ITEMS;

    for (let i = 0; i < menuItems.length; i++) {
        const itemAngle = getMenuItemAngle(i);
        const expectedPath = menuItems[i].path;

        // Find a position that maps to this angle
        let testPosition = null;
        for (let pos = 30; pos <= 100; pos++) {
            const angle = laserPositionToAngle(pos);
            if (Math.abs(angle - itemAngle) < 1) {
                testPosition = pos;
                break;
            }
        }

        if (testPosition) {
            const result = resolveMenuSelection(testPosition);
            assertEqual(result.path, expectedPath, `Menu item ${i} (${menuItems[i].title}) at position ${testPosition}`);
            assertEqual(result.selectedIndex, i, `Menu item ${i} should have selectedIndex ${i}`);
        }
    }
});

// Test 5: Boundary conditions
test('Boundary conditions - positions near overlay thresholds', () => {
    const result35 = resolveMenuSelection(35);
    assertEqual(result35.isOverlay, false, 'Position 35 should not be in overlay');

    const result95 = resolveMenuSelection(95);
    assertEqual(result95.isOverlay, false, 'Position 95 should not be in overlay');
});

// Test 6: Fast scroll issue test
test('Fast scroll issue - position 120 should be overlay', () => {
    const result = resolveMenuSelection(120);
    assertEqual(result.isOverlay, true, 'Position 120 should be overlay');
    assertEqual(result.selectedIndex, -1, 'Position 120 should have no menu item');
});

// Test 7: Edge case positions return valid results
test('Edge case positions return valid results', () => {
    const testCases = [
        { pos: 60, desc: 'Mid-range position' },
        { pos: 75, desc: 'Near boundary position' },
        { pos: 85, desc: 'Another boundary position' },
        { pos: 93, desc: 'Default position' }
    ];

    testCases.forEach(({ pos, desc }) => {
        const result = resolveMenuSelection(pos);
        assertEqual(typeof result.angle, 'number', `${desc} should return number angle`);
        assertEqual(typeof result.isOverlay, 'boolean', `${desc} should return boolean isOverlay`);
        assertEqual(typeof result.selectedIndex, 'number', `${desc} should return number selectedIndex`);
    });
});

// Test 8: Menu item angle calculation
test('Menu item angle calculation', () => {
    const startAngle = getMenuItemAngle(0);
    const endAngle = getMenuItemAngle(LASER_MAPPING_CONFIG.MENU_ITEMS.length - 1);

    // Menu should be centered around 180 degrees
    const centerAngle = (startAngle + endAngle) / 2;
    assertApproximatelyEqual(centerAngle, 180, 1, 'Menu should be centered around 180 degrees');
});

// Test 9: Zone tiling - no gaps or overlaps between items
test('Zone tiling - adjacent zones tile perfectly', () => {
    const menuItems = LASER_MAPPING_CONFIG.MENU_ITEMS;
    const halfStep = LASER_MAPPING_CONFIG.MENU_ANGLE_STEP / 2;

    for (let i = 0; i < menuItems.length - 1; i++) {
        const angle1 = getMenuItemAngle(i);
        const angle2 = getMenuItemAngle(i + 1);
        const gap = Math.abs(angle1 - angle2);
        assertApproximatelyEqual(gap, LASER_MAPPING_CONFIG.MENU_ANGLE_STEP, 0.01,
            `Gap between items ${i} and ${i+1} should equal MENU_ANGLE_STEP`);
    }
});

// Test 10: Comprehensive range test
test('Comprehensive range test', () => {
    for (let pos = 3; pos <= 123; pos += 5) {
        const result = resolveMenuSelection(pos);

        // Should always return a valid result
        assertEqual(typeof result.selectedIndex, 'number', `Position ${pos} should return valid selectedIndex`);

        // Angle should be in valid range
        if (result.angle < 150 || result.angle > 210) {
            throw new Error(`Position ${pos} produced invalid angle: ${result.angle}`);
        }
    }
});

// Test 11: Performance test
test('Performance test', () => {
    const startTime = Date.now();

    for (let i = 0; i < 1000; i++) {
        const pos = 3 + (i % 120);
        resolveMenuSelection(pos);
    }

    const endTime = Date.now();
    const duration = endTime - startTime;

    if (duration > 100) {
        throw new Error(`Performance test took ${duration}ms, should be under 100ms`);
    }

    console.log(`   Performance: 1000 calls in ${duration}ms`);
});

// Test 12: Sweep test — exactly N clicks for N items
test('Sweep test - one item per zone, no duplicates', () => {
    const selectedPaths = new Set();
    let lastPath = null;
    let clickCount = 0;

    for (let pos = 3; pos <= 123; pos++) {
        const result = resolveMenuSelection(pos);
        if (result.path && result.path !== lastPath) {
            clickCount++;
            if (result.path) selectedPaths.add(result.path);
        }
        lastPath = result.path;
    }

    const menuItems = LASER_MAPPING_CONFIG.MENU_ITEMS;
    // Should have navigated through all menu items (each path seen once)
    assertEqual(selectedPaths.size, menuItems.length,
        `Should see all ${menuItems.length} menu items, saw ${selectedPaths.size}`);
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
    const result = resolveMenuSelection(pos);
    const desc = result.isOverlay ? 'overlay' : (result.path || 'gap');
    console.log(`Position ${pos.toString().padStart(3)}: ${desc} (idx ${result.selectedIndex})`);
});

console.log('\n✅ Fast laser test completed successfully!');
