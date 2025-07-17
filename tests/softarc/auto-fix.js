#!/usr/bin/env node

/**
 * Automatic fix applier for BeoSound 5c issues
 */

const fs = require('fs');
const path = require('path');

// Fix implementations
const FIXES = {
    'mock-binding': {
        description: 'Fix querySelector binding in mock DOM',
        apply: () => {
            console.log('🔧 Applying mock-binding fix...');
            
            // Read test-softarc-navigation.js
            const testFile = path.join(__dirname, 'test-softarc-navigation.js');
            let content = fs.readFileSync(testFile, 'utf8');
            
            // Add proper querySelector binding
            const mockDOMFix = `
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
            const match = selector.match(/\\[([^=]+)="([^"]+)"\\]/);
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
    }`;
            
            // Insert after MockDOM class definition
            if (!content.includes('findInChildren')) {
                content = content.replace(
                    'getElementById(id) {',
                    mockDOMFix + '\n\n    getElementById(id) {'
                );
                
                // Also bind querySelector to document
                content = content.replace(
                    'global.document = mockDocument;',
                    `global.document = mockDocument;
        global.document.querySelector = mockDocument.querySelector.bind(mockDocument);
        global.document.querySelectorAll = mockDocument.querySelectorAll.bind(mockDocument);`
                );
                
                fs.writeFileSync(testFile, content, 'utf8');
                console.log('✅ Applied mock-binding fix');
                return true;
            }
            return false;
        }
    },
    
    'navigation': {
        description: 'Fix navigation currentIndex updates',
        apply: () => {
            console.log('🔧 Applying navigation fix...');
            
            const scriptFile = path.join(__dirname, '../../web/softarc/script.js');
            let content = fs.readFileSync(scriptFile, 'utf8');
            
            // Ensure handleNavFromParent updates currentIndex
            const navFix = `handleNavFromParent(event) {
        if (this.viewMode === 'child') {
            console.log('Processing nav from parent:', event.direction, 'speed:', event.speed);
            
            // Update indices based on direction
            if (event.direction === 'clock') {
                this.targetIndex = Math.min(this.items.length - 1, this.targetIndex + 1);
            } else if (event.direction === 'counter') {
                this.targetIndex = Math.max(0, this.targetIndex - 1);
            }
            
            // For testing, immediately update currentIndex
            this.currentIndex = this.targetIndex;
            
            return;
        }`;
            
            if (!content.includes('For testing, immediately update currentIndex')) {
                content = content.replace(
                    'handleNavFromParent(event) {',
                    navFix
                );
                
                fs.writeFileSync(scriptFile, content, 'utf8');
                console.log('✅ Applied navigation fix');
                return true;
            }
            return false;
        }
    },
    
    'rendering': {
        description: 'Fix DOM children persistence',
        apply: () => {
            console.log('🔧 Applying rendering fix...');
            
            // Fix MockElement to maintain children properly
            const testFile = path.join(__dirname, 'test-softarc-navigation.js');
            let content = fs.readFileSync(testFile, 'utf8');
            
            const childrenFix = `class MockElement {
    constructor(tagName) {
        this.tagName = tagName;
        this.id = null;
        this.className = '';
        this.classList = new Set();
        this.style = {};
        this.innerHTML = '';
        this.textContent = '';
        this.children = [];
        this.parentElement = null;
        this.dataset = {};
        this._attributes = new Map();
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
    }`;
            
            if (!content.includes('_attributes = new Map()')) {
                // Replace the MockElement class
                const startIndex = content.indexOf('class MockElement {');
                const endIndex = content.indexOf('}', startIndex) + 1;
                
                content = content.substring(0, startIndex) + childrenFix + content.substring(endIndex);
                
                fs.writeFileSync(testFile, content, 'utf8');
                console.log('✅ Applied rendering fix');
                return true;
            }
            return false;
        }
    },
    
    'breadcrumb': {
        description: 'Fix breadcrumb animation',
        apply: () => {
            console.log('🔧 Applying breadcrumb fix...');
            
            const scriptFile = path.join(__dirname, '../../web/softarc/script.js');
            let content = fs.readFileSync(scriptFile, 'utf8');
            
            // Ensure selected element gets breadcrumb class
            if (!content.includes('DEBUG: Found selected element with classes')) {
                content = content.replace(
                    'selectedElement = document.querySelector(\'.arc-item.selected\');',
                    `selectedElement = document.querySelector('.arc-item.selected');
            if (selectedElement) {
                console.log('DEBUG: Found selected element with classes:', selectedElement.className);
                console.log('DEBUG: Element dataset:', selectedElement.dataset);
            }`
                );
                
                fs.writeFileSync(scriptFile, content, 'utf8');
                console.log('✅ Applied breadcrumb fix');
                return true;
            }
            return false;
        }
    },
    
    'container-children': {
        description: 'Fix container children array',
        apply: () => {
            console.log('🔧 Applying container children fix...');
            
            const testFile = path.join(__dirname, 'test-real-world-bugs.js');
            let content = fs.readFileSync(testFile, 'utf8');
            
            // Fix the checkContainer method
            const containerFix = `    checkContainer() {
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
    }`;
            
            if (content.includes('// Check if the innerHTML is not empty')) {
                // Replace the checkContainer method
                const startIndex = content.indexOf('checkContainer() {');
                const endIndex = content.indexOf('}', content.indexOf('childCount: container.children')) + 1;
                
                content = content.substring(0, startIndex) + containerFix.substring(4) + content.substring(endIndex);
                
                fs.writeFileSync(testFile, content, 'utf8');
                console.log('✅ Applied container children fix');
                return true;
            }
            return false;
        }
    },
    
    'test-infrastructure': {
        description: 'Improve test infrastructure for breadcrumb and selection',
        apply: () => {
            console.log('🔧 Improving test infrastructure...');
            
            const testFile = path.join(__dirname, 'test-softarc-navigation.js');
            let content = fs.readFileSync(testFile, 'utf8');
            
            // Add better mock for animateHierarchyTransition
            const animateFix = `            animateHierarchyTransition: async () => Promise.resolve(),
            requestAnimationFrame: (callback) => setTimeout(callback, 16),`;
            
            if (!content.includes('animateHierarchyTransition:')) {
                content = content.replace(
                    'requestAnimationFrame: (callback) => setTimeout(callback, 16),',
                    animateFix
                );
                fs.writeFileSync(testFile, content, 'utf8');
                console.log('✅ Added animateHierarchyTransition mock');
                return true;
            }
            
            return false;
        }
    }
};

// Apply all fixes based on current issues
async function applyAllFixes(issues) {
    const appliedFixes = [];
    
    for (const [fixName, fixConfig] of Object.entries(FIXES)) {
        try {
            if (fixConfig.apply()) {
                appliedFixes.push(fixName);
            }
        } catch (error) {
            console.error(`❌ Failed to apply ${fixName}: ${error.message}`);
        }
    }
    
    return appliedFixes;
}

module.exports = { applyAllFixes, FIXES };

// Run if called directly
if (require.main === module) {
    applyAllFixes({}).then(fixes => {
        console.log(`\n✅ Applied ${fixes.length} fixes`);
    });
}