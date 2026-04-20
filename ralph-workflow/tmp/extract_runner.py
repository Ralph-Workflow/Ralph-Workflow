#!/usr/bin/env python3
"""Extract a section of runner.py to a file we can read."""
import sys

path = "/Users/mistlight/Projects/RalphWithReviewer/ralph-workflow/ralph/pipeline/runner.py"
start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
end = int(sys.argv[2]) if len(sys.argv) > 2 else start + 200

with open(path) as f:
    lines = f.readlines()

output_path = "/Users/mistlight/Projects/RalphWithReviewer/ralph-workflow/tmp/runner_section.txt"
with open(output_path, "w") as f:
    for i, line in enumerate(lines[start:end], start=start+1):
        f.write(f"{i}: {line}")

print(f"Wrote lines {start+1}-{end} to {output_path}")