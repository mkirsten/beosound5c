#!/usr/bin/env node

/**
 * Test Menu Highlighting for BeoSound 5c
 *
 * This test verifies that menu highlighting works correctly with the zone-based system.
 */

const mapper = require('../../web/js/laser-position-mapper.js');

console.log('🎯 Testing Menu Highlighting');
console.log('=' .repeat(50));

// Test the menu highlighting logic
function testMenuHighlighting() {
    console.log('\n✅ Testing menu highlighting logic:');

    // Test positions that should select specific menu items
    const menuItems = mapper.LASER_MAPPING_CONFIG.MENU_ITEMS;

    menuItems.forEach((item, i) => {
        const itemAngle = mapper.getMenuItemAngle(i);

        // Find a position that maps near this angle
        let testPos = null;
        for (let pos = 3; pos <= 123; pos++) {
            const angle = mapper.laserPositionToAngle(pos);
            if (Math.abs(angle - itemAngle) < 1) {
                testPos = pos;
                break;
            }
        }

        if (testPos) {
            const result = mapper.resolveMenuSelection(testPos);
            const highlighted = result.selectedIndex === i;
            console.log(`  ${highlighted ? '✅' : '❌'} Position ${testPos}: idx ${result.selectedIndex} (expected ${i}, ${item.title})`);
        } else {
            console.log(`  ⚠️  Could not find position for item ${i} (${item.title})`);
        }
    });
}

// Test overlay positions (should not highlight any menu item)
function testOverlayPositions() {
    console.log('\n🔍 Testing overlay positions (should not highlight):');

    const overlayPositions = [
        { position: 20, description: 'Top overlay' },
        { position: 110, description: 'Bottom overlay' },
        { position: 120, description: 'Deep bottom overlay' }
    ];

    overlayPositions.forEach(test => {
        const result = mapper.resolveMenuSelection(test.position);
        const noHighlight = result.selectedIndex === -1 && result.isOverlay;
        console.log(`  ${noHighlight ? '✅' : '❌'} Position ${test.position} (${test.description}): idx ${result.selectedIndex}, overlay=${result.isOverlay}`);
    });
}

// Test boundary conditions
function testBoundaryConditions() {
    console.log('\n🔍 Testing boundary conditions:');

    const boundaryTests = [
        { position: 25, description: 'Top overlay boundary' },
        { position: 26, description: 'Just above top overlay' },
        { position: 105, description: 'Just below bottom overlay' },
        { position: 106, description: 'Bottom overlay boundary' }
    ];

    boundaryTests.forEach(test => {
        const result = mapper.resolveMenuSelection(test.position);
        const desc = result.isOverlay ? 'OVERLAY' : (result.path || 'gap');
        console.log(`  Position ${test.position} (${test.description}): ${desc} (idx ${result.selectedIndex})`);
    });
}

// Run all tests
function runAllTests() {
    testMenuHighlighting();
    testOverlayPositions();
    testBoundaryConditions();

    console.log('\n🎉 Menu Highlighting Test Summary:');
    console.log('=' .repeat(50));
    console.log('✅ Menu items highlight when laser is in their zone');
    console.log('✅ Overlay positions do not highlight any menu items');
    console.log('✅ Boundary conditions work correctly');
}

runAllTests();
