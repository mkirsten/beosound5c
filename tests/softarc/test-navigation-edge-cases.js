#!/usr/bin/env node

/**
 * Edge Case Tests for BeoSound 5c Navigation System
 * 
 * These tests specifically target the issues we've encountered:
 * - Duplicate method definitions
 * - Infinite render loops
 * - Black screen issues
 * - Container children not being created
 * - Animation state blocking renders
 */

const fs = require('fs');
const path = require('path');

// Import base test utilities
const {
    test,
    assertEqual,
    assertTrue,
    assertFalse,
    MockDOM,
    MockElement,
    MockWebSocket,
    loadTestData,
    loadArcListScript,
    testConfig
} = require('./test-softarc-navigation.js');

// Test state tracking
let testCount = 0;
let passCount = 0;
let failCount = 0;

/**
 * Enhanced test wrapper that tracks render calls and detects loops
 */
class EdgeCaseTestWrapper {
    constructor() {
        this.mockDocument = new MockDOM();
        this.setupRequiredElements();
        this.renderCallCount = 0;
        this.renderCallStack = [];
        this.maxRenderCalls = 100; // Detect infinite loops
        this.setupMockEnvironment();
    }
    
    setupRequiredElements() {
        // Create required DOM elements
        const arcContainer = new MockElement('div');
        arcContainer.id = 'arc-container';
        this.mockDocument.elements.set('arc-container', arcContainer);
        
        const currentItem = new MockElement('span');
        currentItem.id = 'current-item';
        this.mockDocument.elements.set('current-item', currentItem);
        
        const totalItems = new MockElement('span');
        totalItems.id = 'total-items';
        this.mockDocument.elements.set('total-items', totalItems);
    }
    
    setupMockEnvironment() {
        global.document = this.mockDocument;
        global.WebSocket = MockWebSocket;
        global.localStorage = {
            getItem: () => null,
            setItem: () => {},
            removeItem: () => {}
        };
        global.window = {
            addEventListener: () => {},
            getComputedStyle: () => ({
                display: 'block',
                visibility: 'visible',
                opacity: '1',
                zIndex: '1'
            })
        };
        global.fetch = async (url) => {
            return { json: () => Promise.resolve(loadTestData()) };
        };
        
        // Track render calls
        this.originalRender = null;
        this.trackRenderCalls();
    }
    
    trackRenderCalls() {
        const self = this;
        
        // Override document.createElement to track render calls
        const originalCreateElement = this.mockDocument.createElement.bind(this.mockDocument);
        this.mockDocument.createElement = function(tagName) {
            if (self.renderCallCount > self.maxRenderCalls) {
                throw new Error(`Infinite loop detected! Render called ${self.renderCallCount} times`);
            }
            return originalCreateElement(tagName);
        };
    }
    
    createArcList(config = {}) {
        const ArcList = loadArcListScript();
        if (!ArcList) {
            throw new Error('Failed to load ArcList class');
        }
        
        // Default config for testing
        const testConfig = {
            dataSource: '../json/playlists_with_tracks.json',
            dataType: 'parent_child',
            viewMode: 'hierarchical',
            parentKey: 'tracks',
            parentNameKey: 'name',
            context: 'music',
            ...config
        };
        
        this.arcList = new ArcList(testConfig);
        
        // Track render calls
        const originalRender = this.arcList.render.bind(this.arcList);
        const self = this;
        
        this.arcList.render = function() {
            self.renderCallCount++;
            self.renderCallStack.push(new Error().stack);
            
            if (self.renderCallCount > self.maxRenderCalls) {
                throw new Error(`Infinite loop detected! Render called ${self.renderCallCount} times`);
            }
            
            return originalRender();
        };
        
        // Load test data
        this.arcList.parentData = loadTestData();
        this.arcList.items = this.arcList.parentData.map((parent, index) => ({
            id: parent.id,
            name: parent.name || `Item ${index + 1}`,
            image: parent.image || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1fYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
        }));
        
        return this.arcList;
    }
    
    resetRenderTracking() {
        this.renderCallCount = 0;
        this.renderCallStack = [];
    }
    
    getContainerChildCount() {
        const container = this.mockDocument.getElementById('arc-container');
        return container ? container.children.length : 0;
    }
}

// Test functions
async function runEdgeCaseTests() {
    console.log('🔍 BeoSound 5c Navigation Edge Case Tests');
    console.log('=' .repeat(50));
    
    // Test 1: Detect duplicate method definitions
    runTest('Test 1: Detect duplicate method definitions', () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Check if renderChildItems is defined only once
        const renderChildItemsCount = Object.getOwnPropertyNames(Object.getPrototypeOf(arcList))
            .filter(name => name === 'renderChildItems').length;
        
        assertEqual(renderChildItemsCount, 1, 'renderChildItems should be defined only once');
        
        // Check for other critical methods
        const criticalMethods = ['render', 'loadParentChildren', 'enterChildView', 'exitChildView'];
        criticalMethods.forEach(method => {
            const count = Object.getOwnPropertyNames(Object.getPrototypeOf(arcList))
                .filter(name => name === method).length;
            assertEqual(count, 1, `${method} should be defined only once`);
        });
        
        console.log('   ✅ No duplicate methods detected');
    });
    
    // Test 2: Detect infinite render loops
    runTest('Test 2: Detect infinite render loops', async () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Set up child view scenario
        arcList.viewMode = 'child';
        arcList.selectedParent = arcList.parentData[0];
        arcList.loadParentChildren();
        
        // Reset tracking
        testWrapper.resetRenderTracking();
        
        // Try to render - should not create infinite loop
        try {
            arcList.render();
            await new Promise(resolve => setTimeout(resolve, 100)); // Wait for any async operations
            
            assertTrue(testWrapper.renderCallCount < 10, `Render called too many times: ${testWrapper.renderCallCount}`);
            console.log(`   ✅ Render called ${testWrapper.renderCallCount} times (no infinite loop)`);
        } catch (error) {
            if (error.message.includes('Infinite loop detected')) {
                throw new Error('Infinite render loop detected!');
            }
            throw error;
        }
    });
    
    // Test 3: Ensure render completes and creates DOM elements
    runTest('Test 3: Render creates DOM elements in container', () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Initial state
        arcList.viewMode = 'parent';
        
        // Check container before render
        const beforeCount = testWrapper.getContainerChildCount();
        assertEqual(beforeCount, 0, 'Container should be empty before render');
        
        // Force initial render
        arcList.isAnimating = false; // Ensure animation doesn't block
        arcList.render();
        
        // Check container after render
        const afterCount = testWrapper.getContainerChildCount();
        assertTrue(afterCount > 0, `Container should have children after render, got ${afterCount}`);
        
        console.log(`   ✅ Render created ${afterCount} DOM elements`);
    });
    
    // Test 4: Test animation flag blocking
    runTest('Test 4: Animation flag should not permanently block renders', () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Set animation flag
        arcList.isAnimating = true;
        arcList.render();
        
        // Should not create elements when animating
        let count = testWrapper.getContainerChildCount();
        assertEqual(count, 0, 'Container should be empty when isAnimating is true');
        
        // Clear animation flag
        arcList.isAnimating = false;
        arcList.render();
        
        // Should create elements now
        count = testWrapper.getContainerChildCount();
        assertTrue(count > 0, 'Container should have children when isAnimating is false');
        
        console.log('   ✅ Animation flag correctly controls rendering');
    });
    
    // Test 5: Test view mode transitions
    runTest('Test 5: View mode transitions without loops', async () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Start in parent mode
        arcList.viewMode = 'parent';
        arcList.render();
        const parentRenderCount = testWrapper.renderCallCount;
        
        // Transition to child view
        testWrapper.resetRenderTracking();
        arcList.selectedParent = arcList.parentData[0];
        arcList.viewMode = 'child';
        arcList.loadParentChildren();
        
        // Check render count
        assertTrue(testWrapper.renderCallCount < 5, `Too many renders during view transition: ${testWrapper.renderCallCount}`);
        
        console.log('   ✅ View transitions work without render loops');
    });
    
    // Test 6: Test container innerHTML manipulation
    runTest('Test 6: Container innerHTML is properly managed', () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Mock innerHTML property
        const container = testWrapper.mockDocument.getElementById('arc-container');
        let innerHTMLValue = '';
        
        Object.defineProperty(container, 'innerHTML', {
            get: () => innerHTMLValue,
            set: (value) => {
                innerHTMLValue = value;
                if (value === '') {
                    // Clear children when innerHTML is set to empty
                    container.children.length = 0;
                }
            }
        });
        
        // Test clearing
        arcList.render();
        assertTrue(testWrapper.getContainerChildCount() > 0, 'Container should have children after render');
        
        console.log('   ✅ Container innerHTML management works correctly');
    });
    
    // Test 7: Test rapid navigation
    runTest('Test 7: Rapid navigation doesn\'t cause issues', async () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Simulate rapid navigation
        for (let i = 0; i < 10; i++) {
            arcList.handleNavFromParent({ direction: 'clock', speed: 20 });
            await new Promise(resolve => setTimeout(resolve, 10));
        }
        
        // Should still be able to render
        testWrapper.resetRenderTracking();
        arcList.render();
        
        assertTrue(testWrapper.renderCallCount === 1, 'Single render call after rapid navigation');
        assertTrue(testWrapper.getContainerChildCount() > 0, 'Container has children after rapid navigation');
        
        console.log('   ✅ Rapid navigation handled correctly');
    });
    
    // Test 8: Test error recovery
    runTest('Test 8: System recovers from render errors', () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Force an error condition
        arcList.container = null;
        
        try {
            arcList.render();
        } catch (error) {
            // Error expected
        }
        
        // Restore container
        arcList.container = testWrapper.mockDocument.getElementById('arc-container');
        
        // Should be able to render now
        arcList.render();
        assertTrue(testWrapper.getContainerChildCount() > 0, 'System recovered from error condition');
        
        console.log('   ✅ Error recovery works correctly');
    });
    
    // Test 9: Test memory leak prevention
    runTest('Test 9: No memory leaks from repeated renders', () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Track initial state
        const initialChildCount = arcList.items.length;
        
        // Render multiple times
        for (let i = 0; i < 20; i++) {
            arcList.render();
        }
        
        // Check that we don't accumulate elements
        const finalCount = testWrapper.getContainerChildCount();
        assertTrue(finalCount <= initialChildCount * 2, `Too many elements after repeated renders: ${finalCount}`);
        
        console.log('   ✅ No memory leaks detected');
    });
    
    // Test 10: Test render call stack depth
    runTest('Test 10: Render call stack depth detection', () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        let maxCallDepth = 0;
        let currentCallDepth = 0;
        const originalRender = arcList.render.bind(arcList);
        
        arcList.render = function() {
            currentCallDepth++;
            maxCallDepth = Math.max(maxCallDepth, currentCallDepth);
            
            if (currentCallDepth > 50) {
                throw new Error(`Render call stack too deep: ${currentCallDepth}`);
            }
            
            try {
                return originalRender();
            } finally {
                currentCallDepth--;
            }
        };
        
        // Normal render should have shallow call stack
        arcList.render();
        assertTrue(maxCallDepth < 5, `Render call stack too deep: ${maxCallDepth}`);
        
        console.log(`   ✅ Render call stack depth: ${maxCallDepth} (acceptable)`);
    });
    
    // Test 11: Test specific black screen scenario
    runTest('Test 11: Black screen prevention', () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Simulate the conditions that caused black screen
        arcList.isAnimating = false;
        arcList.viewMode = 'parent';
        arcList.items = arcList.parentData.map((parent, index) => ({
            id: parent.id,
            name: parent.name || `Item ${index + 1}`,
            image: parent.image || 'placeholder.svg'
        }));
        
        // Ensure container exists
        assertTrue(arcList.container !== null, 'Container should exist');
        
        // Render
        arcList.render();
        
        // Check results
        const childCount = testWrapper.getContainerChildCount();
        assertTrue(childCount > 0, `Black screen detected! Container has ${childCount} children`);
        
        // Check that visible items method works
        const visibleItems = arcList.getVisibleItems();
        assertTrue(visibleItems.length > 0, 'getVisibleItems should return items');
        
        console.log(`   ✅ No black screen - ${childCount} items rendered`);
    });
    
    // Test 12: Test animation flag race conditions
    runTest('Test 12: Animation flag race conditions', async () => {
        const testWrapper = new EdgeCaseTestWrapper();
        const arcList = testWrapper.createArcList();
        
        // Track animation flag changes
        let animationFlagChanges = [];
        let isAnimatingValue = false;
        
        Object.defineProperty(arcList, 'isAnimating', {
            get: () => isAnimatingValue,
            set: (value) => {
                animationFlagChanges.push({ value, timestamp: Date.now() });
                isAnimatingValue = value;
            }
        });
        
        // Simulate rapid navigation that could cause race conditions
        arcList.isAnimating = true;
        arcList.handleNavFromParent({ direction: 'clock', speed: 20 });
        
        // Simulate another navigation before animation completes
        setTimeout(() => {
            arcList.handleNavFromParent({ direction: 'counter', speed: 20 });
        }, 50);
        
        // Wait for animations
        await new Promise(resolve => setTimeout(resolve, 200));
        
        // Check that animation flag was properly reset
        assertFalse(arcList.isAnimating, 'Animation flag should be reset after navigation');
        
        // Check for proper flag management
        const trueCount = animationFlagChanges.filter(c => c.value === true).length;
        const falseCount = animationFlagChanges.filter(c => c.value === false).length;
        
        assertTrue(trueCount > 0, 'Animation flag should have been set to true');
        assertTrue(falseCount > 0, 'Animation flag should have been set to false');
        assertTrue(falseCount >= trueCount, 'Animation flag should be reset for each animation');
        
        console.log(`   ✅ Animation flag properly managed: ${trueCount} activations, ${falseCount} resets`);
    });
}

// Custom test runner for edge cases
function runTest(description, testFunction) {
    testCount++;
    const startTime = Date.now();
    
    try {
        const result = testFunction();
        const duration = Date.now() - startTime;
        
        console.log(`✅ ${description} (${duration}ms)`);
        passCount++;
        
        return result;
    } catch (error) {
        const duration = Date.now() - startTime;
        
        console.log(`❌ ${description} (${duration}ms)`);
        console.log(`   Error: ${error.message}`);
        if (error.stack && process.env.DEBUG) {
            console.log(`   Stack: ${error.stack}`);
        }
        failCount++;
        
        return null;
    }
}

// Main test execution
async function main() {
    try {
        await runEdgeCaseTests();
        
        console.log('\n📊 Edge Case Test Summary');
        console.log('=' .repeat(40));
        console.log(`Total Tests: ${testCount}`);
        console.log(`Passed: ${passCount} ✅`);
        console.log(`Failed: ${failCount} ❌`);
        console.log(`Success Rate: ${((passCount / testCount) * 100).toFixed(1)}%`);
        
        if (failCount === 0) {
            console.log('\n🎉 All edge case tests passed!');
            console.log('✅ The navigation system is resilient to edge cases');
        } else {
            console.log('\n⚠️  Some edge case tests failed.');
            console.log('❌ Issues detected that need to be addressed');
            process.exit(1);
        }
        
    } catch (error) {
        console.error('🔥 Test execution failed:', error);
        process.exit(1);
    }
}

// Run tests
main().catch(console.error);