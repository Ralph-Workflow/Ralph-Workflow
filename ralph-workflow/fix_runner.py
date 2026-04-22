#!/usr/bin/env python3
import subprocess
# Restore runner.py from git commit d3154db93
git_show_cmd = ['git', '--git-dir=/Users/mistlight/Projects/RalphWithReviewer/wt-82-fail-recovery/ralph-workflow/.git', 'show', 'd3154db93:ralph-workflow/ralph/pipeline/runner.py']
result = subprocess.run(git_show_cmd, capture_output=True, text=True)
if result.returncode != 0:
    exit(1)
target_path = '/Users/mistlight/Projects/RalphWithReviewer/wt-82-fail-recovery/ralph-workflow/ralph/pipeline/runner.py'
with open(target_path, 'w') as f:
    f.write(result.stdout)
