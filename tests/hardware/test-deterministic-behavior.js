#!/usr/bin/env node

/**
 * Test Deterministic Behavior for BeoSound 5c
 *
 * This test verifies that a laser position ALWAYS resolves to the same zone,
 * regardless of previous state or navigation path.
 */

const mapper = require('../../web/js/laser-position-mapper.js');

console.log('🎯 Testing Deterministic Behavior');
console.log('=' .repeat(50));

// Test that the same position always returns the same result
function testDeterministicMapping() {
    console.log('\n✅ Testing pure mapping function determinism:');

    const testPositions = [30, 45, 60, 75, 90, 105, 120];
    let allDeterministic = true;

    testPositions.forEach(pos => {
        const results = [];

        for (let i = 0; i < 10; i++) {
            const result = mapper.resolveMenuSelection(pos);
            results.push(result.path);
        }

        const firstResult = results[0];
        const allSame = results.every(r => r === firstResult);

        if (allSame) {
            console.log(`  ✅ Position ${pos}: Always returns ${firstResult || 'overlay (null)'}`);
        } else {
            console.log(`  ❌ Position ${pos}: Returns different results: ${[...new Set(results)].join(', ')}`);
            allDeterministic = false;
        }
    });

    return allDeterministic;
}

// Test different navigation sequences to the same position
function testSequenceDeterminism() {
    console.log('\n🧪 Testing sequence-independent behavior:');

    const testCases = [
        {
            name: 'Forward then back to same position',
            sequence: [60, 70, 80, 90, 85],
            finalPosition: 85
        },
        {
            name: 'Reverse path to same position',
            sequence: [90, 85, 80, 70, 85],
            finalPosition: 85
        },
        {
            name: 'Long sweep then return',
            sequence: [120, 100, 80, 60, 40, 30],
            finalPosition: 30
        },
        {
            name: 'Bottom to top and back',
            sequence: [30, 50, 70, 90, 110, 30],
            finalPosition: 30
        }
    ];

    testCases.forEach(testCase => {
        const results = [];

        for (let run = 0; run < 5; run++) {
            testCase.sequence.forEach(pos => {
                mapper.resolveMenuSelection(pos);
            });
            const finalResult = mapper.resolveMenuSelection(testCase.finalPosition);
            results.push(finalResult.path);
        }

        const allSame = results.every(r => r === results[0]);

        if (allSame) {
            console.log(`  ✅ ${testCase.name}: Always returns ${results[0] || 'overlay'}`);
        } else {
            console.log(`  ❌ ${testCase.name}: Got ${[...new Set(results)].join(', ')}`);
        }
    });
}

// Test boundary conditions
function testBoundaryConditions() {
    console.log('\n🔍 Testing boundary conditions:');

    const boundaryTests = [
        { pos: 25, desc: 'Top overlay boundary' },
        { pos: 26, desc: 'Just above top overlay' },
        { pos: 105, desc: 'Just below bottom overlay' },
        { pos: 106, desc: 'Bottom overlay boundary' },
        { pos: 160, desc: 'Invalid position (should clamp)' }
    ];

    boundaryTests.forEach(test => {
        const results = [];

        for (let i = 0; i < 5; i++) {
            const result = mapper.resolveMenuSelection(test.pos);
            results.push(result.path);
        }

        const allSame = results.every(r => r === results[0]);

        if (allSame) {
            console.log(`  ✅ ${test.desc}: Always returns ${results[0] || 'overlay'}`);
        } else {
            console.log(`  ❌ ${test.desc}: Got ${[...new Set(results)].join(', ')}`);
        }
    });
}

// Run all tests
function runAllTests() {
    const mapperDeterministic = testDeterministicMapping();
    testSequenceDeterminism();
    testBoundaryConditions();

    console.log('\n📊 Deterministic Behavior Test Summary:');
    console.log('=' .repeat(50));

    if (mapperDeterministic) {
        console.log('✅ Pure mapping function is deterministic');
    } else {
        console.log('❌ Pure mapping function has non-deterministic behavior');
    }

    console.log('\n💡 With resolveMenuSelection, the mapping is purely positional — no state.');
}

runAllTests();
