#!/usr/bin/env node

/**
 * Debug script to understand breadcrumb issues
 */

const fs = require('fs');
const path = require('path');

// Load the actual script.js file
const scriptPath = path.join(__dirname, '../../web/softarc/script.js');
const scriptContent = fs.readFileSync(scriptPath, 'utf8');

// Check what's in performEnhancedChildTransition
console.log('🔍 Checking performEnhancedChildTransition implementation...\n');

// Extract the method
const methodMatch = scriptContent.match(/performEnhancedChildTransition\s*\([^)]*\)\s*{[\s\S]*?^    }/m);
if (methodMatch) {
    console.log('Found performEnhancedChildTransition:');
    console.log(methodMatch[0].substring(0, 500) + '...');
} else {
    console.log('❌ Could not find performEnhancedChildTransition method');
}

// Check breadcrumb class usage
console.log('\n🔍 Checking breadcrumb class usage...\n');
const breadcrumbMatches = scriptContent.match(/\.breadcrumb|breadcrumb'/g);
if (breadcrumbMatches) {
    console.log(`Found ${breadcrumbMatches.length} references to breadcrumb class`);
    breadcrumbMatches.forEach((match, i) => {
        if (i < 5) console.log(`  - ${match}`);
    });
} else {
    console.log('❌ No references to breadcrumb class found');
}

// Check renderChildItems
console.log('\n🔍 Checking renderChildItems implementation...\n');
const renderChildMatch = scriptContent.match(/renderChildItems\s*\([^)]*\)\s*{[\s\S]*?^    }/m);
if (renderChildMatch) {
    console.log('Found renderChildItems:');
    console.log(renderChildMatch[0].substring(0, 500) + '...');
} else {
    console.log('❌ Could not find renderChildItems method');
}

// Check for duplicate method definitions
console.log('\n🔍 Checking for duplicate methods...\n');
const methodNames = ['render', 'renderChildItems', 'performEnhancedChildTransition', 'enterChildView'];
methodNames.forEach(name => {
    const regex = new RegExp(`${name}\\s*\\([^)]*\\)\\s*{`, 'g');
    const matches = scriptContent.match(regex);
    if (matches) {
        console.log(`${name}: ${matches.length} definition(s)`);
        if (matches.length > 1) {
            console.log(`  ⚠️  WARNING: Multiple definitions found!`);
        }
    }
});