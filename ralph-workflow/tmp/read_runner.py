#!/usr/bin/env python3
"""Extract the run function and surrounding context from runner.py."""
path = '/Users/mistlight/Projects/RalphWithReviewer/wt-82-fail-recovery/ralph-workflow/ralph/pipeline/runner.py'
with open(path, 'r') as f:
    content = f.read()

# Find the run function
run_start = content.find('\ndef run(')
if run_start == -1:
    run_start = content.find('\nasync def run(')

# Find the next function definition after run
next_func = content.find('\ndef ', run_start + 10)
if next_func == -1:
    next_func = content.find('\nasync def ', run_start + 10)

run_code = content[run_start:next_func]

# Print first 3000 chars of run function
print(run_code[:3000])
print("\n...[truncated]...")
