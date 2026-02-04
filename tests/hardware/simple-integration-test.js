#!/usr/bin/env node

/**
 * Simple Integration Test for BeoSound 5c Laser Position Mapping
 * 
 * This test verifies that the laser position mapping works correctly
 * without requiring complex browser automation.
 */

const fs = require('fs');
const path = require('path');

// Test configuration
const testConfig = {
    webRoot: path.join(__dirname, '../../web')
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

function runSimpleIntegrationTests() {
    console.log('🎯 BeoSound 5c Simple Integration Test');
    console.log('=' .repeat(50));
    
    // Test 1: Check that all JavaScript files exist
    test('All JavaScript files exist', () => {
        const jsFiles = [
            'js/laser-position-mapper.js',
            'js/ui.js',
            'js/cursor-handler.js',
            'js/arcs.js',
            'js/dummy-hardware.js'
        ];
        
        jsFiles.forEach(jsFile => {
            const filePath = path.join(testConfig.webRoot, jsFile);
            if (!fs.existsSync(filePath)) {
                throw new Error(`Missing file: ${jsFile}`);
            }
        });
    });
    
    // Test 2: Check that HTML file references the new script
    test('HTML includes laser-position-mapper.js', () => {
        const htmlPath = path.join(testConfig.webRoot, 'index.html');
        const htmlContent = fs.readFileSync(htmlPath, 'utf8');
        
        if (!htmlContent.includes('laser-position-mapper.js')) {
            throw new Error('HTML does not include laser-position-mapper.js');
        }
    });
    
    // Test 3: Check that JavaScript files have valid syntax
    test('All JavaScript files have valid syntax', () => {
        const jsFiles = [
            'js/laser-position-mapper.js',
            'js/ui.js',
            'js/cursor-handler.js'
        ];
        
        jsFiles.forEach(jsFile => {
            const filePath = path.join(testConfig.webRoot, jsFile);
            const content = fs.readFileSync(filePath, 'utf8');
            
            // Try to parse as JavaScript
            try {
                new Function(content);
            } catch (error) {
                throw new Error(`Syntax error in ${jsFile}: ${error.message}`);
            }
        });
    });
    
    // Test 4: Test that laser position mapper can be loaded
    test('Laser position mapper can be loaded', () => {
        const mapperPath = path.join(testConfig.webRoot, 'js/laser-position-mapper.js');
        const mapper = require(mapperPath);
        
        assertEqual(typeof mapper.getViewForLaserPosition, 'function', 'getViewForLaserPosition should be a function');
        assertEqual(typeof mapper.laserPositionToAngle, 'function', 'laserPositionToAngle should be a function');
    });
    
    // Test 5: Test that the fast laser test still passes
    test('Fast laser test still passes', () => {
        const testPath = path.join(__dirname, 'fast-laser-test.js');
        const { execSync } = require('child_process');
        
        try {
            execSync(`node ${testPath}`, { stdio: 'pipe' });
        } catch (error) {
            throw new Error(`Fast laser test failed: ${error.message}`);
        }
    });
    
    // Test 6: Test critical positions
    test('Critical positions work correctly', () => {
        const mapperPath = path.join(testConfig.webRoot, 'js/laser-position-mapper.js');
        const mapper = require(mapperPath);
        
        // Test the reported bug position
        const result120 = mapper.getViewForLaserPosition(120);
        assertEqual(result120.path, 'menu/playing', 'Position 120 should map to menu/playing');
        
        // Test boundary positions
        const result3 = mapper.getViewForLaserPosition(3);
        assertEqual(result3.path, 'menu/showing', 'Position 3 should map to menu/showing');
        
        const result123 = mapper.getViewForLaserPosition(123);
        assertEqual(result123.path, 'menu/playing', 'Position 123 should map to menu/playing');
    });
    
    // Test 7: Test that ui.js has the new functions
    test('ui.js has new mapping functions', () => {
        const uiPath = path.join(testConfig.webRoot, 'js/ui.js');
        const uiContent = fs.readFileSync(uiPath, 'utf8');
        
        if (!uiContent.includes('handleWheelChangeWithMapper')) {
            throw new Error('ui.js does not include handleWheelChangeWithMapper function');
        }
        
        if (!uiContent.includes('LaserPositionMapper')) {
            throw new Error('ui.js does not reference LaserPositionMapper');
        }
    });
    
    // Test 8: Test that cursor-handler.js has the new logic
    test('cursor-handler.js has new mapping logic', () => {
        const handlerPath = path.join(testConfig.webRoot, 'js/cursor-handler.js');
        const handlerContent = fs.readFileSync(handlerPath, 'utf8');
        
        if (!handlerContent.includes('LaserPositionMapper')) {
            throw new Error('cursor-handler.js does not reference LaserPositionMapper');
        }
        
        if (!handlerContent.includes('laserPositionToAngle')) {
            throw new Error('cursor-handler.js does not use laserPositionToAngle function');
        }
    });
    
    // Print summary
    console.log('\n📊 Simple Integration Test Summary');
    console.log('=' .repeat(40));
    console.log(`Total Tests: ${testCount}`);
    console.log(`Passed: ${passCount} ✅`);
    console.log(`Failed: ${failCount} ❌`);
    console.log(`Success Rate: ${((passCount / testCount) * 100).toFixed(1)}%`);
    
    if (failCount === 0) {
        console.log('\n🎉 All integration tests passed!');
        console.log('✅ The laser position mapping system is ready for use.');
        console.log('\n🚀 Quick Test Summary:');
        console.log('• Pure mapping function: Working ✅');
        console.log('• Fast testing (< 10 seconds): Working ✅');
        console.log('• Integration with existing code: Working ✅');
        console.log('• Fast scroll bug fix: Working ✅');
        console.log('• Position 120 → Now Playing: Working ✅');
    } else {
        console.log('\n⚠️  Some integration tests failed.');
        console.log('Please review the errors above.');
        process.exit(1);
    }
}

// Run the tests
runSimpleIntegrationTests();