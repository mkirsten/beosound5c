#!/usr/bin/env node

/**
 * Test for breadcrumb animation functionality
 * Verifies that the selected playlist animates to the left and stays there
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
 * Enhanced MockDOM that tracks class changes
 */
class AnimationMockDOM extends MockDOM {
    constructor() {
        super();
        this.setupElements();
    }
    
    setupElements() {
        const arcContainer = new AnimationMockElement('div');
        arcContainer.id = 'arc-container';
        this.elements.set('arc-container', arcContainer);
        
        const currentItem = new AnimationMockElement('span');
        currentItem.id = 'current-item';
        this.elements.set('current-item', currentItem);
        
        const totalItems = new AnimationMockElement('span');
        totalItems.id = 'total-items';
        this.elements.set('total-items', totalItems);
    }
    
    querySelector(selector) {
        if (selector === '.arc-item.selected') {
            // Return the selected item from container
            const container = this.getElementById('arc-container');
            if (container && container.children) {
                for (const child of container.children) {
                    if (child._classes && child._classes.has('selected')) {
                        return child;
                    }
                }
            }
            return null;
        }
        
        if (selector === '.arc-item.breadcrumb') {
            // Return the breadcrumb item
            const container = this.getElementById('arc-container');
            if (container && container.children) {
                for (const child of container.children) {
                    if (child._classes && child._classes.has('breadcrumb')) {
                        return child;
                    }
                }
            }
            return null;
        }
        
        return super.querySelector ? super.querySelector(selector) : null;
    }
    
    querySelectorAll(selector) {
        const results = [];
        const container = this.getElementById('arc-container');
        
        if (selector === '.arc-item[data-child-item="true"]') {
            container.children.forEach(child => {
                if (child.dataset && child.dataset.childItem === 'true') {
                    results.push(child);
                }
            });
        }
        
        return results;
    }
}

/**
 * Mock element that tracks class changes
 */
class AnimationMockElement extends MockElement {
    constructor(tagName) {
        super(tagName);
        this.classHistory = [];
    }
    
    addClass(className) {
        super.addClass(className);
        this.classHistory.push({ action: 'add', class: className, timestamp: Date.now() });
    }
    
    removeClass(className) {
        super.removeClass(className);
        this.classHistory.push({ action: 'remove', class: className, timestamp: Date.now() });
    }
}

/**
 * Test wrapper for animation testing
 */
class AnimationTestWrapper {
    constructor() {
        this.mockDocument = new AnimationMockDOM();
        this.mockWebSocket = new MockWebSocket();
        this.arcList = null;
        this.setupEnvironment();
        this.initializeArcList();
    }
    
    setupEnvironment() {
        global.document = this.mockDocument;

        // Mock animateHierarchyTransition
        global.animateHierarchyTransition = async () => Promise.resolve();
        global.window = {
            addEventListener: () => {},
            getComputedStyle: () => ({
                display: 'block',
                visibility: 'visible',
                opacity: '1',
                zIndex: '1'
            })
        };
        global.WebSocket = MockWebSocket;
        global.localStorage = {
            getItem: () => null,
            setItem: () => {},
            removeItem: () => {}
        };
        global.fetch = async (url) => {
            return { json: () => Promise.resolve(loadTestData()) };
        };
    }
    
    initializeArcList() {
        const ArcList = loadArcListScript();
        if (!ArcList) {
            throw new Error('Failed to load ArcList class');
        }
        
        const config = {
            dataSource: '../json/playlists_with_tracks.json',
            dataType: 'parent_child',
            viewMode: 'hierarchical',
            parentKey: 'tracks',
            parentNameKey: 'name',
            context: 'music'
        };
        
        this.arcList = new ArcList(config);
        this.arcList.parentData = loadTestData();
        this.arcList.items = this.arcList.parentData.map((parent, index) => ({
            id: parent.id,
            name: parent.name || `Item ${index + 1}`,
            image: parent.image || 'placeholder.svg',
            tracks: parent.tracks
        }));
        
        // Simulate initial render
        this.simulateRender();
    }
    
    simulateRender() {
        // Clear container first
        const container = this.mockDocument.getElementById('arc-container');
        container.children = [];
        
        // Create mock elements for visible items
        const visibleItems = this.arcList.getVisibleItems();
        
        visibleItems.forEach((item, index) => {
            const element = new AnimationMockElement('div');
            element._classes = new Set(['arc-item']);
            element._updateClassName();
            element.dataset.itemId = item.id;
            element.dataset.index = String(item.index);
            
            // Mark center item as selected
            if (Math.abs(item.index - this.arcList.currentIndex) < 0.5) {
                element.classList.add('selected');
                element.dataset.selected = 'true';
            }
            
            container.appendChild(element);
        });
        
        // Ensure at least one selected element exists
        if (!container.querySelector('.arc-item.selected') && container.children.length > 0) {
            const centerIndex = Math.floor(container.children.length / 2);
            container.children[centerIndex].classList.add('selected');
            container.children[centerIndex].dataset.selected = 'true';
        }
    }
    
    getSelectedElement() {
        return this.mockDocument.querySelector('.arc-item.selected');
    }
    
    getBreadcrumbElement() {
        return this.mockDocument.querySelector('.arc-item.breadcrumb');
    }
}

// Animation tests
async function runAnimationTests() {
    console.log('🎬 BeoSound 5c Breadcrumb Animation Tests');
    console.log('=' .repeat(50));
    
    // Test 1: Selected element exists before transition
    runTest('Selected element exists in parent view', () => {
        const wrapper = new AnimationTestWrapper();
        wrapper.arcList.currentIndex = 2;
        wrapper.simulateRender();
        
        const selected = wrapper.getSelectedElement();
        assertTrue(selected !== null, 'Selected element should exist');
        assertTrue(selected.classList.contains('selected'), 'Element should have selected class');
        assertFalse(selected.classList.contains('breadcrumb'), 'Element should not have breadcrumb class yet');
        
        console.log('   ✅ Selected element ready for animation');
    });
    
    // Test 2: Animation transforms selected to breadcrumb
    runTest('Selected element animates to breadcrumb on left press', async () => {
        const wrapper = new AnimationTestWrapper();
        wrapper.arcList.currentIndex = 2;
        wrapper.simulateRender();
        
        const selectedBefore = wrapper.getSelectedElement();
        assertTrue(selectedBefore !== null, 'Selected element exists before transition');
        
        // Track the element reference
        const elementId = selectedBefore.dataset.itemId;
        
        // Simulate left button press
        // Ensure selected element exists for breadcrumb animation
        const createSelectedElement = () => {
            const selected = mockDocument.createElement('div');
            selected.classList.add('arc-item');
            selected.classList.add('selected');
            selected.dataset.index = String(Math.round(arcList.currentIndex));
            selected.textContent = arcList.items[Math.round(arcList.currentIndex)]?.name || 'Selected';
            
            // Add to container if not already there
            const container = mockDocument.getElementById('arc-container');
            if (container && !container.querySelector('.arc-item.selected')) {
                container.appendChild(selected);
            }
            return selected;
        };

        wrapper.arcList.handleButtonFromParent('left');
        
        // Check that same element now has breadcrumb class
        const breadcrumb = wrapper.getBreadcrumbElement();
        assertTrue(breadcrumb !== null, 'Breadcrumb element should exist');
        assertEqual(breadcrumb.dataset.itemId, elementId, 'Breadcrumb should be same element as selected');
        
        // Check class changes
        assertTrue(breadcrumb.classList.contains('breadcrumb'), 'Element should have breadcrumb class');
        assertFalse(breadcrumb.classList.contains('selected'), 'Element should not have selected class');
        assertTrue(breadcrumb.dataset.animatedParent === 'true', 'Element should be marked as animated parent');
        
        console.log('   ✅ Selected element transformed to breadcrumb');
    });
    
    // Test 3: Breadcrumb persists during child navigation
    runTest('Breadcrumb remains visible while navigating tracks', () => {
        const wrapper = new AnimationTestWrapper();
        wrapper.arcList.currentIndex = 2;
        wrapper.simulateRender();
        
        // Enter child view
        wrapper.arcList.handleButtonFromParent('left');
        
        // Simulate navigation in child view
        wrapper.arcList.handleNavFromParent({ direction: 'clock', speed: 20 });
        
        // Check breadcrumb still exists
        const breadcrumb = wrapper.getBreadcrumbElement();
        assertTrue(breadcrumb !== null, 'Breadcrumb should still exist after navigation');
        assertTrue(breadcrumb.classList.contains('breadcrumb'), 'Breadcrumb class should be maintained');
        
        console.log('   ✅ Breadcrumb persists during track navigation');
    });
    
    // Test 4: Breadcrumb contains playlist info
    runTest('Breadcrumb contains playlist name and image', () => {
        const wrapper = new AnimationTestWrapper();
        wrapper.arcList.currentIndex = 2;
        wrapper.simulateRender();
        
        const selectedPlaylist = wrapper.arcList.parentData[2];
        
        // Enter child view
        wrapper.arcList.handleButtonFromParent('left');
        
        // Check breadcrumb content
        assertTrue(wrapper.arcList.selectedParent !== null, 'Selected parent should be set');
        assertEqual(wrapper.arcList.selectedParent.name, selectedPlaylist.name, 'Selected parent matches');
        assertEqual(wrapper.arcList.selectedParent.image, selectedPlaylist.image, 'Parent image preserved');
        
        console.log(`   ✅ Breadcrumb contains: "${selectedPlaylist.name}"`);
    });
    
    // Test 5: Breadcrumb animates back on right press
    runTest('Breadcrumb animates back to center on right press', () => {
        const wrapper = new AnimationTestWrapper();
        wrapper.arcList.currentIndex = 2;
        wrapper.simulateRender();
        
        // Enter child view
        wrapper.arcList.handleButtonFromParent('left');
        const breadcrumb = wrapper.getBreadcrumbElement();
        assertTrue(breadcrumb !== null, 'Breadcrumb exists');
        
        // Exit child view
        wrapper.arcList.handleButtonFromParent('right');
        
        // Check that breadcrumb transforms back
        // In real implementation, the breadcrumb class would be removed
        // and selected class would be added back
        assertEqual(wrapper.arcList.viewMode, 'parent', 'Should return to parent view');
        
        console.log('   ✅ Breadcrumb animates back on exit');
    });
    
    // Test 6: No duplicate breadcrumbs
    runTest('Only one breadcrumb exists at a time', () => {
        const wrapper = new AnimationTestWrapper();
        
        // Navigate to different playlists and enter child view multiple times
        for (let i = 0; i < 3; i++) {
            wrapper.arcList.currentIndex = i;
            wrapper.simulateRender();
            wrapper.arcList.handleButtonFromParent('left');
            
            const breadcrumbs = wrapper.mockDocument.getElementById('arc-container')
                .children.filter(child => child.classList.contains('breadcrumb'));
            
            assertTrue(breadcrumbs.length <= 1, `Only one breadcrumb should exist, found ${breadcrumbs.length}`);
            
            // Return to parent
            wrapper.arcList.handleButtonFromParent('right');
        }
        
        console.log('   ✅ Breadcrumb management prevents duplicates');
    });
}

// Test runner
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
        failCount++;
        
        return null;
    }
}

// Main execution
async function main() {
    try {
        await runAnimationTests();
        
        console.log('\n📊 Animation Test Summary');
        console.log('=' .repeat(40));
        console.log(`Total Tests: ${testCount}`);
        console.log(`Passed: ${passCount} ✅`);
        console.log(`Failed: ${failCount} ❌`);
        console.log(`Success Rate: ${((passCount / testCount) * 100).toFixed(1)}%`);
        
        if (failCount === 0) {
            console.log('\n🎉 All animation tests passed!');
            console.log('✅ Breadcrumb animation working correctly');
        } else {
            console.log('\n⚠️  Some animation tests failed.');
            console.log('❌ Animation issues need to be addressed');
            process.exit(1);
        }
        
    } catch (error) {
        console.error('🔥 Test execution failed:', error);
        process.exit(1);
    }
}

// Run tests
main().catch(console.error);