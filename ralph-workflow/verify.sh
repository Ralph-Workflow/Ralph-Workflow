#!/bin/bash
# Verification script that should be run
cd /Users/mistlight/Projects/RalphWithReviewer/ralph-workflow
echo "=== Running verification ==="
echo ""
echo "1. Checking for 'Unknown failure' strings in production code..."
rg -n "Unknown failure" ralph/ || echo "None found (good!)"
echo ""
echo "2. Running lint..."
make lint
echo ""
echo "3. Running typecheck..."
make typecheck
echo ""
echo "4. Running tests..."
make test
echo ""
echo "=== Verification complete ==="
