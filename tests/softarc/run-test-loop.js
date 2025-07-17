#!/usr/bin/env node

/**
 * Main test loop runner
 * Automatically runs tests, applies fixes, and verifies
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const { applyAllFixes } = require('./auto-fix');

const MAX_ITERATIONS = 80;
let iteration = 0;
let totalFixesApplied = 0;
let testResults = [];

// Test files to run
const TEST_SEQUENCE = [
    { file: 'test-softarc-navigation.js', critical: true },
    { file: 'test-all-scenarios.js', critical: true },
    { file: 'test-complete-navigation.js', critical: true },
    { file: 'test-real-world-bugs.js', critical: true },
    { file: 'test-breadcrumb-animation.js', critical: true },
    { file: 'test-navigation-edge-cases.js', critical: false },
    { file: 'test-visual-visibility.js', critical: false }
];

// Run a single test and capture results
function runSingleTest(testFile) {
    try {
        console.log(`\n🧪 Running ${testFile}...`);
        const output = execSync(`node ${testFile} 2>&1`, {
            cwd: __dirname,
            encoding: 'utf8',
            maxBuffer: 1024 * 1024 * 10 // 10MB buffer
        });
        
        // Parse results
        const passed = output.includes('All') && output.includes('passed');
        const failureCount = (output.match(/❌/g) || []).length;
        const successCount = (output.match(/✅/g) || []).length;
        
        return {
            file: testFile,
            passed: passed && failureCount === 0,
            output,
            stats: { passed: successCount, failed: failureCount }
        };
    } catch (error) {
        // Test crashed or failed
        const output = error.stdout || error.stderr || error.message;
        return {
            file: testFile,
            passed: false,
            output,
            error: error.message,
            stats: { passed: 0, failed: 1 }
        };
    }
}

// Analyze test failures
function analyzeFailures(results) {
    const issues = {
        querySelector: false,
        navigation: false,
        rendering: false,
        breadcrumb: false,
        container: false,
        other: []
    };
    
    results.forEach(result => {
        if (!result.passed) {
            if (result.output.includes('querySelector is not a function')) {
                issues.querySelector = true;
            }
            if (result.output.includes('Should be at index') || 
                result.output.includes('getCurrentIndex')) {
                issues.navigation = true;
            }
            if (result.output.includes('Should have visible items') ||
                result.output.includes('render')) {
                issues.rendering = true;
            }
            if (result.output.includes('breadcrumb') || 
                result.output.includes('Breadcrumb')) {
                issues.breadcrumb = true;
            }
            if (result.output.includes('Arc container should have content')) {
                issues.container = true;
            }
            
            // Extract specific error messages
            const errorMatches = result.output.match(/❌ (.+)/g) || [];
            errorMatches.forEach(match => {
                if (!issues.other.includes(match)) {
                    issues.other.push(match);
                }
            });
        }
    });
    
    return issues;
}

// Apply targeted fixes
async function applyTargetedFixes(issues) {
    console.log('\n🔧 Applying fixes based on issues...');
    
    const fixesToApply = [];
    
    if (issues.querySelector) {
        fixesToApply.push('mock-binding');
    }
    if (issues.navigation) {
        fixesToApply.push('navigation');
    }
    if (issues.rendering) {
        fixesToApply.push('rendering');
    }
    if (issues.breadcrumb) {
        fixesToApply.push('breadcrumb');
    }
    if (issues.container) {
        fixesToApply.push('container-children');
    }
    
    // Apply fixes
    for (const fix of fixesToApply) {
        console.log(`   Applying ${fix}...`);
        try {
            execSync(`node auto-fix.js ${fix}`, { cwd: __dirname });
            totalFixesApplied++;
        } catch (error) {
            console.error(`   ❌ Failed to apply ${fix}`);
        }
    }
    
    return fixesToApply.length;
}

// Main test loop
async function runTestLoop() {
    console.log('🔄 BeoSound 5c Automated Test & Fix Loop');
    console.log('=' .repeat(60));
    console.log(`📊 Maximum iterations: ${MAX_ITERATIONS}`);
    console.log(`📋 Test files: ${TEST_SEQUENCE.length}`);
    console.log('=' .repeat(60));
    
    // Apply initial fixes
    console.log('\n🔧 Applying initial fixes...');
    await applyAllFixes({});
    
    while (iteration < MAX_ITERATIONS) {
        iteration++;
        console.log(`\n${'='.repeat(60)}`);
        console.log(`🔄 ITERATION ${iteration}/${MAX_ITERATIONS}`);
        console.log(`${'='.repeat(60)}`);
        
        testResults = [];
        let allPassed = true;
        let criticalPassed = true;
        
        // Run all tests
        for (const test of TEST_SEQUENCE) {
            const result = runSingleTest(test.file);
            testResults.push(result);
            
            if (!result.passed) {
                allPassed = false;
                if (test.critical) {
                    criticalPassed = false;
                }
            }
            
            // Show quick status
            console.log(`   ${result.passed ? '✅' : '❌'} ${test.file} (${result.stats.passed}/${result.stats.passed + result.stats.failed})`);
        }
        
        // Check if we're done
        if (allPassed) {
            console.log('\n🎉 ALL TESTS PASSED!');
            break;
        }
        
        if (criticalPassed) {
            console.log('\n✅ All critical tests passed!');
            console.log('⚠️  Some non-critical tests still failing');
        }
        
        // Analyze failures
        const issues = analyzeFailures(testResults);
        console.log('\n📊 Issues found:');
        console.log(`   querySelector: ${issues.querySelector ? '❌' : '✅'}`);
        console.log(`   navigation: ${issues.navigation ? '❌' : '✅'}`);
        console.log(`   rendering: ${issues.rendering ? '❌' : '✅'}`);
        console.log(`   breadcrumb: ${issues.breadcrumb ? '❌' : '✅'}`);
        console.log(`   container: ${issues.container ? '❌' : '✅'}`);
        
        if (issues.other.length > 0) {
            console.log('\n   Other issues:');
            issues.other.slice(0, 5).forEach(issue => {
                console.log(`   ${issue}`);
            });
        }
        
        // Apply fixes
        const fixCount = await applyTargetedFixes(issues);
        
        if (fixCount === 0) {
            console.log('\n⚠️  No automatic fixes available');
            break;
        }
        
        // Short delay
        await new Promise(resolve => setTimeout(resolve, 500));
    }
    
    // Final summary
    console.log('\n' + '='.repeat(60));
    console.log('📊 FINAL SUMMARY');
    console.log('=' .repeat(60));
    console.log(`Iterations: ${iteration}`);
    console.log(`Total fixes applied: ${totalFixesApplied}`);
    console.log('\nTest Results:');
    
    testResults.forEach(result => {
        const status = result.passed ? '✅' : '❌';
        const stats = `${result.stats.passed}/${result.stats.passed + result.stats.failed}`;
        console.log(`   ${status} ${result.file} (${stats})`);
    });
    
    const failedTests = testResults.filter(r => !r.passed);
    if (failedTests.length > 0) {
        console.log('\n❌ Failed tests need manual intervention:');
        failedTests.forEach(test => {
            console.log(`\n   ${test.file}:`);
            // Extract first few errors
            const errors = test.output.match(/❌ (.+)/g) || [];
            errors.slice(0, 3).forEach(err => console.log(`      ${err}`));
        });
    }
    
    return iteration < MAX_ITERATIONS && failedTests.length === 0;
}

// Run the loop
if (require.main === module) {
    runTestLoop().then(success => {
        process.exit(success ? 0 : 1);
    }).catch(error => {
        console.error('🔥 Fatal error:', error);
        process.exit(1);
    });
}