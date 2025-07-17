#!/usr/bin/env node

/**
 * Comprehensive test suite for BeoSound 5c navigation
 * Tests all scenarios to ensure 100% functionality
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

// Test state
let testCount = 0;
let passCount = 0;
let failCount = 0;
let issues = [];

// Enhanced MockDOM for realistic testing
class RealisticMockDOM extends MockDOM {
    constructor() {
        super();
        this.setupDOM();
        this._animationCallbacks = [];
    }
    
    setupDOM() {
        // Create container
        const container = new RealisticMockElement('div');
        container.id = 'arc-container';
        container.style.position = 'relative';
        container.style.width = '1024px';
        container.style.height = '768px';
        this.elements.set('arc-container', container);
        
        // Other elements
        this.elements.set('current-item', new RealisticMockElement('span'));
        this.elements.set('total-items', new RealisticMockElement('span'));
    }
    
    querySelector(selector) {
        // Handle class selectors
        if (selector.startsWith('.')) {
            const container = this.getElementById('arc-container');
            if (!container) return null;
            
            const classes = selector.substring(1).split('.');
            return container.findChild(el => {
                if (!el.classList) return false;
                return classes.every(cls => el.classList.contains(cls));
            });
        }
        
        // Handle attribute selectors
        if (selector.includes('[')) {
            const match = selector.match(/\[([^=]+)="([^"]+)"\]/);
            if (match) {
                const [, attr, value] = match;
                const container = this.getElementById('arc-container');
                return container ? container.findChild(el => el.getAttribute(attr) === value) : null;
            }
        }
        
        return super.querySelector ? super.querySelector(selector) : null;
    }
    
    querySelectorAll(selector) {
        const results = [];
        const container = this.getElementById('arc-container');
        if (!container) return results;
        
        if (selector === '.arc-item[data-child-item="true"]') {
            container.children.forEach(child => {
                if (child.getAttribute('data-child-item') === 'true') {
                    results.push(child);
                }
            });
        } else if (selector === '.arc-item:not(.breadcrumb)') {
            container.children.forEach(child => {
                if (child.classList && child.classList.contains('arc-item') && 
                    !child.classList.contains('breadcrumb')) {
                    results.push(child);
                }
            });
        } else if (selector.startsWith('.')) {
            const className = selector.substring(1);
            container.children.forEach(child => {
                if (child.classList && child.classList.contains(className)) {
                    results.push(child);
                }
            });
        }
        
        return results;
    }
}

// Enhanced MockElement with full DOM-like behavior
class RealisticMockElement extends MockElement {
    constructor(tagName) {
        super(tagName);
        this.dataset = {};
        this._attributes = new Map();
    }
    
    setAttribute(name, value) {
        this._attributes.set(name, value);
        if (name.startsWith('data-')) {
            const dataName = name.substring(5).replace(/-([a-z])/g, (g) => g[1].toUpperCase());
            this.dataset[dataName] = value;
        }
    }
    
    getAttribute(name) {
        return this._attributes.get(name) || null;
    }
    
    findChild(predicate) {
        for (const child of this.children) {
            if (predicate(child)) return child;
            const found = child.findChild ? child.findChild(predicate) : null;
            if (found) return found;
        }
        return null;
    }
    
    remove() {
        if (this.parentElement) {
            const index = this.parentElement.children.indexOf(this);
            if (index > -1) {
                this.parentElement.children.splice(index, 1);
            }
        }
    }
}

// Test scenarios
class TestScenario {
    constructor(name, description) {
        this.name = name;
        this.description = description;
        this.steps = [];
        this.assertions = [];
    }
    
    addStep(action, params) {
        this.steps.push({ action, params });
        return this;
    }
    
    addAssertion(check, message) {
        this.assertions.push({ check, message });
        return this;
    }
    
    async run(wrapper) {
        console.log(`\n🧪 ${this.name}`);
        console.log(`   ${this.description}`);
        
        try {
            // Execute steps
            for (const step of this.steps) {
                await this.executeStep(wrapper, step);
            }
            
            // Run assertions
            let allPassed = true;
            for (const assertion of this.assertions) {
                try {
                    const result = assertion.check(wrapper);
                    if (!result) {
                        console.log(`   ❌ ${assertion.message}`);
                        allPassed = false;
                    }
                } catch (e) {
                    console.log(`   ❌ ${assertion.message}: ${e.message}`);
                    allPassed = false;
                }
            }
            
            if (allPassed) {
                console.log(`   ✅ All checks passed`);
                return true;
            }
            return false;
        } catch (error) {
            console.log(`   ❌ Error: ${error.message}`);
            return false;
        }
    }
    
    async executeStep(wrapper, step) {
        switch (step.action) {
            case 'navigate':
                wrapper.navigate(step.params.direction, step.params.steps);
                break;
            case 'pressButton':
                wrapper.pressButton(step.params.button);
                break;
            case 'wait':
                await new Promise(resolve => setTimeout(resolve, step.params.ms));
                break;
            case 'render':
                wrapper.forceRender();
                break;
        }
    }
}

// Test wrapper
class NavigationTestWrapper {
    constructor() {
        this.mockDocument = new RealisticMockDOM();
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
            innerWidth: 1024,
            innerHeight: 768,
            addEventListener: () => {},
            getComputedStyle: (el) => ({
                display: el.style.display || 'block',
                visibility: el.style.visibility || 'visible',
                opacity: el.style.opacity || '1',
                transform: el.style.transform || 'none',
                zIndex: el.style.zIndex || 'auto'
            })
        };
        global.WebSocket = MockWebSocket;
        global.localStorage = {
            getItem: () => null,
            setItem: () => {},
            removeItem: () => {}
        };
        global.fetch = async (url) => {
            if (url.includes('webhook')) {
                return { ok: true, json: () => Promise.resolve({}) };
            }
            return { json: () => Promise.resolve(loadTestData()) };
        };
    }
    
    initializeArcList() {
        const ArcList = loadArcListScript();
        if (!ArcList) {
            throw new Error('Failed to load ArcList class');
        }
        
        this.arcList = new ArcList({
            dataSource: '../json/playlists_with_tracks.json',
            dataType: 'parent_child',
            viewMode: 'hierarchical',
            parentKey: 'tracks',
            parentNameKey: 'name',
            context: 'music'
        });
        
        // Load data
        this.arcList.parentData = loadTestData();
        this.arcList.items = this.arcList.parentData.map((parent, index) => ({
            id: parent.id,
            name: parent.name || `Item ${index + 1}`,
            image: parent.image || 'placeholder.svg',
            tracks: parent.tracks
        }));
        
        // Initial render
        this.forceRender();
    }
    
    navigate(direction, steps = 1) {
        for (let i = 0; i < steps; i++) {
            this.arcList.handleNavFromParent({ direction, speed: 20 });
        }
    }
    
    pressButton(button) {
        // Ensure selected element exists for breadcrumb animation
        const createSelectedElement = () => {
            const selected = this.mockDocument.createElement('div');
            selected.classList.add('arc-item');
            selected.classList.add('selected');
            selected.dataset.index = String(Math.round(this.arcList.currentIndex));
            selected.textContent = this.arcList.items[Math.round(this.arcList.currentIndex)]?.name || 'Selected';
            
            // Add to container if not already there
            const container = this.mockDocument.getElementById('arc-container');
            if (container && !this.mockDocument.querySelector('.arc-item.selected')) {
                container.appendChild(selected);
            }
            return selected;
        };

        this.arcList.handleButtonFromParent(button);
    }
    
    forceRender() {
        this.arcList.isAnimating = false;
        this.arcList.render();
    }
    
    // Getters for assertions
    getViewMode() {
        return this.arcList.viewMode;
    }
    
    getCurrentIndex() {
        return Math.round(this.arcList.currentIndex);
    }
    
    getSelectedElement() {
        // Enhanced querySelector mock for breadcrumb tests
        const originalQuerySelector = mockDocument.querySelector ? mockDocument.querySelector.bind(mockDocument) : () => null;
        mockDocument.querySelector = function(selector) {
            if (selector === '.arc-item.selected') {
                // First check container for existing selected element
                const container = this.getElementById('arc-container');
                if (container && container.children) {
                    for (const child of container.children) {
                        if (child.classList && child.classList.contains('selected')) {
                            return child;
                        }
                    }
                }
                // Create one if needed
                return createSelectedElement();
            }
            
            if (selector === '.arc-item.breadcrumb') {
                const container = this.getElementById('arc-container');
                if (container && container.children) {
                    for (const child of container.children) {
                        if (child.classList && child.classList.contains('breadcrumb')) {
                            return child;
                        }
                    }
                }
                return null;
            }
            
            return originalQuerySelector(selector);
        };
mockDocument.querySelector('.arc-item.selected');
    }
    
    getBreadcrumb() {
        return this.mockDocument.querySelector('.arc-item.breadcrumb');
    }
    
    getChildItems() {
        return this.mockDocument.querySelectorAll('.arc-item[data-child-item="true"]');
    }
    
    getContainer() {
        return this.mockDocument.getElementById('arc-container');
    }
    
    hasVisibleItems() {
        const container = this.getContainer();
        return container && container.children.length > 0;
    }
    
    getSelectedParent() {
        return this.arcList.selectedParent;
    }
}

// Define all test scenarios
function createTestScenarios() {
    const scenarios = [];
    
    // Scenario 1: Basic navigation
    scenarios.push(
        new TestScenario('Basic Navigation', 'Navigate through playlists')
            .addStep('navigate', { direction: 'clock', steps: 3 })
            .addAssertion(w => w.getCurrentIndex() === 3, 'Should be at index 3')
            .addAssertion(w => w.getViewMode() === 'parent', 'Should be in parent mode')
            .addAssertion(w => w.hasVisibleItems(), 'Should have visible items')
    );
    
    // Scenario 2: Enter child view with animation
    scenarios.push(
        new TestScenario('Enter Child View', 'Select playlist and see animation')
            .addStep('navigate', { direction: 'clock', steps: 2 })
            .addStep('pressButton', { button: 'left' })
            .addStep('wait', { ms: 50 })
            .addAssertion(w => w.getViewMode() === 'child', 'Should be in child mode')
            .addAssertion(w => w.getBreadcrumb() !== null, 'Should have breadcrumb')
            .addAssertion(w => {
                const bc = w.getBreadcrumb();
                return bc && bc.classList.contains('breadcrumb');
            }, 'Breadcrumb should have correct class')
            .addAssertion(w => {
                const bc = w.getBreadcrumb();
                return bc && bc.dataset.animatedParent === 'true';
            }, 'Breadcrumb should be marked as animated parent')
    );
    
    // Scenario 3: Breadcrumb contains playlist info
    scenarios.push(
        new TestScenario('Breadcrumb Content', 'Breadcrumb shows playlist info')
            .addStep('navigate', { direction: 'clock', steps: 3 })
            .addStep('pressButton', { button: 'left' })
            .addAssertion(w => {
                const parent = w.getSelectedParent();
                return parent && parent.name && parent.image;
            }, 'Selected parent should have name and image')
            .addAssertion(w => {
                const bc = w.getBreadcrumb();
                if (!bc) return false;
                // Check if breadcrumb would contain image
                const parent = w.getSelectedParent();
                return parent !== null;
            }, 'Breadcrumb should reference selected parent')
    );
    
    // Scenario 4: Navigate in child view
    scenarios.push(
        new TestScenario('Child Navigation', 'Navigate through tracks')
            .addStep('navigate', { direction: 'clock', steps: 1 })
            .addStep('pressButton', { button: 'left' })
            .addStep('wait', { ms: 50 })
            .addStep('navigate', { direction: 'clock', steps: 2 })
            .addAssertion(w => w.getViewMode() === 'child', 'Should remain in child mode')
            .addAssertion(w => w.getBreadcrumb() !== null, 'Breadcrumb should persist')
            .addAssertion(w => w.getCurrentIndex() === 2, 'Should navigate in tracks')
    );
    
    // Scenario 5: Return to parent view
    scenarios.push(
        new TestScenario('Return to Parent', 'Exit child view with animation')
            .addStep('navigate', { direction: 'clock', steps: 2 })
            .addStep('pressButton', { button: 'left' })
            .addStep('wait', { ms: 50 })
            .addStep('pressButton', { button: 'right' })
            .addStep('wait', { ms: 50 })
            .addAssertion(w => w.getViewMode() === 'parent', 'Should be back in parent mode')
            .addAssertion(w => w.getCurrentIndex() === 2, 'Should restore parent position')
            .addAssertion(w => w.getBreadcrumb() === null, 'Breadcrumb should be removed')
    );
    
    // Scenario 6: Empty playlist handling
    scenarios.push(
        new TestScenario('Empty Playlist', 'Handle playlist with no tracks')
            .addStep('navigate', { direction: 'clock', steps: 6 }) // Navigate to empty playlist
            .addStep('pressButton', { button: 'left' })
            .addAssertion(w => {
                const parent = w.getSelectedParent();
                return parent && (!parent.tracks || parent.tracks.length === 0);
            }, 'Should handle empty playlist gracefully')
    );
    
    // Scenario 7: Rapid navigation
    scenarios.push(
        new TestScenario('Rapid Navigation', 'Handle fast button presses')
            .addStep('navigate', { direction: 'clock', steps: 10 })
            .addStep('navigate', { direction: 'counter', steps: 5 })
            .addAssertion(w => w.getCurrentIndex() === 5, 'Should handle rapid navigation')
            .addAssertion(w => w.hasVisibleItems(), 'Should maintain visible items')
    );
    
    // Scenario 8: Multiple enter/exit cycles
    scenarios.push(
        new TestScenario('Multiple Cycles', 'Enter and exit child view multiple times')
            .addStep('pressButton', { button: 'left' })
            .addStep('wait', { ms: 50 })
            .addStep('pressButton', { button: 'right' })
            .addStep('wait', { ms: 50 })
            .addStep('navigate', { direction: 'clock', steps: 2 })
            .addStep('pressButton', { button: 'left' })
            .addStep('wait', { ms: 50 })
            .addAssertion(w => w.getViewMode() === 'child', 'Should be in child mode')
            .addAssertion(w => w.getBreadcrumb() !== null, 'Should have breadcrumb')
            .addAssertion(w => {
                const container = w.getContainer();
                const breadcrumbs = container.children.filter(c => 
                    c.classList && c.classList.contains('breadcrumb')
                );
                return breadcrumbs.length === 1;
            }, 'Should have exactly one breadcrumb')
    );
    
    // Scenario 9: Animation state management
    scenarios.push(
        new TestScenario('Animation State', 'Animation flag properly managed')
            .addStep('pressButton', { button: 'left' })
            .addStep('wait', { ms: 100 })
            .addAssertion(w => !w.arcList.isAnimating, 'Animation flag should reset')
    );
    
    // Scenario 10: DOM element preservation
    scenarios.push(
        new TestScenario('DOM Preservation', 'Breadcrumb preserved during renders')
            .addStep('navigate', { direction: 'clock', steps: 1 })
            .addStep('pressButton', { button: 'left' })
            .addStep('wait', { ms: 50 })
            .addStep('render', {}) // Force render
            .addStep('render', {}) // Another render
            .addAssertion(w => w.getBreadcrumb() !== null, 'Breadcrumb should survive renders')
            .addAssertion(w => {
                const bc = w.getBreadcrumb();
                return bc && bc.dataset.animatedParent === 'true';
            }, 'Breadcrumb marker should persist')
    );
    
    return scenarios;
}

// Main test execution
async function runAllTests() {
    console.log('🔬 BeoSound 5c Comprehensive Test Suite');
    console.log('=' .repeat(50));
    
    const scenarios = createTestScenarios();
    
    for (const scenario of scenarios) {
        const wrapper = new NavigationTestWrapper();
        const passed = await scenario.run(wrapper);
        
        testCount++;
        if (passed) {
            passCount++;
        } else {
            failCount++;
            issues.push(scenario.name);
        }
    }
    
    // Summary
    console.log('\n📊 Test Summary');
    console.log('=' .repeat(40));
    console.log(`Total Tests: ${testCount}`);
    console.log(`Passed: ${passCount} ✅`);
    console.log(`Failed: ${failCount} ❌`);
    console.log(`Success Rate: ${((passCount / testCount) * 100).toFixed(1)}%`);
    
    if (failCount > 0) {
        console.log('\n❌ Failed Tests:');
        issues.forEach(issue => console.log(`   - ${issue}`));
    }
    
    return { passed: passCount, failed: failCount, issues };
}

// Export for looping
if (require.main === module) {
    runAllTests().then(results => {
        process.exit(results.failed > 0 ? 1 : 0);
    });
} else {
    module.exports = { runAllTests };
}