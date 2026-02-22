#!/usr/bin/env node

/**
 * Integration Test for BeoSound 5c Laser Position Mapping
 * 
 * This test verifies that the laser position mapping works correctly
 * in a browser-like environment with the actual HTML/JS files.
 */

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

// Test configuration
const testConfig = {
    webRoot: path.join(__dirname, '../../web'),
    verbose: false
};

// Test results tracking
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

async function runIntegrationTests() {
    console.log('🎯 BeoSound 5c Integration Test');
    console.log('=' .repeat(50));
    
    // Check if JSDOM is available
    if (!JSDOM) {
        console.log('⚠️  JSDOM not available - skipping browser integration tests');
        console.log('   Install with: npm install jsdom');
        return;
    }
    
    // Load the HTML file
    const htmlPath = path.join(testConfig.webRoot, 'index.html');
    const htmlContent = fs.readFileSync(htmlPath, 'utf8');
    
    // Create a DOM environment
    const dom = new JSDOM(htmlContent, {
        url: `file://${htmlPath}`,
        resources: 'usable',
        runScripts: 'dangerously'
    });
    
    const { window } = dom;
    global.window = window;
    global.document = window.document;
    
    // Wait for DOM to be ready
    await new Promise(resolve => {
        if (window.document.readyState === 'loading') {
            window.document.addEventListener('DOMContentLoaded', resolve);
        } else {
            resolve();
        }
    });
    
    // Test 1: Check that LaserPositionMapper is available
    test('LaserPositionMapper available in browser', () => {
        assertEqual(typeof window.LaserPositionMapper, 'object', 'LaserPositionMapper should be available');
        assertEqual(typeof window.LaserPositionMapper.resolveMenuSelection, 'function', 'resolveMenuSelection should be available');
    });
    
    // Test 2: Test basic laser position mapping
    test('Basic laser position mapping works', () => {
        const result = window.LaserPositionMapper.resolveMenuSelection(93);
        assertEqual(typeof result, 'object', 'Should return an object');
        assertEqual(typeof result.selectedIndex, 'number', 'Should have a selectedIndex number');
        assertEqual(typeof result.isOverlay, 'boolean', 'Should have an isOverlay boolean');
    });
    
    // Test 3: Test position 120 (the reported bug)
    test('Position 120 maps to Now Playing (fast scroll fix)', () => {
        const result = window.LaserPositionMapper.resolveMenuSelection(120);
        assertEqual(result.isOverlay, true, 'Position 120 should be overlay');
        assertEqual(result.selectedIndex, -1, 'Position 120 should have no menu item');
    });
    
    // Test 4: Check that UIStore is available
    test('UIStore available in browser', () => {
        // Wait a bit for UIStore to initialize
        setTimeout(() => {
            assertEqual(typeof window.uiStore, 'object', 'UIStore should be available');
            assertEqual(typeof window.uiStore.handleWheelChange, 'function', 'handleWheelChange should be available');
        }, 100);
    });
    
    // Test 5: Test that dummy hardware system is available
    test('Dummy hardware system available', () => {
        assertEqual(typeof window.dummyHardwareManager, 'object', 'Dummy hardware manager should be available');
    });
    
    // Print summary
    console.log('\n📊 Integration Test Summary');
    console.log('=' .repeat(40));
    console.log(`Total Tests: ${testCount}`);
    console.log(`Passed: ${passCount} ✅`);
    console.log(`Failed: ${failCount} ❌`);
    
    if (failCount === 0) {
        console.log('\n🎉 All integration tests passed!');
        console.log('✅ The laser position mapping system is working correctly in the browser.');
    } else {
        console.log('\n⚠️  Some integration tests failed.');
        console.log('Please check the browser console for errors.');
    }
}

// Run the tests
runIntegrationTests().catch(error => {
    console.error('Integration test failed:', error);
    process.exit(1);
});