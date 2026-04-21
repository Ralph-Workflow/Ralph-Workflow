#!/usr/bin/env python3
import subprocess
import sys

# Run pytest on reducer tests
result = subprocess.run(
    ["uv", "run", "pytest", "tests/test_reducer.py", "-x", "-q"],
    cwd="/Users/mistlight/Projects/RalphWithReviewer/wt-77-fix-counting-/ralph-workflow",
    capture_output=True,
    text=True,
    timeout=300
)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
sys.exit(result.returncode)
