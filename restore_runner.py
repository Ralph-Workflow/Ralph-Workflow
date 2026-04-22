#!/usr/bin/env python3
"""Restore runner.py from git and fix the test files."""
import subprocess
import sys

# Restore runner.py from HEAD
result = subprocess.run(
    ['git', 'show', 'HEAD:ralph-workflow/ralph/pipeline/runner.py'],
    capture_output=True, text=True,
    cwd='/Users/mistlight/Projects/RalphWithReviewer/wt-82-fail-recovery'
)
if result.returncode != 0:
    print('Failed to get runner.py from git:', result.stderr, file=sys.stderr)
    sys.exit(1)

with open('/Users/mistlight/Projects/RalphWithReviewer/wt-82-fail-recovery/ralph-workflow/ralph/pipeline/runner.py', 'w') as f:
    f.write(result.stdout)

print('Restored runner.py successfully')
print(f'File has {len(result.stdout.splitlines())} lines')
