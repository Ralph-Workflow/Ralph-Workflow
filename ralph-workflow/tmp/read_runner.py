#!/usr/bin/env python3
"""Extract runner.py content for analysis."""
import sys

path = "/Users/mistlight/Projects/RalphWithReviewer/ralph-workflow/ralph/pipeline/runner.py"
with open(path) as f:
    content = f.read()

# Write full content to tmp
with open("/Users/mistlight/Projects/RalphWithReviewer/ralph-workflow/tmp/runner_full.py", "w") as f:
    f.write(content)

print(f"Runner.py is {len(content)} characters, {len(content.splitlines())} lines")