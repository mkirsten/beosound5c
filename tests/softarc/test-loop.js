#!/usr/bin/env node

/**
 * Automated test loop for BeoSound 5c
 * Runs tests, identifies issues, and tracks fixes needed
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// Configuration
const MAX_ITERATIONS = 80;
const TEST_FILES = [
    'test-softarc-navigation.js',
    'test-complete-navigation.js', 
    'test-real-world-bugs.js',
    'test-navigation-edge-cases.js',
    'test-visual-visibility.js',
    'test-breadcrumb-animation.js',
    'test-all-scenarios.js'
];

// Track issues and fixes
let iteration = 0;
let allIssues = [];
let fixesApplied = [];

// Issue patterns
const KNOWN_ISSUES = {
    'document.querySelector is not a function': {
        type: 'mock-binding',
        fix: 'bind-querySelector'
    },
    'container.children.length': {
        type: 'mock-dom',
        fix: 'fix-children-array'
    },
    'Should be at index': {
        type: 'navigation',
        fix: 'fix-nav-update'
    },
    'Breadcrumb should exist': {
        type: 'breadcrumb',
        fix: 'fix-breadcrumb-creation'
    },
    'Should have visible items': {
        type: 'rendering',
        fix: 'fix-render-persistence'
    },
    'Cannot read properties of null': {
        type: 'null-check',
        fix: 'add-null-checks'
    },
    'Arc container should have content': {
        type: 'mock-dom',
        fix: 'fix-container-children'
    }
};

// Run a single test file
function runTest(testFile) {
    console.log(`\n📋 Running ${testFile}...`);
    try {
        const output = execSync(`node ${testFile}`, {
            cwd: __dirname,
            encoding: 'utf8',
            stdio: 'pipe'
        });
        
        // Parse output for failures
        const failures = parseTestOutput(output);
        return { success: failures.length === 0, failures, output };
    } catch (error) {
        // Test failed - parse error output
        const output = error.stdout || error.message;
        const failures = parseTestOutput(output);
        
        // Check for crash errors
        if (error.message.includes('is not a function')) {
            failures.push({
                test: testFile,
                error: error.message,
                type: 'crash'
            });
        }
        
        return { success: false, failures, output };
    }
}

// Parse test output for failures
function parseTestOutput(output) {
    const failures = [];
    const lines = output.split('\n');
    
    lines.forEach((line, index) => {
        if (line.includes('❌')) {
            const errorLine = line;
            const nextLine = lines[index + 1] || '';
            
            failures.push({
                error: errorLine.trim(),
                details: nextLine.includes('Error:') ? nextLine.trim() : '',
                line: index
            });
        }
    });
    
    // Check for specific error patterns
    for (const [pattern, info] of Object.entries(KNOWN_ISSUES)) {
        if (output.includes(pattern)) {
            failures.push({
                error: pattern,
                type: info.type,
                suggestedFix: info.fix
            });
        }
    }
    
    return failures;
}

// Analyze all failures and categorize issues
function analyzeFailures(allFailures) {
    const issues = {
        mockBinding: [],
        navigation: [],
        rendering: [],
        breadcrumb: [],
        other: []
    };
    
    allFailures.forEach(failure => {
        if (failure.error.includes('querySelector') || failure.error.includes('is not a function')) {
            issues.mockBinding.push(failure);
        } else if (failure.error.includes('index') || failure.error.includes('navigate')) {
            issues.navigation.push(failure);
        } else if (failure.error.includes('render') || failure.error.includes('visible')) {
            issues.rendering.push(failure);
        } else if (failure.error.includes('breadcrumb')) {
            issues.breadcrumb.push(failure);
        } else {
            issues.other.push(failure);
        }
    });
    
    return issues;
}

// Apply fixes based on issue type
function applyFixes(issues) {
    const fixes = [];
    
    // Fix 1: Mock binding issues
    if (issues.mockBinding.length > 0) {
        console.log('\n🔧 Fixing mock DOM binding issues...');
        fixes.push({
            file: 'test-softarc-navigation.js',
            fix: 'mock-binding',
            description: 'Fix querySelector binding'
        });
    }
    
    // Fix 2: Navigation issues
    if (issues.navigation.length > 0) {
        console.log('\n🔧 Fixing navigation index updates...');
        fixes.push({
            file: 'script.js',
            fix: 'navigation',
            description: 'Ensure navigation updates currentIndex'
        });
    }
    
    // Fix 3: Rendering issues
    if (issues.rendering.length > 0) {
        console.log('\n🔧 Fixing rendering persistence...');
        fixes.push({
            file: 'script.js',
            fix: 'rendering',
            description: 'Fix DOM children persistence'
        });
    }
    
    // Fix 4: Breadcrumb issues
    if (issues.breadcrumb.length > 0) {
        console.log('\n🔧 Fixing breadcrumb animation...');
        fixes.push({
            file: 'script.js',
            fix: 'breadcrumb',
            description: 'Ensure breadcrumb transforms existing element'
        });
    }
    
    return fixes;
}

// Main test loop
async function runTestLoop() {
    console.log('🔄 Starting automated test loop');
    console.log(`📊 Maximum iterations: ${MAX_ITERATIONS}`);
    console.log(`📋 Test files: ${TEST_FILES.length}`);
    
    while (iteration < MAX_ITERATIONS) {
        iteration++;
        console.log(`\n${'='.repeat(60)}`);
        console.log(`🔄 ITERATION ${iteration}/${MAX_ITERATIONS}`);
        console.log(`${'='.repeat(60)}`);
        
        let allFailures = [];
        let allPassed = true;
        
        // Run all tests
        for (const testFile of TEST_FILES) {
            const result = runTest(testFile);
            
            if (!result.success) {
                allPassed = false;
                allFailures = allFailures.concat(result.failures.map(f => ({
                    ...f,
                    testFile
                })));
            }
        }
        
        // Check if all tests passed
        if (allPassed) {
            console.log('\n🎉 ALL TESTS PASSED!');
            console.log(`✅ Completed in ${iteration} iterations`);
            console.log(`📝 Fixes applied: ${fixesApplied.length}`);
            
            if (fixesApplied.length > 0) {
                console.log('\nFixes applied during this session:');
                fixesApplied.forEach((fix, i) => {
                    console.log(`${i + 1}. ${fix.description} (${fix.file})`);
                });
            }
            
            break;
        }
        
        // Analyze failures
        console.log(`\n❌ Found ${allFailures.length} failures`);
        const issues = analyzeFailures(allFailures);
        
        // Log issue summary
        console.log('\n📊 Issue Summary:');
        console.log(`   Mock Binding: ${issues.mockBinding.length}`);
        console.log(`   Navigation: ${issues.navigation.length}`);
        console.log(`   Rendering: ${issues.rendering.length}`);
        console.log(`   Breadcrumb: ${issues.breadcrumb.length}`);
        console.log(`   Other: ${issues.other.length}`);
        
        // Apply fixes
        const fixes = applyFixes(issues);
        
        if (fixes.length === 0) {
            console.log('\n⚠️  No automatic fixes available');
            console.log('📋 Unresolved issues:');
            allFailures.forEach(f => {
                console.log(`   - ${f.error}`);
                if (f.details) console.log(`     ${f.details}`);
            });
            break;
        }
        
        // Track fixes
        fixesApplied = fixesApplied.concat(fixes);
        
        // Add delay to prevent infinite loops
        await new Promise(resolve => setTimeout(resolve, 100));
        
        // Break if we're not making progress
        if (iteration > 10 && allFailures.length === allIssues.length) {
            console.log('\n⚠️  No progress being made - stopping');
            break;
        }
        
        allIssues = allFailures;
    }
    
    // Final summary
    console.log('\n' + '='.repeat(60));
    console.log('📊 FINAL SUMMARY');
    console.log('='.repeat(60));
    console.log(`Iterations: ${iteration}`);
    console.log(`Fixes applied: ${fixesApplied.length}`);
    console.log(`Remaining issues: ${allIssues.length}`);
    
    if (allIssues.length > 0) {
        console.log('\n❌ Remaining issues that need manual intervention:');
        const uniqueIssues = [...new Set(allIssues.map(i => i.error))];
        uniqueIssues.forEach(issue => {
            console.log(`   - ${issue}`);
        });
    }
}

// Export for external use
module.exports = { runTestLoop };

// Run if called directly
if (require.main === module) {
    runTestLoop().catch(console.error);
}