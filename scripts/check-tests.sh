#!/bin/bash
# Pre-commit check for integration test anti-patterns
# Run this manually: bash scripts/check-tests.sh
# Or integrate into your CI pipeline

set -e

echo "=== Checking for test anti-patterns in staged changes ==="
echo ""

# Check staged src/ unit test files for #[serial] (also BANNED there)
STAGED_SRC_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep "^ralph-workflow/src/.*\.rs$" || true)
if [ -n "$STAGED_SRC_FILES" ]; then
    SRC_SERIAL_COUNT=0
    for file in $STAGED_SRC_FILES; do
        if [ ! -f "$file" ]; then
            continue
        fi
        if git diff --cached "$file" | grep "^\+" | grep -v "^+\s*//" | grep -q "#\[serial\]"; then
            echo "❌ $file: Uses #[serial] in src/ unit tests - this is BANNED"
            echo "   Fix: use MemoryConfigEnvironment::with_env_var() or an injectable Fn(&str)->Option<String>"
            echo "   instead of mutating the real process environment."
            SRC_SERIAL_COUNT=$((SRC_SERIAL_COUNT + 1))
        fi
    done
    if [ "$SRC_SERIAL_COUNT" -gt 0 ]; then
        echo ""
        echo "❌ Found $SRC_SERIAL_COUNT #[serial] violation(s) in src/ unit tests"
        exit 1
    fi
fi

# Get staged integration test files
STAGED_TEST_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep "^tests/integration_tests/.*\.rs$" || true)

if [ -z "$STAGED_TEST_FILES" ]; then
    echo "✓ No integration test files staged for commit"
    exit 0
fi

file_count=$(printf '%s\n' "$STAGED_TEST_FILES" | wc -l | xargs)
echo "Checking $file_count staged test file(s)..."
echo ""

ERROR_COUNT=0

# Check each staged file for anti-patterns
for file in $STAGED_TEST_FILES; do
    if [ ! -f "$file" ]; then
        continue
    fi
    
    # Check for std::fs usage
    if git diff --cached "$file" | grep -q "^\+.*std::fs::"; then
        echo "❌ $file: Uses std::fs - use MemoryWorkspace instead"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
    
    # Check for TempDir usage
    if git diff --cached "$file" | grep -q "^\+.*TempDir"; then
        echo "❌ $file: Uses TempDir - use MemoryWorkspace instead"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
    
    # Check for real process execution
    if git diff --cached "$file" | grep -q "^\+.*std::process::Command"; then
        echo "❌ $file: Uses std::process::Command - use MockProcessExecutor instead"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
    
    # Check for cfg!(test) in non-comment lines
    if git diff --cached "$file" | grep "^\+" | grep -v "^+\s*//" | grep -q "cfg!(test)"; then
        echo "❌ $file: Uses cfg!(test) in code - use dependency injection instead"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
    
    # Check for #[serial] in integration tests (BANNED - design smell)
    if git diff --cached "$file" | grep "^\+" | grep -v "^+\s*//" | grep -q "#\[serial\]\|use serial_test"; then
        echo "❌ $file: Uses #[serial] - this is BANNED in integration tests"
        echo "   Fix: use dependency injection (env-injection pattern) to eliminate global state coupling"
        echo "   See docs/agents/testing-guide.md for the env-injection pattern"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi

    # Check for std::env::set_var/remove_var (requires serialization, banned)
    if git diff --cached "$file" | grep "^\+" | grep -v "^+\s*//" | grep -q "env::set_var\|env::remove_var"; then
        echo "❌ $file: Uses env::set_var/remove_var - use env-injection pattern instead"
        echo "   Direct env mutation races with parallel tests."
        echo "   See docs/agents/testing-guide.md 'Env-Injection Pattern'"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi

    # Check file size (after commit)
    LINE_COUNT=$(wc -l < "$file" 2>/dev/null || echo 0)
    if [ "$LINE_COUNT" -gt 1000 ]; then
        echo "⚠️  $file: $LINE_COUNT lines (should be < 1000) - consider splitting"
        # Don't increment error count, just warn
    fi
done

echo ""

if [ $ERROR_COUNT -gt 0 ]; then
    echo "❌ Found $ERROR_COUNT test anti-pattern(s)"
    echo ""
    echo "Integration tests must follow guidelines in docs/agents/testing-guide.md"
    echo "Run 'cargo xtask verify' for full audit"
    exit 1
else
    echo "✓ All staged test files comply with integration testing guidelines"
    exit 0
fi
