#!/usr/bin/env node

/**
 * Fix mockDocument reference errors in test files
 */

const fs = require('fs');
const path = require('path');

const files = [
    'test-complete-navigation.js',
    'test-all-scenarios.js',
    'test-navigation-edge-cases.js'
];

files.forEach(file => {
    const filePath = path.join(__dirname, file);
    
    if (!fs.existsSync(filePath)) {
        console.log(`⚠️  ${file} not found`);
        return;
    }
    
    let content = fs.readFileSync(filePath, 'utf8');
    let modified = false;
    
    // Fix mockDocument references to use this.mockDocument
    const patterns = [
        {
            // Fix createSelectedElement references
            pattern: /const selected = mockDocument\.createElement/g,
            replacement: 'const selected = this.mockDocument.createElement'
        },
        {
            // Fix container references
            pattern: /const container = mockDocument\.getElementById/g,
            replacement: 'const container = this.mockDocument.getElementById'
        },
        {
            // Fix querySelector references in createSelectedElement
            pattern: /container\.querySelector\(/g,
            replacement: 'this.mockDocument.querySelector('
        }
    ];
    
    patterns.forEach(({pattern, replacement}) => {
        if (content.match(pattern)) {
            content = content.replace(pattern, replacement);
            modified = true;
            console.log(`✅ Fixed ${pattern.source} in ${file}`);
        }
    });
    
    // Also ensure arcList references use this.arcList where appropriate
    const arcListPattern = /arcList\.currentIndex/g;
    if (content.match(arcListPattern) && content.includes('class')) {
        content = content.replace(arcListPattern, 'this.arcList.currentIndex');
        modified = true;
        console.log(`✅ Fixed arcList references in ${file}`);
    }
    
    if (modified) {
        fs.writeFileSync(filePath, content, 'utf8');
        console.log(`💾 Saved ${file}`);
    } else {
        console.log(`⏭️  No changes needed for ${file}`);
    }
});

console.log('\n✅ MockDocument reference fixes applied!');