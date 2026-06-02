# Ralph Workflow Live Demo Outputs

Real terminal captures from running Ralph Workflow v0.8.8 on a fresh project.
Every file in this directory was captured from actual runs — no mock-ups, no edited output.

## Files

- **`init-output.txt`** — Full `ralph --init` output including the banner, baseline capabilities table, and first-run setup message
- **`diagnose-output.txt`** — Full `ralph --diagnose` output: git status, agent inventory, config validation, MCP server inventory, workspace files, pre-flight check
- **`dry-run-output.txt`** — `ralph --dry-run` showing pipeline phase progression and agent chain selection (if available)

## Source Commit

These outputs were generated from commit `[latest]` of the Ralph Workflow source.
To reproduce: install `ralph-workflow` from PyPI, create a new git repo, and follow START_HERE.md.
