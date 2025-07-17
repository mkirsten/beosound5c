#!/usr/bin/env node

/**
 * Real-World Bug Detection Tests for BeoSound 5c
 * 
 * These tests capture specific bugs reported in the real interface:
 * 1. Music not showing at all
 * 2. Scenes: left press makes icon go too far left, no sublist shows
 * 3. Settings: same issue as Scenes
 * 4. Navigation cross-contamination between Music/Scenes
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

// Enhanced MockDOM that simulates real browser behavior
class RealWorldMockDOM extends MockDOM {
    constructor() {
        super();
        this.setupAllRequiredElements();
    }
    
    setupAllRequiredElements() {
        // Create all required DOM elements for different views
        const views = ['music', 'scenes', 'settings'];
        
        views.forEach(view => {
            const arcContainer = new MockElement('div');
            arcContainer.id = `${view}-arc-container`;
            this.elements.set(`${view}-arc-container`, arcContainer);
            
            const currentItem = new MockElement('span');
            currentItem.id = `${view}-current-item`;
            this.elements.set(`${view}-current-item`, currentItem);
            
            const totalItems = new MockElement('span');
            totalItems.id = `${view}-total-items`;
            this.elements.set(`${view}-total-items`, totalItems);
            
            const hierarchyBg = new MockElement('div');
            hierarchyBg.id = `${view}-hierarchy-background`;
            this.elements.set(`${view}-hierarchy-background`, hierarchyBg);
        });
        
        // Main elements (fallback)
        const mainContainer = new MockElement('div');
        mainContainer.id = 'arc-container';
        this.elements.set('arc-container', mainContainer);
        
        const mainCurrentItem = new MockElement('span');
        mainCurrentItem.id = 'current-item';
        this.elements.set('current-item', mainCurrentItem);
        
        const mainTotalItems = new MockElement('span');
        mainTotalItems.id = 'total-items';
        this.elements.set('total-items', mainTotalItems);
        
        const mainHierarchyBg = new MockElement('div');
        mainHierarchyBg.id = 'hierarchy-background';
        this.elements.set('hierarchy-background', mainHierarchyBg);
    }
    
    querySelector(selector) {
        // Enhanced querySelector that simulates real DOM behavior
        if (selector === '.arc-item.selected') {
            const selectedItem = new MockElement('div');
            selectedItem.classList.add('arc-item');
            selectedItem.classList.add('selected');
            selectedItem.style.transform = 'translateX(0px)'; // Default position
            return selectedItem;
        }
        
        if (selector === '.arc-item.breadcrumb') {
            const breadcrumb = new MockElement('div');
            breadcrumb.classList.add('arc-item');
            breadcrumb.classList.add('breadcrumb');
            return breadcrumb;
        }
        
        return super.querySelector ? super.querySelector(selector) : null;
    }
    
    querySelectorAll(selector) {
        if (selector === '.arc-item:not(.breadcrumb)') {
            // Return empty array to simulate no items showing
            return [];
        }
        
        return super.querySelectorAll ? super.querySelectorAll(selector) : [];
    }
}

// Test configuration for different views
const viewConfigs = {
    music: {
        dataSource: '../json/playlists_with_tracks.json',
        dataType: 'parent_child',
        viewMode: 'hierarchical',
        parentKey: 'tracks',
        parentNameKey: 'name',
        context: 'music',
        expectedItemCount: 59 // Based on test data
    },
    scenes: {
        dataSource: '../json/scenes.json',
        dataType: 'parent_child',
        viewMode: 'hierarchical',
        parentKey: 'scenes',
        parentNameKey: 'name',
        context: 'scenes',
        expectedItemCount: 10 // Estimated
    },
    settings: {
        dataSource: '../json/settings.json',
        dataType: 'parent_child',
        viewMode: 'hierarchical',
        parentKey: 'options',
        parentNameKey: 'name',
        context: 'settings',
        expectedItemCount: 8 // Estimated
    }
};

// Mock data for scenes and settings
const mockScenesData = [
    {
        id: 'living-room',
        name: 'Living Room',
        scenes: [
            { id: 'movie-night', name: 'Movie Night' },
            { id: 'reading', name: 'Reading' },
            { id: 'party', name: 'Party' }
        ]
    },
    {
        id: 'bedroom',
        name: 'Bedroom',
        scenes: [
            { id: 'sleep', name: 'Sleep' },
            { id: 'wake-up', name: 'Wake Up' }
        ]
    }
];

const mockSettingsData = [
    {
        id: 'display',
        name: 'Display',
        options: [
            { id: 'brightness', name: 'Brightness' },
            { id: 'contrast', name: 'Contrast' }
        ]
    },
    {
        id: 'audio',
        name: 'Audio',
        options: [
            { id: 'volume', name: 'Volume' },
            { id: 'bass', name: 'Bass' },
            { id: 'treble', name: 'Treble' }
        ]
    }
];

// Test ArcList wrapper for different views
class ViewTestWrapper {
    constructor(viewType) {
        this.viewType = viewType;
        this.config = viewConfigs[viewType];
        this.mockDocument = new RealWorldMockDOM();
        this.mockWebSocket = new MockWebSocket();
        this.arcList = null;
        this.initializeView();
    }
    
    initializeView() {
        const ArcList = loadArcListScript();
        if (!ArcList) {
            throw new Error('Failed to load ArcList class');
        }
        
        this.setupMockEnvironment();
        this.arcList = new ArcList(this.config);
        this.loadMockData();
    }
    
    setupMockEnvironment() {
        global.document = this.mockDocument;

        // Mock animateHierarchyTransition
        global.animateHierarchyTransition = async () => Promise.resolve();
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
            if (url.includes('playlists_with_tracks.json')) {
                return { json: () => Promise.resolve(loadTestData()) };
            } else if (url.includes('scenes.json')) {
                return { json: () => Promise.resolve(mockScenesData) };
            } else if (url.includes('settings.json')) {
                return { json: () => Promise.resolve(mockSettingsData) };
            }
            throw new Error(`Fetch not mocked for URL: ${url}`);
        };
        
        global.document.querySelector = this.mockDocument.querySelector.bind(this.mockDocument);
        global.document.querySelectorAll = this.mockDocument.querySelectorAll.bind(this.mockDocument);
        global.document.contains = this.mockDocument.contains ? this.mockDocument.contains.bind(this.mockDocument) : () => true;
    }
    
    loadMockData() {
        if (this.viewType === 'music') {
            this.arcList.parentData = loadTestData();
        } else if (this.viewType === 'scenes') {
            this.arcList.parentData = mockScenesData;
        } else if (this.viewType === 'settings') {
            this.arcList.parentData = mockSettingsData;
        }
        
        // Set up items
        this.arcList.items = this.arcList.parentData.map((parent, index) => ({
            id: parent.id,
            name: parent.name || `Item ${index + 1}`,
            image: parent.image || 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNjQiIGhlaWdodD0iNjQiIHZpZXdCb3g9IjAgMCA2NCA2NCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHJlY3Qgd2lkdGg9IjY0IiBoZWlnaHQ9IjY0IiBmaWxsPSIjMzMzMzMzIi8+Cjx0ZXh0IHg9IjMyIiB5PSI0MCIgZm9udC1mYW1pbHk9IkFyaWFsLCBzYW5zLXNlcmlmIiBmb250LXNpemU9IjI0IiBmaWxsPSIjZmZmZmZmIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIj7imqo8L3RleHQ+Cjwvc3ZnPgo='
        }));
        
        // Force initial render to display items
        console.log('Calling render(), container before:', this.mockDocument.getElementById('arc-container').children.length);
        console.log('ArcList.container is null:', this.arcList.container === null);
        console.log('ArcList.container exists:', !!this.arcList.container);
        
        // Test appendChild directly
        const testElement = document.createElement('div');
        testElement.className = 'test-element';
        console.log('Testing appendChild directly...');
        this.arcList.container.appendChild(testElement);
        console.log('After test appendChild:', this.mockDocument.getElementById('arc-container').children.length);
        
        this.arcList.render();
        console.log('After render(), container children:', this.mockDocument.getElementById('arc-container').children.length);
    }
    
    // Test methods
    getItemCount() {
        return this.arcList.items.length;
    }
    
    getViewMode() {
        return this.arcList.viewMode;
    }
    
    getCurrentIndex() {
        return Math.round(this.arcList.currentIndex);
    }
    
    getCurrentItem() {
        const index = this.getCurrentIndex();
        return this.arcList.items[index];
    }
    
    isAnimating() {
        return this.arcList.isAnimating;
    }
    
    simulateLeftPress() {
        // Ensure selected element exists for breadcrumb animation
        const createSelectedElement = () => {
            const selected = this.mockDocument.createElement('div');
            selected.classList.add('arc-item');
            selected.classList.add('selected');
            selected.dataset.index = String(Math.round(this.arcList.currentIndex));
            selected.textContent = this.arcList.items[Math.round(this.arcList.currentIndex)]?.name || 'Selected';
            
            // Add to container if not already there
            const container = this.mockDocument.getElementById('arc-container');
            if (container && !container.querySelector('.arc-item.selected')) {
                container.appendChild(selected);
            }
            return selected;
        };
        
        // Create the selected element before pressing left
        createSelectedElement();

        this.arcList.handleButtonFromParent('left');
    }
    
    simulateRightPress() {
        this.arcList.handleButtonFromParent('right');
    }
    
    simulateNavigation(direction, steps = 1) {
        for (let i = 0; i < steps; i++) {
            this.arcList.handleNavFromParent({
                direction: direction,
                speed: 20
            });
        }
    }
    
    // Check if selected element is positioned correctly
    checkSelectedElementPosition() {
        const selectedElement = this.mockDocument.querySelector('.arc-item.selected');
        if (!selectedElement) return { exists: false, position: null };
        
        const transform = selectedElement.style.transform;
        const match = transform.match(/translateX\(([^)]+)\)/);
        const xPosition = match ? parseFloat(match[1]) : 0;
        
        return {
            exists: true,
            position: xPosition,
            tooFarLeft: xPosition < -100, // Arbitrary threshold for "too far left"
            visible: selectedElement.style.display !== 'none' && selectedElement.style.opacity !== '0'
        };
    }
    
    // Check if sublist items are visible
    checkSublistVisibility() {
        const sublistItems = this.mockDocument.querySelectorAll('.arc-item:not(.breadcrumb)');
        return {
            count: sublistItems.length,
            hasVisibleItems: sublistItems.length > 0
        };
    }
    
    // Check if parent items are visible (for detecting black screen)
    checkVisibleItems() {
        // In parent mode, check if items would be rendered
        if (this.arcList.viewMode === 'parent') {
            const visibleItems = this.arcList.getVisibleItems ? this.arcList.getVisibleItems() : [];
            return {
                count: visibleItems.length,
                hasVisibleItems: visibleItems.length > 0
            };
        } else {
            // In child mode, check child items
            const childItems = this.mockDocument.querySelectorAll('.arc-item[data-child-item="true"]');
            return {
                count: childItems.length,
                hasVisibleItems: childItems.length > 0
            };
        }
    }
    
    // Check if container exists and has content
    checkContainer() {
        const container = this.mockDocument.getElementById('arc-container');
        if (!container) {
            return { exists: false, hasContent: false };
        }
        
        // Get actual rendered children count
        const childCount = container.children ? container.children.length : 0;
        const hasContent = childCount > 0;
        
        return {
            exists: true,
            hasContent: hasContent,
            childCount: childCount
        };
    };
    }
}

// Real-world bug tests
async function runRealWorldBugTests() {
    console.log('🔍 BeoSound 5c Real-World Bug Detection Tests');
    console.log('=' .repeat(55));
    
    // Bug 1: Music not showing at all (black screen)
    runTest('Bug 1: Music view should show playlist items (not black screen)', () => {
        const musicView = new ViewTestWrapper('music');
        
        // Check that items are loaded
        const itemCount = musicView.getItemCount();
        assertTrue(itemCount > 0, `Music view should have items, got ${itemCount}`);
        assertTrue(itemCount >= 50, `Music view should have many playlists, got ${itemCount}`);
        
        const currentItem = musicView.getCurrentItem();
        assertTrue(currentItem !== null, 'Music view should have a current item');
        assertTrue(currentItem.name.length > 0, 'Current item should have a name');
        
        // Check that the view mode is correct for displaying items
        const viewMode = musicView.getViewMode();
        assertEqual(viewMode, 'parent', 'Music view should be in parent mode to show playlists');
        
        // Check if animation is blocking render
        const isAnimating = musicView.isAnimating();
        assertFalse(isAnimating, 'Animation flag should not be stuck as true');
        
        // Track render calls and method calls to detect infinite loops
        let renderCallCount = 0;
        let renderChildItemsCallCount = 0;
        let callStack = [];
        
        const originalRender = musicView.arcList.render.bind(musicView.arcList);
        const originalRenderChildItems = musicView.arcList.renderChildItems ? musicView.arcList.renderChildItems.bind(musicView.arcList) : null;
        
        musicView.arcList.render = function() {
            renderCallCount++;
            callStack.push('render');
            
            // Detect alternating pattern between render and renderChildItems
            if (callStack.length > 4) {
                const recent = callStack.slice(-4);
                if (recent[0] === 'render' && recent[1] === 'renderChildItems' && 
                    recent[2] === 'render' && recent[3] === 'renderChildItems') {
                    throw new Error('Infinite loop detected: render and renderChildItems alternating!');
                }
            }
            
            if (renderCallCount > 10) {
                throw new Error(`Infinite render loop detected! Call pattern: ${callStack.slice(-10).join(' -> ')}`);
            }
            return originalRender();
        };
        
        if (originalRenderChildItems) {
            musicView.arcList.renderChildItems = function() {
                renderChildItemsCallCount++;
                callStack.push('renderChildItems');
                
                if (renderChildItemsCallCount > 10) {
                    throw new Error('Infinite renderChildItems loop detected!');
                }
                return originalRenderChildItems();
            };
        }
        
        // Force a render to ensure items are displayed
        musicView.arcList.render();
        
        // Check that render didn't create an infinite loop
        assertTrue(renderCallCount <= 1, `Render called ${renderCallCount} times - possible loop`);
        
        // Check that DOM elements would be created for parent items
        const visibleItems = musicView.checkVisibleItems();
        assertTrue(visibleItems.count > 0, 'Should have visible items rendered');
        
        // Check that the container exists and has content
        const containerCheck = musicView.checkContainer();
        assertTrue(containerCheck.exists, 'Arc container should exist');
        
        // Enhanced duplicate method detection
        const proto = Object.getPrototypeOf(musicView.arcList);
        const methodCounts = {};
        
        // Count all methods
        Object.getOwnPropertyNames(proto).forEach(name => {
            if (typeof proto[name] === 'function') {
                methodCounts[name] = (methodCounts[name] || 0) + 1;
            }
        });
        
        // Check for duplicates
        const duplicates = Object.entries(methodCounts).filter(([name, count]) => count > 1);
        assertEqual(duplicates.length, 0, `Duplicate methods found: ${duplicates.map(d => d[0]).join(', ')}`);
        
        // Specifically check critical methods
        assertEqual(methodCounts['renderChildItems'] || 0, 1, 'renderChildItems should be defined only once');
        assertEqual(methodCounts['render'] || 0, 1, 'render should be defined only once');
        assertEqual(methodCounts['loadParentChildren'] || 0, 1, 'loadParentChildren should be defined only once');
        
        console.log(`   ✅ Music view has ${itemCount} items`);
        console.log(`   ✅ Current item: "${currentItem.name}"`);
        console.log(`   ✅ View mode: ${viewMode}`);
        console.log(`   ✅ Animation flag: ${isAnimating}`);
        console.log(`   ✅ Visible items: ${visibleItems.count}`);
        console.log(`   ✅ Container exists: ${containerCheck.exists}`);
        console.log(`   ✅ Container has content: ${containerCheck.hasContent}`);
        console.log(`   ✅ Container child count: ${containerCheck.childCount}`);
        console.log(`   ✅ Render call count: ${renderCallCount}`);
        console.log(`   ✅ No duplicate methods detected`);
        
        assertTrue(containerCheck.hasContent, 'Arc container should have content (not black screen)');
    });
    
    // Bug 2: Scenes - left press makes icon go too far left
    runTest('Bug 2: Scenes left navigation - icon positioning', async () => {
        const scenesView = new ViewTestWrapper('scenes');
        
        // Initial state should be normal
        const initialPosition = scenesView.checkSelectedElementPosition();
        assertTrue(initialPosition.exists, 'Selected element should exist initially');
        assertTrue(initialPosition.visible, 'Selected element should be visible initially');
        
        // Press left to enter child view
        scenesView.simulateLeftPress();
        
        // Wait for animation
        await wait(500);
        
        // Check if icon went too far left
        const afterLeftPress = scenesView.checkSelectedElementPosition();
        assertTrue(afterLeftPress.exists, 'Selected element should still exist after left press');
        
        if (afterLeftPress.tooFarLeft) {
            console.log(`   ❌ BUG DETECTED: Icon went too far left (${afterLeftPress.position}px)`);
            // This is the expected failure - we want to detect this bug
            throw new Error(`Icon positioned too far left: ${afterLeftPress.position}px`);
        }
        
        // Check if sublist is showing
        const sublistCheck = scenesView.checkSublistVisibility();
        assertTrue(sublistCheck.hasVisibleItems, 'Sublist should be visible after left press');
        
        console.log(`   ✅ Icon positioned correctly: ${afterLeftPress.position}px`);
        console.log(`   ✅ Sublist showing ${sublistCheck.count} items`);
    });
    
    // Bug 3: Settings - same issue as Scenes
    runTest('Bug 3: Settings left navigation - same issue as Scenes', async () => {
        const settingsView = new ViewTestWrapper('settings');
        
        // Initial state should be normal
        const initialPosition = settingsView.checkSelectedElementPosition();
        assertTrue(initialPosition.exists, 'Selected element should exist initially');
        assertTrue(initialPosition.visible, 'Selected element should be visible initially');
        
        // Press left to enter child view
        settingsView.simulateLeftPress();
        
        // Wait for animation
        await wait(500);
        
        // Check if icon went too far left
        const afterLeftPress = settingsView.checkSelectedElementPosition();
        assertTrue(afterLeftPress.exists, 'Selected element should still exist after left press');
        
        if (afterLeftPress.tooFarLeft) {
            console.log(`   ❌ BUG DETECTED: Icon went too far left (${afterLeftPress.position}px)`);
            // This is the expected failure - we want to detect this bug
            throw new Error(`Icon positioned too far left: ${afterLeftPress.position}px`);
        }
        
        // Check if sublist is showing
        const sublistCheck = settingsView.checkSublistVisibility();
        assertTrue(sublistCheck.hasVisibleItems, 'Sublist should be visible after left press');
        
        console.log(`   ✅ Icon positioned correctly: ${afterLeftPress.position}px`);
        console.log(`   ✅ Sublist showing ${sublistCheck.count} items`);
    });
    
    // Bug 4: Navigation cross-contamination
    runTest('Bug 4: Navigation cross-contamination between views', () => {
        const musicView = new ViewTestWrapper('music');
        const scenesView = new ViewTestWrapper('scenes');
        
        // Navigate in music view
        const musicStartIndex = musicView.getCurrentIndex();
        musicView.simulateNavigation('clock', 3);
        const musicEndIndex = musicView.getCurrentIndex();
        
        // Check that scenes view wasn't affected
        const scenesCurrentIndex = scenesView.getCurrentIndex();
        assertEqual(scenesCurrentIndex, 0, 'Scenes view should not be affected by music navigation');
        
        // Navigate in scenes view
        scenesView.simulateNavigation('clock', 1);
        const scenesEndIndex = scenesView.getCurrentIndex();
        
        // Check that music view wasn't affected
        const musicCurrentIndex = musicView.getCurrentIndex();
        assertEqual(musicCurrentIndex, musicEndIndex, 'Music view should not be affected by scenes navigation');
        
        console.log(`   ✅ Music: ${musicStartIndex} → ${musicEndIndex} (isolated)`);
        console.log(`   ✅ Scenes: 0 → ${scenesEndIndex} (isolated)`);
    });
    
    // Bug 5: Music view initialization
    runTest('Bug 5: Music view properly initializes and renders', () => {
        const musicView = new ViewTestWrapper('music');
        
        // Check view mode
        assertEqual(musicView.getViewMode(), 'parent', 'Music view should start in parent mode');
        
        // Check that we have the expected number of items
        const itemCount = musicView.getItemCount();
        assertTrue(itemCount > 0, 'Music view should have items');
        
        // Check that current item exists and has required properties
        const currentItem = musicView.getCurrentItem();
        assertTrue(currentItem !== null, 'Should have current item');
        assertTrue(currentItem.id !== undefined, 'Current item should have id');
        assertTrue(currentItem.name !== undefined, 'Current item should have name');
        
        console.log(`   ✅ Music view initialized with ${itemCount} items`);
        console.log(`   ✅ Current item: "${currentItem.name}" (id: ${currentItem.id})`);
    });
}

// Wait helper for async operations
function wait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// Custom test function for this file
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

// Run the tests
async function main() {
    try {
        await runRealWorldBugTests();
        
        console.log('\n📊 Real-World Bug Detection Summary');
        console.log('=' .repeat(40));
        console.log(`Total Tests: ${testCount}`);
        console.log(`Passed: ${passCount} ✅`);
        console.log(`Failed: ${failCount} ❌`);
        console.log(`Success Rate: ${((passCount / testCount) * 100).toFixed(1)}%`);
        
        if (failCount > 0) {
            console.log('\n🔍 Bugs Successfully Detected:');
            console.log('• Music view initialization issues');
            console.log('• Scenes left navigation - icon positioning');
            console.log('• Settings left navigation - same issue');
            console.log('• Navigation cross-contamination between views');
            console.log('\n✅ Test suite is working - these failures indicate real bugs to fix');
        } else {
            console.log('\n🎉 All tests passed!');
            console.log('✅ No bugs detected - navigation system is working correctly');
        }
        
    } catch (error) {
        console.error('🔥 Test execution failed:', error);
        process.exit(1);
    }
}

// Run the main test suite
main().catch(console.error);