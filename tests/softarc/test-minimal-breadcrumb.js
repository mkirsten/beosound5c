#!/usr/bin/env node

/**
 * Minimal test to debug breadcrumb issue
 */

const { 
    MockDOM, 
    MockElement, 
    loadArcListScript,
    loadTestData 
} = require('./test-softarc-navigation.js');

// Create a proper mock environment
const mockDocument = new MockDOM();
global.document = mockDocument;
global.window = { addEventListener: () => {} };
global.localStorage = { getItem: () => null, setItem: () => {} };
global.fetch = async (url) => {
    if (url.includes('playlists_with_tracks.json')) {
        return { json: () => Promise.resolve(loadTestData()) };
    }
    throw new Error(`Fetch not mocked for URL: ${url}`);
};

// Mock querySelector to return elements when needed
const originalQuerySelector = mockDocument.querySelector.bind(mockDocument);
let breadcrumbElement = null; // Store breadcrumb element

mockDocument.querySelector = function(selector) {
    console.log(`🔍 querySelector called with: "${selector}"`);
    
    if (selector === '.arc-item.selected') {
        // Find the actual selected element in the container
        const container = mockDocument.getElementById('arc-container');
        if (container && container.children) {
            for (const child of container.children) {
                if (child.classList && child.classList.contains('selected')) {
                    console.log('✅ Found actual selected element');
                    return child;
                }
            }
        }
        
        // Fallback: Create a selected element
        const selected = new MockElement('div');
        selected.classList.add('arc-item');
        selected.classList.add('selected');
        selected.dataset.index = '1';
        selected.textContent = 'Selected Item';
        console.log('✅ Returning mock selected element');
        return selected;
    }
    
    if (selector === '.arc-item.breadcrumb') {
        // Return stored breadcrumb element if exists
        if (breadcrumbElement) {
            console.log('✅ Returning stored breadcrumb element');
            return breadcrumbElement;
        }
        
        // Check container for breadcrumb
        const container = mockDocument.getElementById('arc-container');
        if (container && container.children) {
            for (const child of container.children) {
                if (child.classList && child.classList.contains('breadcrumb')) {
                    breadcrumbElement = child;
                    console.log('✅ Found breadcrumb in container');
                    return child;
                }
            }
        }
        
        console.log('❌ No breadcrumb found');
        return null;
    }
    
    return originalQuerySelector(selector);
};

// Bind it properly
global.document.querySelector = mockDocument.querySelector.bind(mockDocument);

// Create container
const container = new MockElement('div');
container.id = 'arc-container';
mockDocument.elements.set('arc-container', container);

// Load ArcList
const ArcList = loadArcListScript();
if (!ArcList) {
    console.error('❌ Failed to load ArcList');
    process.exit(1);
}

// Create instance
const arcList = new ArcList({
    dataSource: '../json/playlists_with_tracks.json',
    dataType: 'parent_child',
    viewMode: 'hierarchical'
});

// Initialize with test data
const testData = loadTestData();
arcList.parentData = testData;

// Make sure we use playlists that have tracks
const playlistsWithTracks = testData.filter(p => p.tracks && p.tracks.length > 0);
console.log(`Found ${playlistsWithTracks.length} playlists with tracks`);

arcList.items = playlistsWithTracks.slice(0, 5).map((p, i) => ({
    id: p.id,
    name: p.name || `Item ${i}`,
    image: p.image
}));

console.log('\n🧪 Testing breadcrumb creation...\n');

// Set up in parent mode
arcList.viewMode = 'parent';
arcList.currentIndex = 1;

// Simulate left button press
console.log('📱 Simulating left button press...');
arcList.handleButtonFromParent('left');

// Check results after a delay
setTimeout(() => {
    console.log('\n📊 Checking results...');
    
    // Check view mode
    console.log(`View mode: ${arcList.viewMode}`);
    
    // Try to find breadcrumb
    const breadcrumb = mockDocument.querySelector('.arc-item.breadcrumb');
    console.log(`Breadcrumb found: ${breadcrumb ? 'YES' : 'NO'}`);
    
    if (breadcrumb) {
        console.log('Breadcrumb details:');
        console.log(`  - Classes: ${Array.from(breadcrumb._classes).join(', ')}`);
        console.log(`  - Dataset: ${JSON.stringify(breadcrumb.dataset)}`);
        console.log(`  - Text: ${breadcrumb.textContent}`);
    }
    
    // Check container children
    console.log(`\nContainer children: ${container.children.length}`);
    container.children.forEach((child, i) => {
        console.log(`  Child ${i}: ${Array.from(child._classes || []).join(', ')}`);
    });
    
    process.exit(0);
}, 100);