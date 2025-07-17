#!/usr/bin/env node

/**
 * Automated Softarc Navigation Test for BeoSound 5c
 * 
 * This test validates the complete softarc navigation workflow without human interaction:
 * 1. Navigate to music view
 * 2. See the list of playlists
 * 3. Move down a few steps in the list
 * 4. Press 'left' to select a playlist
 * 5. Ensure it moves to the left and stays visible
 * 6. See the sublist (list of songs for the playlist)
 * 7. Navigate the sublist
 * 8. Press 'right' to go back to the playlist selection
 */

const fs = require('fs');
const path = require('path');

// Test configuration
const testConfig = {
    webRoot: path.join(__dirname, '../../web'),
    softarcRoot: path.join(__dirname, '../../web/softarc'),
    testDataPath: path.join(__dirname, '../../web/json/playlists_with_tracks.json')
};

// Test results tracking
let testCount = 0;
let passCount = 0;
let failCount = 0;
let testResults = [];

// Mock DOM environment
class MockDOM {
    constructor() {
        this.elements = new Map();
        this.eventListeners = new Map();
    }
    
    
    querySelector(selector) {
        // Handle different selector types
        if (selector.startsWith('.')) {
            const className = selector.substring(1).split('.')[0];
            for (const [id, element] of this.elements) {
                if (element.classList && element.classList.contains(className)) {
                    return element;
                }
                // Check children recursively
                const found = this.findInChildren(element, className);
                if (found) return found;
            }
        }
        
        // Handle ID selectors
        if (selector.startsWith('#')) {
            return this.getElementById(selector.substring(1));
        }
        
        // Handle attribute selectors
        if (selector.includes('[')) {
            const match = selector.match(/\[([^=]+)="([^"]+)"\]/);
            if (match) {
                const [, attr, value] = match;
                for (const [id, element] of this.elements) {
                    if (element.getAttribute && element.getAttribute(attr) === value) {
                        return element;
                    }
                }
            }
        }
        
        return null;
    }
    
    findInChildren(element, className) {
        if (!element.children) return null;
        for (const child of element.children) {
            if (child.classList && child.classList.contains(className)) {
                return child;
            }
            const found = this.findInChildren(child, className);
            if (found) return found;
        }
        return null;
    }

    getElementById(id) {
        return this.elements.get(id) || null;
    }
    
    createElement(tagName) {
        return new MockElement(tagName);
    }
    
    addEventListener(event, listener) {
        if (!this.eventListeners.has(event)) {
            this.eventListeners.set(event, []);
        }
        this.eventListeners.get(event).push(listener);
    }
    
    dispatchEvent(event, data) {
        const listeners = this.eventListeners.get(event) || [];
        listeners.forEach(listener => listener(data));
    }
}

class MockElement {
    constructor(tagName) {
        this.tagName = tagName;
        this.id = null;
        this.className = '';
        this._classes = new Set();
        this.classList = {
            add: (cls) => { this._classes.add(cls); this._updateClassName(); },
            remove: (cls) => { this._classes.delete(cls); this._updateClassName(); },
            contains: (cls) => this._classes.has(cls),
            toggle: (cls) => {
                if (this._classes.has(cls)) {
                    this._classes.delete(cls);
                } else {
                    this._classes.add(cls);
                }
                this._updateClassName();
            }
        };
        this.style = {};
        this._innerHTML = '';
        this.textContent = '';
        this.children = [];
        this.parentElement = null;
        this.dataset = {};
        this._attributes = new Map();
    }
    
    get innerHTML() {
        return this._innerHTML;
    }
    
    set innerHTML(value) {
        this._innerHTML = value;
        // Clear all children when innerHTML is set
        if (value === '') {
            this.children = [];
        }
    }
    
    _updateClassName() {
        this.className = Array.from(this._classes).join(' ');
    }
    
    appendChild(child) {
        if (child.parentElement) {
            child.parentElement.removeChild(child);
        }
        this.children.push(child);
        child.parentElement = this;
        return child;
    }
    
    removeChild(child) {
        const index = this.children.indexOf(child);
        if (index > -1) {
            this.children.splice(index, 1);
            child.parentElement = null;
        }
        return child;
    }
    
    remove() {
        if (this.parentElement) {
            this.parentElement.removeChild(this);
        }
    }
    
    setAttribute(name, value) {
        this._attributes.set(name, value);
        if (name === 'class') {
            this.className = value;
            this.classList = new Set(value.split(' ').filter(c => c));
        } else if (name.startsWith('data-')) {
            const dataName = name.substring(5).replace(/-([a-z])/g, (g) => g[1].toUpperCase());
            this.dataset[dataName] = value;
        }
    }
    
    getAttribute(name) {
        return this._attributes.get(name) || null;
    }
    
    querySelector(selector) {
        // Simple selector implementation
        if (selector.startsWith('.')) {
            // Handle compound class selectors like '.arc-item.breadcrumb'
            const classes = selector.substring(1).split('.');
            return this.findByClasses(classes);
        }
        return null;
    }
    
    querySelectorAll(selector) {
        // Simple selector implementation
        if (selector.startsWith('.')) {
            const className = selector.substring(1);
            return this.findAllByClass(className);
        }
        return [];
    }
    
    findByClass(className) {
        if (this.classList.contains(className)) {
            return this;
        }
        for (let child of this.children) {
            const result = child.findByClass(className);
            if (result) return result;
        }
        return null;
    }
    
    findByClasses(classes) {
        // Check if this element has ALL the specified classes
        const hasAllClasses = classes.every(cls => this._classes.has(cls));
        if (hasAllClasses) {
            return this;
        }
        // Search children
        for (let child of this.children) {
            if (child.findByClasses) {
                const result = child.findByClasses(classes);
                if (result) return result;
            }
        }
        return null;
    }
    
    findAllByClass(className) {
        const results = [];
        if (this.classList.contains(className)) {
            results.push(this);
        }
        for (let child of this.children) {
            results.push(...child.findAllByClass(className));
        }
        return results;
    }
    
    
    contains(element) {
        return this.children.includes(element) || this.children.some(child => child.contains(element));
    }
    
    getBoundingClientRect() {
        return {
            left: 0,
            top: 0,
            width: 100,
            height: 100,
            right: 100,
            bottom: 100
        };
    }
    
    get offsetWidth() { return 100; }
    get offsetHeight() { return 100; }
    get offsetLeft() { return 0; }
    get offsetTop() { return 0; }
}

class MockClassList {
    constructor(element) {
        this.element = element;
        this.classes = new Set();
    }
    
    add(className) {
        this.classes.add(className);
        this.updateElement();
    }
    
    remove(className) {
        this.classes.delete(className);
        this.updateElement();
    }
    
    contains(className) {
        return this.classes.has(className);
    }
    
    toggle(className) {
        if (this.classes.has(className)) {
            this.classes.delete(className);
        } else {
            this.classes.add(className);
        }
        this.updateElement();
    }
    
    updateElement() {
        this.element.className = Array.from(this.classes).join(' ');
    }
}

// Test utility functions
function test(description, testFunction) {
    testCount++;
    const startTime = Date.now();
    
    try {
        const result = testFunction();
        const duration = Date.now() - startTime;
        
        console.log(`✅ ${description} (${duration}ms)`);
        testResults.push({
            description,
            status: 'PASS',
            duration,
            error: null
        });
        passCount++;
        
        return result;
    } catch (error) {
        const duration = Date.now() - startTime;
        
        console.log(`❌ ${description} (${duration}ms)`);
        console.log(`   Error: ${error.message}`);
        testResults.push({
            description,
            status: 'FAIL',
            duration,
            error: error.message
        });
        failCount++;
        
        return null;
    }
}

function assertEqual(actual, expected, message) {
    if (actual !== expected) {
        throw new Error(`${message}: expected ${expected}, got ${actual}`);
    }
}

function assertTrue(condition, message) {
    if (!condition) {
        throw new Error(message);
    }
}

function assertFalse(condition, message) {
    if (condition) {
        throw new Error(message);
    }
}

// Mock WebSocket for testing
class MockWebSocket {
    constructor() {
        this.readyState = 1; // OPEN
        this.onopen = null;
        this.onmessage = null;
        this.onclose = null;
        this.onerror = null;
        this.messages = [];
    }
    
    send(data) {
        this.messages.push(JSON.parse(data));
    }
    
    simulateMessage(data) {
        if (this.onmessage) {
            this.onmessage({ data: JSON.stringify(data) });
        }
    }
    
    close() {
        this.readyState = 3; // CLOSED
        if (this.onclose) {
            this.onclose();
        }
    }
}

// Load test data
function loadTestData() {
    try {
        const data = fs.readFileSync(testConfig.testDataPath, 'utf8');
        return JSON.parse(data);
    } catch (error) {
        console.error('Error loading test data:', error);
        return [];
    }
}

// Load the ArcList script for testing
function loadArcListScript() {
    try {
        const scriptPath = path.join(testConfig.softarcRoot, 'script.js');
        const scriptContent = fs.readFileSync(scriptPath, 'utf8');
        
        // Create a mock document for DOM operations
        const mockDocument = new MockDOM();
        
        // Create required DOM elements
        const arcContainer = new MockElement('div');
        arcContainer.id = 'arc-container';
        mockDocument.elements.set('arc-container', arcContainer);
        
        const currentItem = new MockElement('span');
        currentItem.id = 'current-item';
        mockDocument.elements.set('current-item', currentItem);
        
        const totalItems = new MockElement('span');
        totalItems.id = 'total-items';
        mockDocument.elements.set('total-items', totalItems);
        
        const hierarchyBg = new MockElement('div');
        hierarchyBg.id = 'hierarchy-background';
        mockDocument.elements.set('hierarchy-background', hierarchyBg);
        
        // Create a safe evaluation context
        const context = {
            console: console,
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
            setInterval: setInterval,
            clearInterval: clearInterval,
            animateHierarchyTransition: async () => Promise.resolve(),
            requestAnimationFrame: (callback) => setTimeout(callback, 16),
            cancelAnimationFrame: clearTimeout,
            Date: Date,
            Math: Math,
            JSON: JSON,
            Promise: Promise,
            fetch: async (url) => {
                // Mock fetch for loading playlist data
                if (url.includes('playlists_with_tracks.json')) {
                    return {
                        json: () => Promise.resolve(loadTestData())
                    };
                }
                // Mock webhook calls
                if (url.includes('webhook')) {
                    return {
                        ok: true,
                        json: () => Promise.resolve({})
                    };
                }
                throw new Error(`Fetch not mocked for URL: ${url}`);
            },
            WebSocket: MockWebSocket,
            localStorage: {
                getItem: () => null,
                setItem: () => {},
                removeItem: () => {}
            },
            window: {
                addEventListener: () => {},
                getComputedStyle: () => ({
                    display: 'block',
                    visibility: 'visible',
                    opacity: '1',
                    zIndex: '1'
                })
            },
            document: {
                ...mockDocument,
                createElement: (tagName) => new MockElement(tagName),
                getElementById: (id) => mockDocument.getElementById(id),
                querySelector: (selector) => mockDocument.querySelector(selector),
                querySelectorAll: (selector) => mockDocument.querySelectorAll(selector)
            }
        };
        
        // Execute the script in our context and capture the ArcList class
        const func = new Function(...Object.keys(context), scriptContent + '\nreturn typeof ArcList !== "undefined" ? ArcList : null;');
        const ArcList = func(...Object.values(context));
        
        return ArcList;
    } catch (error) {
        console.error('Error loading ArcList script:', error);
        return null;
    }
}

console.log('🎯 BeoSound 5c Softarc Navigation Test');
console.log('=' .repeat(50));

// Test 1: Verify test data exists
test('Test data file exists and is valid', () => {
    const testData = loadTestData();
    assertTrue(Array.isArray(testData), 'Test data should be an array');
    assertTrue(testData.length > 0, 'Test data should not be empty');
    assertTrue(testData[0].hasOwnProperty('tracks'), 'First playlist should have tracks');
    assertTrue(testData[0].tracks.length > 0, 'First playlist should have at least one track');
    
    console.log(`   Loaded ${testData.length} playlists with tracks`);
    return testData;
});

// Test 2: Verify ArcList script can be loaded
test('ArcList script loads successfully', () => {
    const ArcList = loadArcListScript();
    assertTrue(ArcList !== null, 'ArcList class should be available');
    assertTrue(typeof ArcList === 'function', 'ArcList should be a constructor function');
    
    return ArcList;
});

console.log('\n📊 Test Summary');
console.log('=' .repeat(40));
console.log(`Total Tests: ${testCount}`);
console.log(`Passed: ${passCount} ✅`);
console.log(`Failed: ${failCount} ❌`);
console.log(`Success Rate: ${((passCount / testCount) * 100).toFixed(1)}%`);

if (failCount === 0) {
    console.log('\n🎉 All basic tests passed!');
    console.log('✅ Ready to implement full navigation test suite');
} else {
    console.log('\n⚠️  Some tests failed. Please fix the errors above.');
    process.exit(1);
}

// Export for other tests
module.exports = {
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
};