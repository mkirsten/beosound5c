#!/usr/bin/env node

/**
 * Fix breadcrumb test infrastructure issues
 */

const fs = require('fs');
const path = require('path');

// Files to update
const files = [
    'test-all-scenarios.js',
    'test-complete-navigation.js',
    'test-breadcrumb-animation.js',
    'test-real-world-bugs.js'
];

// Fix 1: Add proper selected element creation
const selectedElementFix = `
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
`;

// Fix 2: Improve querySelector mock
const querySelectorFix = `
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
`;

// Fix 3: Add animateHierarchyTransition to global context
const animateFix = `
        // Mock animateHierarchyTransition
        global.animateHierarchyTransition = async () => Promise.resolve();
`;

// Apply fixes to each file
files.forEach(file => {
    const filePath = path.join(__dirname, file);
    
    if (!fs.existsSync(filePath)) {
        console.log(`⚠️  Skipping ${file} - file not found`);
        return;
    }
    
    let content = fs.readFileSync(filePath, 'utf8');
    let modified = false;
    
    // Apply selected element fix
    if (!content.includes('createSelectedElement')) {
        // Find a good insertion point (after arcList creation)
        const insertPoint = content.indexOf('arcList.handleButtonFromParent');
        if (insertPoint > -1) {
            const lineStart = content.lastIndexOf('\n', insertPoint);
            content = content.substring(0, lineStart) + selectedElementFix + content.substring(lineStart);
            modified = true;
            console.log(`✅ Added createSelectedElement to ${file}`);
        }
    }
    
    // Apply querySelector fix
    if (!content.includes('Enhanced querySelector mock') && content.includes('mockDocument')) {
        const insertPoint = content.indexOf('mockDocument.querySelector');
        if (insertPoint > -1) {
            const lineStart = content.lastIndexOf('\n', insertPoint);
            content = content.substring(0, lineStart) + querySelectorFix + content.substring(insertPoint);
            modified = true;
            console.log(`✅ Enhanced querySelector in ${file}`);
        }
    }
    
    // Apply animate fix
    if (!content.includes('global.animateHierarchyTransition')) {
        const insertPoint = content.indexOf('global.document');
        if (insertPoint > -1) {
            const lineEnd = content.indexOf('\n', insertPoint);
            content = content.substring(0, lineEnd + 1) + animateFix + content.substring(lineEnd + 1);
            modified = true;
            console.log(`✅ Added animateHierarchyTransition mock to ${file}`);
        }
    }
    
    if (modified) {
        fs.writeFileSync(filePath, content, 'utf8');
        console.log(`💾 Saved ${file}`);
    } else {
        console.log(`⏭️  No changes needed for ${file}`);
    }
});

console.log('\n✅ Breadcrumb test fixes applied!');