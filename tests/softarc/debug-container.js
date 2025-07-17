#!/usr/bin/env node

/**
 * Debug container and breadcrumb issue
 */

const { 
    MockDOM, 
    MockElement, 
    loadArcListScript,
    loadTestData 
} = require('./test-softarc-navigation.js');

// Set up environment
const mockDocument = new MockDOM();
global.document = mockDocument;

// Override createElement to use MockElement
const originalCreateElement = global.document.createElement;
global.document.createElement = function(tagName) {
    console.log(`📦 document.createElement called for: ${tagName}`);
    const element = new MockElement(tagName);
    return element;
};

// Create container
const container = new MockElement('div');
container.id = 'arc-container';
mockDocument.elements.set('arc-container', container);

// Load ArcList
const ArcList = loadArcListScript();
const arcList = new ArcList({
    dataSource: '../json/playlists_with_tracks.json',
    dataType: 'parent_child',
    viewMode: 'hierarchical'
});

// Test breadcrumb creation
console.log('\n🧪 Testing breadcrumb creation...\n');

// Manually call createBreadcrumbElement
arcList.selectedParent = { name: 'Test Playlist', id: 'test-id' };
arcList.createBreadcrumbElement();

console.log('Container children after createBreadcrumbElement:', container.children.length);
console.log('Container children details:');
container.children.forEach((child, i) => {
    console.log(`  [${i}] Classes: ${Array.from(child._classes || []).join(', ')}, className: ${child.className}`);
});

// Try querySelector
console.log('\n🔍 Testing querySelector...');
const breadcrumb1 = container.querySelector('.breadcrumb');
console.log('querySelector(.breadcrumb):', breadcrumb1 ? 'FOUND' : 'NOT FOUND');

const breadcrumb2 = container.querySelector('.arc-item.breadcrumb');
console.log('querySelector(.arc-item.breadcrumb):', breadcrumb2 ? 'FOUND' : 'NOT FOUND');

// Direct check
console.log('\n🔍 Direct check of children...');
const hasBreadcrumb = container.children.some(child => {
    console.log(`  Checking child: _classes=${Array.from(child._classes || []).join(',')}, className="${child.className}"`);
    return child.className && child.className.includes('breadcrumb');
});
console.log('Has breadcrumb via direct check:', hasBreadcrumb);