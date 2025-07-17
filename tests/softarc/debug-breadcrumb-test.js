#!/usr/bin/env node

const { execSync } = require('child_process');

// Run test and check breadcrumb details
console.log('🔍 Debugging breadcrumb issue...\n');

const output = execSync(`node test-all-scenarios.js 2>&1 | grep -A20 -B20 "Created breadcrumb"`, {
    encoding: 'utf8',
    cwd: __dirname
}).toString();

console.log('Test output around breadcrumb creation:');
console.log(output);

// Also check what happens after
console.log('\n\n🔍 Checking container state after breadcrumb creation...\n');

const containerCheck = execSync(`node test-all-scenarios.js 2>&1 | grep -A5 "container children"`, {
    encoding: 'utf8', 
    cwd: __dirname
}).toString();

console.log(containerCheck);