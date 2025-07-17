#!/usr/bin/env node

/**
 * Quick test to verify current state
 */

const { execSync } = require('child_process');

// Test 1: Basic loading
console.log('🧪 Test 1: Basic script loading...');
try {
    execSync('node -e "require(\'./test-softarc-navigation.js\')"', { cwd: __dirname });
    console.log('✅ Script loads without syntax errors');
} catch (e) {
    console.log('❌ Syntax error in test-softarc-navigation.js');
    process.exit(1);
}

// Test 2: Can we create DOM elements?
console.log('\n🧪 Test 2: Mock DOM functionality...');
try {
    const { MockDOM, MockElement } = require('./test-softarc-navigation.js');
    const dom = new MockDOM();
    const el = new MockElement('div');
    el.id = 'test';
    dom.elements.set('test', el);
    
    console.log('✅ Mock DOM creates elements');
} catch (e) {
    console.log('❌ Mock DOM error:', e.message);
}

// Test 3: Can we load ArcList?
console.log('\n🧪 Test 3: ArcList loading...');
try {
    const { loadArcListScript } = require('./test-softarc-navigation.js');
    const ArcList = loadArcListScript();
    if (ArcList) {
        console.log('✅ ArcList class loads successfully');
    } else {
        console.log('❌ ArcList class failed to load');
    }
} catch (e) {
    console.log('❌ ArcList loading error:', e.message);
}

// Test 4: querySelector binding
console.log('\n🧪 Test 4: querySelector binding...');
try {
    const { MockDOM } = require('./test-softarc-navigation.js');
    const dom = new MockDOM();
    
    // Set up global
    global.document = dom;
    global.document.querySelector = dom.querySelector ? dom.querySelector.bind(dom) : null;
    
    if (typeof global.document.querySelector === 'function') {
        console.log('✅ querySelector is properly bound');
    } else {
        console.log('❌ querySelector is not a function');
    }
} catch (e) {
    console.log('❌ querySelector binding error:', e.message);
}

// Test 5: Navigation functionality
console.log('\n🧪 Test 5: Navigation index updates...');
try {
    const { loadArcListScript, MockDOM, loadTestData } = require('./test-softarc-navigation.js');
    const ArcList = loadArcListScript();
    
    global.document = new MockDOM();
    global.window = { addEventListener: () => {} };
    global.localStorage = { getItem: () => null, setItem: () => {} };
    
    const arcList = new ArcList({
        dataSource: '../json/playlists_with_tracks.json',
        dataType: 'parent_child'
    });
    
    arcList.items = [
        { id: '1', name: 'Item 1' },
        { id: '2', name: 'Item 2' },
        { id: '3', name: 'Item 3' }
    ];
    
    arcList.currentIndex = 0;
    arcList.targetIndex = 0;
    
    // Test navigation
    arcList.handleNavFromParent({ direction: 'clock', speed: 20 });
    
    if (arcList.targetIndex === 1 || arcList.currentIndex === 1) {
        console.log('✅ Navigation updates indices');
    } else {
        console.log('❌ Navigation does not update indices');
        console.log('   currentIndex:', arcList.currentIndex);
        console.log('   targetIndex:', arcList.targetIndex);
    }
} catch (e) {
    console.log('❌ Navigation test error:', e.message);
}

console.log('\n✅ Quick tests complete');