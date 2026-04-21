#!/usr/bin/env python3
import subprocess
import sys

result = subprocess.run(
    ["make", "verify"],
    cwd="/Users/mistlight/Projects/RalphWithReviewer/wt-77-fix-counting-/ralph-workflow",
    capture_output=True,
    text=True,
    timeout=600
)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
sys.exit(result.returncode)
