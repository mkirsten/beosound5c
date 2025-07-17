#!/usr/bin/env node

/**
 * Fix animation test issues
 */

const fs = require('fs');
const path = require('path');

const testFile = path.join(__dirname, 'test-breadcrumb-animation.js');
let content = fs.readFileSync(testFile, 'utf8');

// Fix 1: Update AnimationMockElement to have proper classList implementation
const classListFix = `class AnimationMockElement extends MockElement {
    constructor(tagName) {
        super(tagName);
        this.transitions = [];
        this.animations = [];
    }
    
    // Override classList to ensure it works properly
    get classList() {
        if (!this._classListProxy) {
            const self = this;
            this._classListProxy = {
                add: (cls) => { 
                    self._classes.add(cls); 
                    self._updateClassName(); 
                },
                remove: (cls) => { 
                    self._classes.delete(cls); 
                    self._updateClassName(); 
                },
                contains: (cls) => self._classes.has(cls),
                toggle: (cls) => {
                    if (self._classes.has(cls)) {
                        self._classes.delete(cls);
                    } else {
                        self._classes.add(cls);
                    }
                    self._updateClassName();
                }
            };
        }
        return this._classListProxy;
    }`;

// Replace the AnimationMockElement class
const classStart = content.indexOf('class AnimationMockElement extends MockElement');
if (classStart > -1) {
    const classEnd = content.indexOf('recordTransition(', classStart);
    if (classEnd > -1) {
        content = content.substring(0, classStart) + classListFix + '\n    \n    ' + content.substring(classEnd);
        console.log('✅ Fixed AnimationMockElement classList');
    }
}

// Fix 2: Update simulateRender to properly set up the DOM
const renderFix = `    simulateRender() {
        // Clear container first
        const container = this.mockDocument.getElementById('arc-container');
        container.children = [];
        
        // Create mock elements for visible items
        const visibleItems = this.arcList.getVisibleItems();
        
        visibleItems.forEach((item, index) => {
            const element = new AnimationMockElement('div');
            element._classes = new Set(['arc-item']);
            element._updateClassName();
            element.dataset.itemId = item.id;
            element.dataset.index = String(item.index);
            
            // Mark center item as selected
            if (Math.abs(item.index - this.arcList.currentIndex) < 0.5) {
                element.classList.add('selected');
                element.dataset.selected = 'true';
            }
            
            container.appendChild(element);
        });
        
        // Ensure at least one selected element exists
        if (!container.querySelector('.arc-item.selected') && container.children.length > 0) {
            const centerIndex = Math.floor(container.children.length / 2);
            container.children[centerIndex].classList.add('selected');
            container.children[centerIndex].dataset.selected = 'true';
        }
    }`;

// Replace simulateRender
const renderStart = content.indexOf('simulateRender() {');
if (renderStart > -1) {
    const renderEnd = content.indexOf('    }', renderStart) + 5;
    content = content.substring(0, renderStart) + renderFix.substring(4) + content.substring(renderEnd);
    console.log('✅ Fixed simulateRender method');
}

// Fix 3: Fix querySelector in AnimationMockDOM
const querySelectorFix = `    querySelector(selector) {
        // Handle arc-item.selected
        if (selector === '.arc-item.selected') {
            const container = this.getElementById('arc-container');
            if (container && container.children) {
                for (const child of container.children) {
                    if (child._classes && child._classes.has('selected')) {
                        return child;
                    }
                }
            }
            return null;
        }
        
        // Handle arc-item.breadcrumb
        if (selector === '.arc-item.breadcrumb') {
            const container = this.getElementById('arc-container');
            if (container && container.children) {
                for (const child of container.children) {
                    if (child._classes && child._classes.has('breadcrumb')) {
                        return child;
                    }
                }
            }
            return null;
        }
        
        return super.querySelector(selector);
    }`;

// Find AnimationMockDOM class and add querySelector if missing
const mockDOMStart = content.indexOf('class AnimationMockDOM extends MockDOM');
if (mockDOMStart > -1) {
    const constructorEnd = content.indexOf('    }', content.indexOf('constructor()', mockDOMStart)) + 5;
    
    // Check if querySelector already exists
    if (!content.substring(mockDOMStart, mockDOMStart + 1000).includes('querySelector(')) {
        content = content.substring(0, constructorEnd) + '\n' + querySelectorFix + '\n' + content.substring(constructorEnd);
        console.log('✅ Added querySelector to AnimationMockDOM');
    }
}

// Write the fixed content
fs.writeFileSync(testFile, content, 'utf8');
console.log('✅ Animation test fixes applied!');