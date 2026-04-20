#!/usr/bin/env python3
"""Read a section of runner.py"""
import sys
path = sys.argv[1] if len(sys.argv) > 1 else "/Users/mistlight/Projects/RalphWithReviewer/ralph-workflow/ralph/pipeline/runner.py"
start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
end = int(sys.argv[3]) if len(sys.argv) > 3 else start + 500

with open(path) as f:
    lines = f.readlines()

for i, line in enumerate(lines[start:end], start=start+1):
    print(f"{i}: {line}", end="")