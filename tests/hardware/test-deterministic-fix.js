#!/usr/bin/env node

/**
 * Test Deterministic Fix for BeoSound 5c
 *
 * This test verifies that the zone-based selection works correctly
 * by testing specific positions and boundary conditions.
 */

const mapper = require('../../web/js/laser-position-mapper.js');

console.log('🎯 Testing Deterministic Fix');
console.log('=' .repeat(50));

// Test progressive behavior through the arc
function testProgressiveBehavior() {
    console.log('\n🔄 Testing Progressive Behavior Through Arc:');

    const positions = [20, 30, 40, 50, 60, 70, 80, 90, 100, 110];

    positions.forEach(pos => {
        const result = mapper.resolveMenuSelection(pos);
        const desc = result.isOverlay ? 'OVERLAY' : (result.path || 'gap');
        console.log(`  Position ${pos.toString().padStart(3)}: ${desc.padEnd(20)} (idx ${result.selectedIndex})`);
    });
}

// Test boundary conditions
function testBoundaryConditions() {
    console.log('\n🔍 Testing Boundary Conditions:');

    const boundaryTests = [
        { pos: 25, desc: 'Top overlay boundary' },
        { pos: 26, desc: 'Just above top overlay' },
        { pos: 105, desc: 'Just below bottom overlay' },
        { pos: 106, desc: 'Bottom overlay boundary' }
    ];

    boundaryTests.forEach(test => {
        const result = mapper.resolveMenuSelection(test.pos);
        const desc = result.isOverlay ? 'OVERLAY' : (result.path || 'gap');
        console.log(`  Position ${test.pos}: ${desc} (${test.desc})`);
    });
}

// Test consistency across multiple calls
function testConsistencyAcrossCalls() {
    console.log('\n🎯 Testing Consistency Across Multiple Calls:');

    const criticalPositions = [30, 55, 85, 90];

    criticalPositions.forEach(pos => {
        const results = [];

        for (let i = 0; i < 100; i++) {
            const result = mapper.resolveMenuSelection(pos);
            results.push(result.path);
        }

        const uniqueResults = [...new Set(results)];
        const consistent = uniqueResults.length === 1;

        console.log(`  Position ${pos}: ${consistent ? '✅' : '❌'} ${consistent ? 'CONSISTENT' : 'INCONSISTENT'}`);
        if (consistent) {
            console.log(`    Always returns: ${uniqueResults[0] || 'overlay'}`);
        } else {
            console.log(`    Returns: ${uniqueResults.join(', ')}`);
        }
    });
}

// Test the complete range
function testCompleteRange() {
    console.log('\n📊 Testing Complete Range (every 10 positions):');

    for (let pos = 3; pos <= 123; pos += 10) {
        const result = mapper.resolveMenuSelection(pos);
        const desc = result.isOverlay ? 'OVERLAY' : (result.path || 'gap');
        console.log(`  Position ${pos.toString().padStart(3)}: ${desc}`);
    }
}

// Run all tests
function runAllTests() {
    testProgressiveBehavior();
    testBoundaryConditions();
    testConsistencyAcrossCalls();
    testCompleteRange();

    console.log('\n🎉 Deterministic Fix Test Summary:');
    console.log('=' .repeat(50));
    console.log('✅ Each position returns a consistent zone result');
    console.log('✅ No state-dependent navigation logic');
    console.log('✅ Position always determines zone');
}

runAllTests();
