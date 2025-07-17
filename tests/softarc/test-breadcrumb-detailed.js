#!/usr/bin/env node

/**
 * Detailed breadcrumb test with extensive logging
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

// Mock animateHierarchyTransition to not actually wait
global.animateHierarchyTransition = async () => {
    console.log('🎭 Mock animateHierarchyTransition called');
    return Promise.resolve();
};

// Create container
const container = new MockElement('div');
container.id = 'arc-container';
mockDocument.elements.set('arc-container', container);

// Create hierarchy background
const hierarchyBg = new MockElement('div');
hierarchyBg.id = 'hierarchy-background';
mockDocument.elements.set('hierarchy-background', hierarchyBg);

// Load ArcList
console.log('🔄 Loading ArcList class...');
const ArcList = loadArcListScript();
if (!ArcList) {
    console.error('❌ Failed to load ArcList');
    process.exit(1);
}

// Override some methods to add logging
const originalPerformEnhanced = ArcList.prototype.performEnhancedChildTransition;
ArcList.prototype.performEnhancedChildTransition = async function(selectedElement) {
    console.log('🎯 performEnhancedChildTransition called with:', selectedElement);
    const result = await originalPerformEnhanced.call(this, selectedElement);
    console.log('🎯 performEnhancedChildTransition completed');
    return result;
};

const originalLoadParentChildren = ArcList.prototype.loadParentChildren;
ArcList.prototype.loadParentChildren = function() {
    console.log('📚 loadParentChildren called');
    const result = originalLoadParentChildren.call(this);
    console.log('📚 loadParentChildren completed, viewMode:', this.viewMode);
    return result;
};

// Create instance with hierarchical mode
console.log('🏗️ Creating ArcList instance...');
const arcList = new ArcList({
    dataSource: '../json/playlists_with_tracks.json',
    dataType: 'parent_child',
    viewMode: 'hierarchical',
    parentKey: 'tracks'
});

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

// Initialize with test data
const testData = loadTestData();
arcList.parentData = testData.filter(p => p.tracks && p.tracks.length > 0);
arcList.items = arcList.parentData.slice(0, 5).map((p, i) => ({
    id: p.id,
    name: p.name || `Item ${i}`,
    image: p.image
}));

console.log('📋 Initialized with', arcList.items.length, 'items');

// Mock querySelector
const originalQuerySelector = document.querySelector.bind(document);
document.querySelector = function(selector) {
    console.log(`🔍 querySelector: "${selector}"`);
    
    if (selector === '.arc-item.selected' || selector === '.arc-item[data-index="1"]') {
        // Create a proper selected element that matches what would be in the DOM
        const selected = new MockElement('div');
        selected.classList.add('arc-item');
        selected.classList.add('selected');
        selected.dataset.index = '1';
        selected.dataset.parentIndex = '1';
        selected.textContent = arcList.items[1].name;
        
        // Make sure it's in the container
        if (!container.children.includes(selected)) {
            container.appendChild(selected);
        }
        
        console.log('   ✅ Returning selected element:', selected.textContent);
        return selected;
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
    
    return originalQuerySelector(selector);
};

// Set up initial state
arcList.viewMode = 'parent';
arcList.currentIndex = 1;

// Render initial view
console.log('\n🎨 Initial render...');
arcList.render();

console.log('\n📱 Simulating left button press...');

// Use async function to handle the button press
(async () => {
    try {
        await arcList.handleButtonFromParent('left');
        
        // Wait a bit for any async operations
        await new Promise(resolve => setTimeout(resolve, 100));
        
        console.log('\n📊 Final state:');
        console.log('   View mode:', arcList.viewMode);
        console.log('   Current index:', arcList.currentIndex);
        console.log('   Container children:', container.children.length);
        
        // Check for breadcrumb
        const breadcrumb = document.querySelector('.arc-item.breadcrumb');
        console.log('   Breadcrumb exists:', !!breadcrumb);
        
        if (breadcrumb) {
            console.log('   Breadcrumb classes:', Array.from(breadcrumb._classes || []).join(', '));
            console.log('   Breadcrumb dataset:', JSON.stringify(breadcrumb.dataset));
        }
        
        // Check container children
        console.log('\n🏗️ Container structure:');
        container.children.forEach((child, i) => {
            const classes = Array.from(child._classes || []).join(', ');
            console.log(`   [${i}] ${classes} - ${child.textContent}`);
        });
        
    } catch (error) {
        console.error('❌ Error:', error);
    }
})();