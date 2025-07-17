#!/usr/bin/env node

/**
 * Quick summary of all test files
 */

const { execSync } = require('child_process');

const tests = [
    'test-softarc-navigation.js',
    'test-all-scenarios.js',
    'test-complete-navigation.js',
    'test-real-world-bugs.js',
    'test-breadcrumb-animation.js',
    'test-navigation-edge-cases.js',
    'test-visual-visibility.js'
];

console.log('🧪 BeoSound 5c Test Suite Summary');
console.log('=' .repeat(60));

let totalPassed = 0;
let totalFailed = 0;

tests.forEach(test => {
    try {
        const output = execSync(`node ${test} 2>&1`, {
            cwd: __dirname,
            encoding: 'utf8',
            maxBuffer: 1024 * 1024 * 10
        });
        
        // Extract pass/fail counts
        const passMatch = output.match(/Passed: (\d+)/);
        const failMatch = output.match(/Failed: (\d+)/);
        
        const passed = passMatch ? parseInt(passMatch[1]) : 0;
        const failed = failMatch ? parseInt(failMatch[1]) : 0;
        const total = passed + failed;
        
        if (total > 0) {
            const percentage = ((passed / total) * 100).toFixed(0);
            const status = failed === 0 ? '✅' : '❌';
            console.log(`${status} ${test.padEnd(35)} ${passed}/${total} (${percentage}%)`);
            
            totalPassed += passed;
            totalFailed += failed;
        } else {
            console.log(`⚠️  ${test.padEnd(35)} No results`);
        }
    } catch (error) {
        console.log(`💥 ${test.padEnd(35)} CRASHED`);
        totalFailed += 1;
    }
});

console.log('=' .repeat(60));
const grandTotal = totalPassed + totalFailed;
const overallPercentage = grandTotal > 0 ? ((totalPassed / grandTotal) * 100).toFixed(1) : '0';
console.log(`📊 OVERALL: ${totalPassed}/${grandTotal} tests passing (${overallPercentage}%)`);

if (totalFailed === 0) {
    console.log('🎉 All tests passing!');
} else {
    console.log(`❌ ${totalFailed} tests still need fixes`);
}