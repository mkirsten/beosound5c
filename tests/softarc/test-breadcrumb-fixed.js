#!/usr/bin/env node

/**
 * Fixed breadcrumb test - properly sets up selected element
 */

const { 
    MockDOM, 
    MockElement, 
    loadArcListScript,
    loadTestData 
} = require('./test-softarc-navigation.js');

// Create mock environment
const mockDocument = new MockDOM();
global.document = mockDocument;
global.window = { 
    addEventListener: () => {},
    getComputedStyle: () => ({
        display: 'block',
        visibility: 'visible',
        opacity: '1'
    })
};
global.localStorage = { getItem: () => null, setItem: () => {} };

// Create container
const container = new MockElement('div');
container.id = 'arc-container';
mockDocument.elements.set('arc-container', container);

// Create other required elements
const hierarchyBg = new MockElement('div');
hierarchyBg.id = 'hierarchy-background';
mockDocument.elements.set('hierarchy-background', hierarchyBg);

// Create stats elements
const currentItem = new MockElement('span');
currentItem.id = 'current-item';
mockDocument.elements.set('current-item', currentItem);

const totalItems = new MockElement('span');
totalItems.id = 'total-items';
mockDocument.elements.set('total-items', totalItems);

// Pre-create a selected element that will be in the DOM
let selectedElement = null;

// Mock querySelector
const originalQuerySelector = document.querySelector.bind(document);
document.querySelector = function(selector) {
    console.log(`🔍 querySelector: "${selector}"`);
    
    if (selector === '.arc-item.selected') {
        if (selectedElement && container.children.includes(selectedElement)) {
            console.log('   ✅ Returning pre-created selected element');
            return selectedElement;
        }
        console.log('   ❌ No selected element in container');
        return null;
    }
    
    if (selector === '.arc-item.breadcrumb') {
        // Look for breadcrumb in container
        for (const child of container.children) {
            if (child.classList && child.classList.contains('breadcrumb')) {
                console.log('   ✅ Found breadcrumb');
                return child;
            }
        }
        console.log('   ❌ No breadcrumb found');
        return null;
    }
    
    if (selector === '#arc-container') {
        return container;
    }
    
    return originalQuerySelector(selector);
};

// Load ArcList
console.log('🔄 Loading ArcList class...');
const ArcList = loadArcListScript();
if (!ArcList) {
    console.error('❌ Failed to load ArcList');
    process.exit(1);
}

// Mock fetch
global.fetch = async (url) => {
    if (url.includes('playlists_with_tracks.json')) {
        return { json: () => Promise.resolve(loadTestData()) };
    }
    if (url.includes('webhook')) {
        return { ok: true, json: () => Promise.resolve({}) };
    }
    throw new Error(`Fetch not mocked for URL: ${url}`);
};

// Override render to properly create selected element
const originalRender = ArcList.prototype.render;
ArcList.prototype.render = function() {
    console.log('🎨 Custom render called, viewMode:', this.viewMode);
    
    if (this.viewMode === 'parent') {
        // Clear and recreate container content
        container.children = [];
        
        // Create visible items
        const visibleItems = this.getVisibleItems();
        visibleItems.forEach((item, i) => {
            const el = new MockElement('div');
            el.classList.add('arc-item');
            el.dataset.index = String(i);
            el.textContent = item.name;
            
            // Mark the current item as selected
            if (i === Math.round(this.currentIndex - this.startIndex)) {
                el.classList.add('selected');
                selectedElement = el; // Store reference
                console.log('   🎯 Created selected element:', item.name);
            }
            
            container.appendChild(el);
        });
        
        console.log('   📦 Container now has', container.children.length, 'children');
    } else {
        // Call original render for child mode
        originalRender.call(this);
    }
};

// Create instance
console.log('🏗️ Creating ArcList instance...');
const arcList = new ArcList({
    dataSource: '../json/playlists_with_tracks.json',
    dataType: 'parent_child',
    viewMode: 'hierarchical',
    parentKey: 'tracks'
});

// Initialize with test data
const testData = loadTestData();
arcList.parentData = testData.filter(p => p.tracks && p.tracks.length > 0);
arcList.items = arcList.parentData.slice(0, 5).map((p, i) => ({
    id: p.id,
    name: p.name || `Item ${i}`,
    image: p.image
}));

console.log('📋 Initialized with', arcList.items.length, 'items');

// Set up initial state
arcList.viewMode = 'parent';
arcList.currentIndex = 1;
arcList.startIndex = 0;

// Initial render
console.log('\n🎨 Initial render...');
arcList.render();

// Verify selected element exists
console.log('\n✅ Verifying setup:');
const checkSelected = document.querySelector('.arc-item.selected');
console.log('   Selected element exists:', !!checkSelected);
if (checkSelected) {
    console.log('   Selected text:', checkSelected.textContent);
}

console.log('\n📱 Simulating left button press...');

// Handle button press
(async () => {
    try {
        await arcList.handleButtonFromParent('left');
        
        // Wait for any async operations
        await new Promise(resolve => setTimeout(resolve, 100));
        
        console.log('\n📊 Final state:');
        console.log('   View mode:', arcList.viewMode);
        console.log('   Selected parent:', arcList.selectedParent ? arcList.selectedParent.name : 'None');
        
        // Check for breadcrumb
        const breadcrumb = document.querySelector('.arc-item.breadcrumb');
        console.log('   Breadcrumb exists:', !!breadcrumb);
        
        if (breadcrumb) {
            console.log('   ✅ BREADCRUMB FOUND!');
            console.log('   Breadcrumb classes:', Array.from(breadcrumb._classes || []).join(', '));
            console.log('   Breadcrumb text:', breadcrumb.textContent);
            console.log('   Has animatedParent marker:', breadcrumb.dataset.animatedParent === 'true');
        } else {
            console.log('   ❌ NO BREADCRUMB FOUND');
        }
        
        // Check container state
        console.log('\n🏗️ Container final state:');
        console.log('   Children count:', container.children.length);
        container.children.forEach((child, i) => {
            const classes = Array.from(child._classes || []).join(', ');
            console.log(`   [${i}] ${classes} - ${child.textContent}`);
        });
        
        // Success check
        const success = arcList.viewMode === 'child' && !!breadcrumb;
        console.log(`\n${success ? '✅ TEST PASSED' : '❌ TEST FAILED'}`);
        
    } catch (error) {
        console.error('❌ Error:', error);
        console.error(error.stack);
    }
})();