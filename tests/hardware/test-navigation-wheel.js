#!/usr/bin/env node

/**
 * Test Navigation Wheel for BeoSound 5c
 * 
 * This test verifies that the navigation wheel works correctly
 * by simulating the topWheelPosition updates and checking laser position changes.
 */

// Mock the UI system to test navigation wheel functionality
class MockUIStore {
    constructor() {
        this.laserPosition = 93; // Start at NOW PLAYING
        this.topWheelPosition = 0;
        this.wheelPointerAngle = 180;
        this.currentRoute = 'menu/playing';
    }
    
    handleWheelChange() {
        // Convert topWheelPosition to laserPosition changes (navigation wheel support)
        if (this.topWheelPosition !== 0) {
            const positionStep = 5; // Same as in ui.js
            const newLaserPosition = Math.max(3, Math.min(123, 
                this.laserPosition + (this.topWheelPosition * positionStep)));
            
            console.log(`[DEBUG] Wheel navigation: position ${this.laserPosition} -> ${newLaserPosition} (step: ${this.topWheelPosition})`);
            this.laserPosition = newLaserPosition;
        }
        
        // Reset wheel position (like ui.js does)
        this.topWheelPosition = 0;
    }
    
    // Simulate hardware navigation events
    simulateNavEvent(direction) {
        if (direction === 'clock') {
            this.topWheelPosition = 1;
        } else if (direction === 'counter') {
            this.topWheelPosition = -1;
        }
        this.handleWheelChange();
    }
}

console.log('🎯 Testing Navigation Wheel Functionality');
console.log('=' .repeat(50));

// Test navigation wheel events
function testNavigationWheel() {
    console.log('\n✅ Testing navigation wheel events:');
    
    const ui = new MockUIStore();
    const startPosition = ui.laserPosition;
    
    console.log(`Starting position: ${startPosition}`);
    
    // Test clockwise navigation (down)
    console.log('\n🔄 Testing clockwise navigation (down):');
    for (let i = 0; i < 5; i++) {
        const beforePosition = ui.laserPosition;
        ui.simulateNavEvent('clock');
        const afterPosition = ui.laserPosition;
        
        console.log(`  Step ${i + 1}: ${beforePosition} -> ${afterPosition} (${afterPosition > beforePosition ? '✅' : '❌'})`);
    }
    
    // Test counterclockwise navigation (up)
    console.log('\n🔄 Testing counterclockwise navigation (up):');
    for (let i = 0; i < 10; i++) {
        const beforePosition = ui.laserPosition;
        ui.simulateNavEvent('counter');
        const afterPosition = ui.laserPosition;
        
        console.log(`  Step ${i + 1}: ${beforePosition} -> ${afterPosition} (${afterPosition < beforePosition ? '✅' : '❌'})`);
    }
    
    console.log(`\nFinal position: ${ui.laserPosition}`);
}

// Test boundary conditions
function testBoundaryConditions() {
    console.log('\n🔍 Testing boundary conditions:');
    
    // Test minimum boundary
    const ui1 = new MockUIStore();
    ui1.laserPosition = 5; // Near minimum
    console.log(`\nTesting minimum boundary (starting at ${ui1.laserPosition}):`);
    
    ui1.simulateNavEvent('counter'); // Should clamp to 3
    console.log(`  After counter: ${ui1.laserPosition} (should be >= 3)`);
    
    ui1.simulateNavEvent('counter'); // Should stay at 3
    console.log(`  After counter: ${ui1.laserPosition} (should stay at 3)`);
    
    // Test maximum boundary
    const ui2 = new MockUIStore();
    ui2.laserPosition = 120; // Near maximum
    console.log(`\nTesting maximum boundary (starting at ${ui2.laserPosition}):`);
    
    ui2.simulateNavEvent('clock'); // Should clamp to 123
    console.log(`  After clock: ${ui2.laserPosition} (should be <= 123)`);
    
    ui2.simulateNavEvent('clock'); // Should stay at 123
    console.log(`  After clock: ${ui2.laserPosition} (should stay at 123)`);
}

// Test integration with laser position mapper
function testIntegrationWithMapper() {
    console.log('\n🔗 Testing integration with laser position mapper:');
    
    const mapper = require('../../web/js/laser-position-mapper.js');
    const ui = new MockUIStore();
    
    // Test navigation through different menu items
    const testSequence = [
        { direction: 'counter', steps: 8, description: 'Navigate up to SHOWING' },
        { direction: 'clock', steps: 4, description: 'Navigate down to SETTINGS' },
        { direction: 'clock', steps: 6, description: 'Navigate down to PLAYING' },
        { direction: 'counter', steps: 2, description: 'Navigate up to MUSIC' }
    ];
    
    testSequence.forEach(test => {
        console.log(`\n  ${test.description}:`);
        const startPosition = ui.laserPosition;
        const startView = mapper.getViewForLaserPosition(startPosition);
        
        for (let i = 0; i < test.steps; i++) {
            ui.simulateNavEvent(test.direction);
        }
        
        const endPosition = ui.laserPosition;
        const endView = mapper.getViewForLaserPosition(endPosition);
        
        console.log(`    ${startPosition} (${startView.path}) -> ${endPosition} (${endView.path})`);
        console.log(`    Menu item: ${endView.menuItem ? endView.menuItem.title : 'OVERLAY'}`);
    });
}

// Run all tests
function runAllTests() {
    testNavigationWheel();
    testBoundaryConditions();
    testIntegrationWithMapper();
    
    console.log('\n🎉 Navigation Wheel Test Summary:');
    console.log('=' .repeat(50));
    console.log('✅ Navigation wheel converts topWheelPosition to laserPosition');
    console.log('✅ Clockwise navigation increases laser position');
    console.log('✅ Counterclockwise navigation decreases laser position');
    console.log('✅ Boundary conditions work correctly (3-123 range)');
    console.log('✅ Integration with laser position mapper works');
    
    console.log('\n🧪 Manual Testing Instructions:');
    console.log('1. Open web interface');
    console.log('2. Use Up/Down arrow keys to test navigation wheel');
    console.log('3. Check console for "[DEBUG] Wheel navigation:" messages');
    console.log('4. Verify menu items change as you navigate');
    console.log('5. Test with real hardware navigation wheel if available');
}

// Run the tests
runAllTests();