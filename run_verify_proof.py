#!/usr/bin/env python3
"""Timed verify proof as specified in the plan."""
import subprocess
import time

start = time.perf_counter()
result = subprocess.run(['make', 'verify'], check=False, cwd='/Users/mistlight/Projects/RalphWithReviewer/wt-114-refactor/ralph-workflow')
elapsed = time.perf_counter() - start
print(f'ELAPSED={elapsed:.3f}')
print(f'EXIT_CODE={result.returncode}')
if result.returncode != 0 or elapsed > 30.0:
    raise SystemExit(result.returncode or 1)
print("VERIFICATION PASSED")